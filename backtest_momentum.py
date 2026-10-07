"""
업비트 알트코인 모멘텀 전략 백테스트
=====================================

전략
  - 매주 같은 요일 09:00(업비트 일봉 시작)에 리밸런싱
  - 대상: 업비트 원화마켓 알트코인 (BTC, 스테이블코인 제외)
  - 유니버스(둘 중 선택, 기본은 둘 다 실행)
      volume : 30일 평균 원화 거래대금 상위 30개 (2021년부터 장기 검증용, 시총 대용치)
      mcap   : 코인게코 시가총액 3조원 이상 (무료 API 한도로 최근 365일만 가능)
  - 룩백 수익률 상위 5개를 동일비중 매수, 1주 보유
  - BTC 추세 필터: BTC 종가가 N일 이동평균 아래면 그 주는 현금 보유
  - 손절: 진입가 대비 -SL% 도달 시 전량 매도
  - 익절: 진입가 대비 +TP% 도달 시 보유량의 50% 매도 (남은 물량은 손절 유지)

사용법 (Windows 명령 프롬프트 또는 PowerShell)
  pip install requests pandas numpy
  python backtest_momentum.py

  처음 실행 시 데이터 수집에 15분에서 30분 정도 걸립니다. (data 폴더에 저장되어 재실행은 빠름)
  코인게코 Demo API 키가 있으면 더 빠릅니다:  python backtest_momentum.py --cg-key 발급받은키

결과
  results/summary.txt      : 핵심 요약 (이 파일을 Claude에게 올려주세요)
  results/grid_volume.csv  : 전체 파라미터 조합 결과 (거래대금 유니버스)
  results/grid_mcap.csv    : 전체 파라미터 조합 결과 (시총 유니버스)
"""

import argparse
import itertools
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
TRADE_START = date(2021, 1, 1)      # 백테스트 시작일
WARMUP_DAYS = 230                   # 이동평균 200일 계산용 사전 데이터
REBAL_WEEKDAY = 0                   # 리밸런싱 요일 (0=월요일)
TOP_K = 5                           # 보유 종목 수

VOLUME_TOP_N = 30                   # 거래대금 유니버스 크기
VOLUME_WINDOW = 30                  # 거래대금 평균 기간(일)
MIN_HISTORY = 60                    # 상장 후 최소 경과일
MCAP_THRESHOLD = 3e12               # 시총 하한 3조원

FEE = 0.0005                        # 업비트 원화마켓 수수료 0.05%
SLIPPAGE = 0.001                    # 일반 매매 슬리피지 가정 0.1%
STOP_SLIPPAGE = 0.003               # 손절 체결 슬리피지 가정 0.3%
TP_FRACTION = 0.5                   # 익절 시 매도 비중

# 파라미터 그리드
LOOKBACKS = ["7", "14", "21", "28", "mix7_28"]   # mix7_28: 7일과 28일 수익률 평균
BTC_FILTERS = [0, 20, 50, 100, 200]              # 0 = 필터 없음, 그 외 = BTC N일 이동평균
STOP_LOSSES = [0.0, 0.05, 0.10, 0.15, 0.20]      # 0 = 손절 없음
TAKE_PROFITS = [0.0, 0.10, 0.20, 0.30]           # 0 = 익절 없음
ABS_MOMENTUM = [False, True]                     # True = 룩백 수익률이 플러스인 코인만 편입

# 원안 (사용자 최초 설정)
ORIGINAL = dict(lookback="7", btc_ma=0, sl=0.10, tp=0.10, abs_mom=False)

STABLES = {"USDT", "USDC", "USDS", "USDE", "DAI", "TUSD", "FDUSD", "PYUSD",
           "USD1", "RLUSD", "BUSD", "USDP", "GUSD", "EURC", "KRWT"}

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
RES_DIR = os.path.join(BASE, "results")
UPBIT = "https://api.upbit.com/v1"
CG = "https://api.coingecko.com/api/v3"


# ---------------------------------------------------------------------------
# 데이터 수집
# ---------------------------------------------------------------------------
def _get(url, params=None, headers=None, sleep=0.12, tries=6):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=20)
            if r.status_code == 429:
                time.sleep(2 + 3 * i)
                continue
            r.raise_for_status()
            time.sleep(sleep)
            return r.json()
        except requests.RequestException as e:
            if i == tries - 1:
                raise
            print(f"   재시도 {i + 1}: {e}")
            time.sleep(2 + 2 * i)
    return None


def upbit_markets():
    data = _get(f"{UPBIT}/market/all", params={"isDetails": "true"})
    rows = []
    for m in data:
        if not m["market"].startswith("KRW-"):
            continue
        sym = m["market"].split("-")[1]
        rows.append({"market": m["market"], "symbol": sym,
                     "english_name": m.get("english_name", "")})
    return pd.DataFrame(rows)


def upbit_daily(market, start):
    """업비트 일봉. 날짜는 KST 09:00 시작 기준."""
    out, to = [], None
    while True:
        params = {"market": market, "count": 200}
        if to:
            params["to"] = to
        rows = _get(f"{UPBIT}/candles/days", params=params)
        if not rows:
            break
        out.extend(rows)
        oldest = rows[-1]
        if len(rows) < 200 or oldest["candle_date_time_kst"][:10] <= start.isoformat():
            break
        to = oldest["candle_date_time_utc"].replace("T", " ")
    if not out:
        return None
    df = pd.DataFrame(out)
    df["date"] = pd.to_datetime(df["candle_date_time_kst"].str[:10])
    df = df.rename(columns={"opening_price": "open", "high_price": "high",
                            "low_price": "low", "trade_price": "close",
                            "candle_acc_trade_price": "value"})
    df = df[["date", "open", "high", "low", "close", "value"]]
    df = df.drop_duplicates("date").sort_values("date")
    return df[df["date"] >= pd.Timestamp(start)]


def load_upbit(refresh=False):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "upbit_daily.csv")
    if os.path.exists(path) and not refresh:
        print("업비트 일봉: 저장된 데이터 사용")
        return pd.read_csv(path, parse_dates=["date"])
    start = TRADE_START - timedelta(days=WARMUP_DAYS)
    mk = upbit_markets()
    print(f"업비트 원화마켓 {len(mk)}개 일봉 수집 시작 (5분에서 10분 소요)")
    frames = []
    for i, row in enumerate(mk.itertuples(), 1):
        try:
            df = upbit_daily(row.market, start)
        except Exception as e:
            print(f"  {row.market} 실패: {e}")
            continue
        if df is not None and len(df):
            df["symbol"] = row.symbol
            frames.append(df)
        if i % 20 == 0:
            print(f"  {i}/{len(mk)} 완료")
    all_df = pd.concat(frames, ignore_index=True)
    all_df.to_csv(path, index=False)
    mk.to_csv(os.path.join(DATA_DIR, "upbit_markets.csv"), index=False, encoding="utf-8-sig")
    return all_df


def load_mcap(symbols, cg_key=None, refresh=False):
    """코인게코 일별 시가총액(KRW). 무료 API는 최근 365일까지만 제공."""
    path = os.path.join(DATA_DIR, "coingecko_mcap.csv")
    if os.path.exists(path) and not refresh:
        print("코인게코 시총: 저장된 데이터 사용")
        return pd.read_csv(path, parse_dates=["date"])
    headers = {"x-cg-demo-api-key": cg_key} if cg_key else None
    sleep = 2.2 if cg_key else 6.5

    # 심볼 -> 코인게코 id 매핑 (같은 심볼이 여럿이면 시총이 가장 큰 코인)
    print("코인게코 코인 목록 조회")
    sym2id, cur_mcap = {}, {}
    for page in (1, 2, 3, 4):
        rows = _get(f"{CG}/coins/markets",
                    params={"vs_currency": "krw", "order": "market_cap_desc",
                            "per_page": 250, "page": page},
                    headers=headers, sleep=sleep)
        for c in rows or []:
            s = c["symbol"].upper()
            if s not in sym2id:
                sym2id[s] = c["id"]
                cur_mcap[s] = c.get("market_cap") or 0

    # 현재 시총이 너무 작은 코인은 지난 1년간 3조원을 넘었을 가능성이 낮아 제외 (호출 수 절감)
    targets = [s for s in symbols if s in sym2id and cur_mcap.get(s, 0) >= 3e11]
    print(f"코인게코 시총 수집 대상 {len(targets)}개 (코인당 {sleep:.0f}초 정도)")
    frames = []
    for i, s in enumerate(targets, 1):
        try:
            d = _get(f"{CG}/coins/{sym2id[s]}/market_chart",
                     params={"vs_currency": "krw", "days": 365, "interval": "daily"},
                     headers=headers, sleep=sleep)
        except Exception as e:
            print(f"  {s} 실패: {e}")
            continue
        mc = pd.DataFrame(d.get("market_caps", []), columns=["ts", "mcap"])
        if mc.empty:
            continue
        # 00:00 UTC 스냅샷 = 업비트 전일(09:00 KST 시작) 일봉 종가 시점
        mc["date"] = pd.to_datetime(mc["ts"], unit="ms").dt.normalize() - pd.Timedelta(days=1)
        mc["symbol"] = s
        frames.append(mc[["date", "symbol", "mcap"]].drop_duplicates("date"))
        if i % 10 == 0:
            print(f"  {i}/{len(targets)} 완료")
    out = pd.concat(frames, ignore_index=True)
    out.to_csv(path, index=False)
    with open(os.path.join(DATA_DIR, "coingecko_symbol_map.json"), "w", encoding="utf-8") as f:
        json.dump({s: sym2id[s] for s in targets}, f, ensure_ascii=False, indent=1)
    return out


# ---------------------------------------------------------------------------
# 패널 구성
# ---------------------------------------------------------------------------
def build_panels(df):
    dates = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
    P = {}
    for col in ["open", "high", "low", "close", "value"]:
        P[col] = df.pivot(index="date", columns="symbol", values=col).reindex(dates)
    return P


def volume_universe(P, alts):
    val = P["value"][alts]
    avg = val.rolling(VOLUME_WINDOW, min_periods=VOLUME_WINDOW // 2).mean()
    age = P["close"][alts].notna().cumsum()
    avg = avg.where(age >= MIN_HISTORY)
    rank = avg.rank(axis=1, ascending=False)
    return rank <= VOLUME_TOP_N            # 해당 날짜 종가 기준 (당일 정보)


def mcap_universe(P, alts, mc):
    m = mc.pivot(index="date", columns="symbol", values="mcap")
    m = m.reindex(index=P["close"].index, columns=alts)
    age = P["close"][alts].notna().cumsum()
    elig = (m >= MCAP_THRESHOLD) & (age >= MIN_HISTORY)
    first = m.dropna(how="all").index.min()
    return elig, first


def scores(close):
    out = {}
    r = {L: close / close.shift(L) - 1 for L in (7, 14, 21, 28)}
    for L in (7, 14, 21, 28):
        out[str(L)] = r[L]
    out["mix7_28"] = (r[7] + r[28]) / 2
    return out


# ---------------------------------------------------------------------------
# 시뮬레이션
# ---------------------------------------------------------------------------
def simulate(O, H, Lo, C, elig, score, btc_ok, rebal, sl, tp, abs_mom,
             k=TOP_K, tp_frac=TP_FRACTION):
    """
    O, H, Lo, C : (T, N) 가격 배열
    elig        : (T, N) 전일 종가 기준 편입 가능 여부 (이미 1일 시프트된 상태)
    score       : (T, N) 전일 종가 기준 점수 (이미 1일 시프트)
    btc_ok      : (T,) 전일 종가 기준 BTC 필터 통과 여부
    rebal       : 리밸런싱 날짜 인덱스 배열 (오름차순)
    반환: 일별 자산가치 (rebal[0]부터 끝까지), 통계
    """
    T, N = O.shape
    cost = FEE + SLIPPAGE
    stop_cost = FEE + STOP_SLIPPAGE
    cash = 1.0
    units = np.zeros(N)
    ref = np.zeros(N)
    tp_done = np.zeros(N, dtype=bool)
    last_px = np.full(N, np.nan)
    start = rebal[0]
    eq = np.empty(T - start)
    n_sl = n_tp = invested_weeks = 0
    turnover = 0.0

    # 리밸 이전 종가로 last_px 초기화
    for t in range(max(0, start - 10), start):
        v = ~np.isnan(C[t])
        last_px[v] = C[t][v]

    bounds = list(rebal) + [T]
    for w in range(len(rebal)):
        d, nd = bounds[w], bounds[w + 1]
        px = np.where(np.isnan(O[d]), last_px, O[d])
        held = units > 0
        cur_val = np.where(held, units * np.nan_to_num(px), 0.0)
        V = cash + cur_val.sum()

        picks = np.array([], dtype=int)
        if btc_ok[d]:
            s = score[d]
            mask = elig[d] & ~np.isnan(s) & ~np.isnan(O[d])
            if abs_mom:
                mask &= s > 0
            idx = np.where(mask)[0]
            if len(idx):
                picks = idx[np.argsort(-s[idx])][:k]

        target = np.zeros(N)
        if len(picks):
            target[picks] = V / k
            invested_weeks += 1
        trade = np.abs(target - cur_val).sum()
        turnover += trade / V if V > 0 else 0
        fee = trade * cost
        units[:] = 0.0
        if len(picks):
            units[picks] = target[picks] / O[d, picks]
            ref[picks] = O[d, picks]
        tp_done[:] = False
        cash = V - target.sum() - fee

        for t in range(d, nd):
            for j in np.where(units > 0)[0]:
                o, h, l = O[t, j], H[t, j], Lo[t, j]
                if np.isnan(o):
                    continue
                if sl > 0 and l <= ref[j] * (1 - sl):          # 같은 날 둘 다 닿으면 손절 우선(보수적)
                    fill = min(o, ref[j] * (1 - sl))
                    cash += units[j] * fill * (1 - stop_cost)
                    units[j] = 0.0
                    n_sl += 1
                    continue
                if tp > 0 and not tp_done[j] and h >= ref[j] * (1 + tp):
                    fill = max(o, ref[j] * (1 + tp))
                    q = units[j] * tp_frac
                    cash += q * fill * (1 - cost)
                    units[j] -= q
                    tp_done[j] = True
                    n_tp += 1
            v = ~np.isnan(C[t])
            last_px[v] = C[t][v]
            eq[t - start] = cash + np.nansum(units * last_px)

    weeks = len(rebal)
    stats = dict(n_sl=n_sl, n_tp=n_tp, exposure=invested_weeks / weeks,
                 turnover_per_week=turnover / weeks)
    return eq, stats


def metrics(eq, dates):
    if len(eq) < 30:
        return dict(cagr=np.nan, mdd=np.nan, sharpe=np.nan, calmar=np.nan, total=np.nan)
    eq = np.asarray(eq, dtype=float)
    years = (dates[-1] - dates[0]).days / 365.0
    total = eq[-1] / eq[0] - 1
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1 if years > 0 and eq[-1] > 0 else -1.0
    peak = np.maximum.accumulate(eq)
    mdd = (eq / peak - 1).min()
    r = np.diff(eq) / eq[:-1]
    sharpe = r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else np.nan
    calmar = cagr / abs(mdd) if mdd < 0 else np.nan
    return dict(cagr=cagr, mdd=mdd, sharpe=sharpe, calmar=calmar, total=total)


def yearly(eq, dates):
    s = pd.Series(eq, index=dates)
    out = {}
    for y, g in s.groupby(s.index.year):
        prev = s[s.index < g.index[0]]
        base = prev.iloc[-1] if len(prev) else g.iloc[0]
        out[str(y)] = g.iloc[-1] / base - 1
    return out


# ---------------------------------------------------------------------------
# 그리드 실행
# ---------------------------------------------------------------------------
def run_grid(P, elig_df, trade_start, label):
    alts = list(elig_df.columns)
    idx = P["close"].index
    O = P["open"][alts].to_numpy(float)
    H = P["high"][alts].to_numpy(float)
    Lo = P["low"][alts].to_numpy(float)
    C = P["close"][alts].to_numpy(float)
    # 신호는 전일 종가 기준 -> 1일 시프트
    elig = elig_df.shift(1).fillna(False).to_numpy(bool)
    sc = {k: v[alts].shift(1).to_numpy(float) for k, v in scores(P["close"]).items()}

    btc = P["close"]["BTC"]
    btc_ok = {0: np.ones(len(idx), dtype=bool)}
    for n in BTC_FILTERS:
        if n:
            ok = (btc > btc.rolling(n).mean()).shift(1).fillna(False)
            btc_ok[n] = ok.to_numpy(bool)

    first = max(pd.Timestamp(trade_start), idx[0] + pd.Timedelta(days=WARMUP_DAYS - 20))
    rebal = np.array([i for i, d in enumerate(idx) if d >= first and d.weekday() == REBAL_WEEKDAY])
    rebal = rebal[rebal < len(idx) - 1]
    sim_dates = idx[rebal[0]:]
    split = sim_dates[0] + (sim_dates[-1] - sim_dates[0]) * 2 / 3
    is_mask = sim_dates < split

    # 벤치마크: BTC 보유
    bO = P["open"]["BTC"].to_numpy(float)
    bC = P["close"]["BTC"].ffill().to_numpy(float)
    btc_eq = bC[rebal[0]:] / bO[rebal[0]]
    bench = dict(full=metrics(btc_eq, sim_dates),
                 is_=metrics(btc_eq[is_mask], sim_dates[is_mask]),
                 oos=metrics(btc_eq[~is_mask], sim_dates[~is_mask]),
                 yearly=yearly(btc_eq, sim_dates))

    combos = list(itertools.product(LOOKBACKS, BTC_FILTERS, STOP_LOSSES, TAKE_PROFITS, ABS_MOMENTUM))
    print(f"\n[{label}] {sim_dates[0].date()} ~ {sim_dates[-1].date()} | "
          f"조합 {len(combos)}개 | 학습/검증 분할 {split.date()}")
    rows, curves = [], {}
    t0 = time.time()
    for i, (lb, ma, sl, tp, ab) in enumerate(combos, 1):
        eq, st = simulate(O, H, Lo, C, elig, sc[lb], btc_ok[ma], rebal, sl, tp, ab)
        f = metrics(eq, sim_dates)
        a = metrics(eq[is_mask], sim_dates[is_mask])
        b = metrics(eq[~is_mask], sim_dates[~is_mask])
        row = dict(lookback=lb, btc_ma=ma, sl=sl, tp=tp, abs_mom=ab)
        row.update({f"full_{k}": v for k, v in f.items()})
        row.update({f"is_{k}": v for k, v in a.items()})
        row.update({f"oos_{k}": v for k, v in b.items()})
        row.update(st)
        rows.append(row)
        curves[(lb, ma, sl, tp, ab)] = eq
        if i % 100 == 0:
            print(f"  {i}/{len(combos)} ({time.time() - t0:.0f}초)")
    grid = pd.DataFrame(rows)
    return grid, curves, sim_dates, bench, split


# ---------------------------------------------------------------------------
# 요약
# ---------------------------------------------------------------------------
def fmt_pct(x):
    return "" if pd.isna(x) else f"{x * 100:,.1f}%"


def summarize(grid, curves, sim_dates, bench, split, label):
    L = []
    L.append("=" * 100)
    L.append(f"[{label}] 기간 {sim_dates[0].date()} ~ {sim_dates[-1].date()}  "
             f"(학습 구간 ~{split.date()}, 검증 구간 이후)")
    L.append("=" * 100)

    def bline(name, m):
        return (f"{name:<10} CAGR {fmt_pct(m['cagr']):>9} | MDD {fmt_pct(m['mdd']):>8} | "
                f"Sharpe {m['sharpe']:.2f} | Calmar {m['calmar']:.2f}")
    L.append("BTC 보유 벤치마크")
    L.append("  " + bline("전체", bench["full"]))
    L.append("  " + bline("학습", bench["is_"]))
    L.append("  " + bline("검증", bench["oos"]))
    L.append("  연도별: " + ", ".join(f"{y} {fmt_pct(v)}" for y, v in bench["yearly"].items()))

    show = ["lookback", "btc_ma", "sl", "tp", "abs_mom",
            "full_cagr", "full_mdd", "full_sharpe", "full_calmar",
            "is_sharpe", "oos_sharpe", "oos_cagr", "oos_mdd",
            "exposure", "n_sl", "n_tp", "turnover_per_week"]

    def table(df):
        d = df[show].copy()
        for c in ["full_cagr", "full_mdd", "oos_cagr", "oos_mdd", "exposure", "sl", "tp", "turnover_per_week"]:
            d[c] = d[c].map(fmt_pct)
        for c in ["full_sharpe", "full_calmar", "is_sharpe", "oos_sharpe"]:
            d[c] = d[c].map(lambda x: "" if pd.isna(x) else f"{x:.2f}")
        return d.to_string(index=False)

    o = ORIGINAL
    orig = grid[(grid.lookback == o["lookback"]) & (grid.btc_ma == o["btc_ma"]) &
                (grid.sl == o["sl"]) & (grid.tp == o["tp"]) & (grid.abs_mom == o["abs_mom"])]
    L.append("\n원안 (룩백 7일, BTC 필터 없음, 손절 10%, 익절 10%에서 50%)")
    L.append(table(orig))

    L.append("\n전체 기간 Sharpe 상위 15")
    L.append(table(grid.sort_values("full_sharpe", ascending=False).head(15)))

    L.append("\n전체 기간 Calmar(CAGR/MDD) 상위 10")
    L.append(table(grid.sort_values("full_calmar", ascending=False).head(10)))

    q = grid["is_sharpe"].quantile(0.8)
    L.append(f"\n학습구간 Sharpe 상위 20% 중 검증구간 Sharpe 상위 10 (과최적화 점검)")
    L.append(table(grid[grid.is_sharpe >= q].sort_values("oos_sharpe", ascending=False).head(10)))

    L.append("\n파라미터별 평균 성과 (다른 파라미터 전부 평균 - 견고성 확인용)")
    for p in ["lookback", "btc_ma", "sl", "tp", "abs_mom"]:
        g = grid.groupby(p)[["full_sharpe", "full_cagr", "full_mdd", "oos_sharpe"]].median()
        g["full_cagr"] = g["full_cagr"].map(fmt_pct)
        g["full_mdd"] = g["full_mdd"].map(fmt_pct)
        g["full_sharpe"] = g["full_sharpe"].map(lambda x: f"{x:.2f}")
        g["oos_sharpe"] = g["oos_sharpe"].map(lambda x: f"{x:.2f}")
        L.append(f"\n  [{p}] (중앙값)")
        L.append("  " + g.to_string().replace("\n", "\n  "))

    L.append("\n연도별 수익률 (Sharpe 상위 5 + 원안)")
    keys = [tuple(r) for r in grid.sort_values("full_sharpe", ascending=False)
            .head(5)[["lookback", "btc_ma", "sl", "tp", "abs_mom"]].itertuples(index=False)]
    keys.append((o["lookback"], o["btc_ma"], o["sl"], o["tp"], o["abs_mom"]))
    for k in keys:
        y = yearly(curves[k], sim_dates)
        L.append(f"  {str(k):<40} " + ", ".join(f"{yy} {fmt_pct(v)}" for yy, v in y.items()))
    return "\n".join(L)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", choices=["volume", "mcap", "both"], default="both")
    ap.add_argument("--cg-key", default=None, help="코인게코 Demo API 키 (선택)")
    ap.add_argument("--refresh", action="store_true", help="저장된 데이터 무시하고 다시 수집")
    args = ap.parse_args()

    os.makedirs(RES_DIR, exist_ok=True)
    raw = load_upbit(args.refresh)
    P = build_panels(raw)
    if "BTC" not in P["close"].columns:
        sys.exit("BTC 데이터가 없습니다.")
    alts = [s for s in P["close"].columns if s != "BTC" and s not in STABLES]

    out = [f"생성 시각: {datetime.now():%Y-%m-%d %H:%M}",
           f"설정: 보유 {TOP_K}개, 매주 {['월','화','수','목','금','토','일'][REBAL_WEEKDAY]}요일 09시 리밸런싱, "
           f"수수료 {FEE*100:.2f}%, 슬리피지 {SLIPPAGE*100:.1f}%(손절 {STOP_SLIPPAGE*100:.1f}%), 익절 매도비중 {TP_FRACTION*100:.0f}%",
           "주의: 상장폐지 코인은 업비트 API에서 조회되지 않아 결과가 실제보다 좋게 나올 수 있음(생존 편향)\n"]

    if args.universe in ("volume", "both"):
        elig = volume_universe(P, alts)
        res = run_grid(P, elig, TRADE_START, "거래대금 상위 30 유니버스")
        res[0].to_csv(os.path.join(RES_DIR, "grid_volume.csv"), index=False, encoding="utf-8-sig")
        out.append(summarize(*res, "거래대금 상위 30 유니버스"))

    if args.universe in ("mcap", "both"):
        try:
            mc = load_mcap(alts, args.cg_key, args.refresh)
            elig, first = mcap_universe(P, alts, mc)
            res = run_grid(P, elig, max(pd.Timestamp(TRADE_START), first + pd.Timedelta(days=1)),
                           "시총 3조원 이상 유니버스")
            res[0].to_csv(os.path.join(RES_DIR, "grid_mcap.csv"), index=False, encoding="utf-8-sig")
            out.append(summarize(*res, "시총 3조원 이상 유니버스 (최근 1년)"))
        except Exception as e:
            out.append(f"\n[시총 유니버스] 실행 실패: {e}")
            print(f"시총 유니버스 실패: {e}")

    text = "\n".join(out)
    with open(os.path.join(RES_DIR, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print(f"\n완료: {os.path.join(RES_DIR, 'summary.txt')}")


if __name__ == "__main__":
    main()
