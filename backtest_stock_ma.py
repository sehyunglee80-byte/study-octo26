"""
주식·지수 이동평균 추세추종 백테스트 (BTC 200일선 분석을 주식에 적용)
=====================================================================

대상: SPY, QQQ (야후, 배당 재투자 반영 수정주가), 코스피 지수, 삼성전자 (네이버, 수정주가, 배당 미반영)
기본 규칙: 매주 첫 거래일 시가에 판단, 전일 종가 > N일 이평선이면 보유, 아니면 현금
비교: 이평선 20에서 250일, 주 1회 vs 매일, 버퍼 ±1에서 5%, 확인 필터, 요일별, 요일 분할
기간: 2022-01-03부터 (BTC 분석과 같은 구간), 2001-01-02부터 (2008년, 2020년 하락장 포함)

비용 (편도)
  SPY, QQQ  : 수수료 0.07% + 슬리피지 0.05%
  코스피    : 수수료 0.015% + 슬리피지 0.05% (KODEX 200 같은 ETF로 매매 가정, 거래세 면제)
  삼성전자  : 수수료 0.015% + 슬리피지 0.05%, 매도 시 거래세 0.20% (2026년 세율을 전 기간에 적용)
  환전 비용, 양도세, 배당세는 반영하지 않음. 현금 이자는 0 (SPY, QQQ는 미국 단기국채 이자 반영 결과를 따로 표시)

사용법
  pip install requests pandas numpy
  python backtest_stock_ma.py            # 저장된 데이터가 있으면 재사용
  python backtest_stock_ma.py --refresh  # 데이터 다시 받기

결과
  results/summary_stock_ma.txt : 요약
  results/grid_stock_ma.csv    : 전체 조합 결과
  results/yearly_stock_ma.csv  : 연도별 수익률
"""

import argparse
import os
import time
from datetime import datetime

import numpy as np
import pandas as pd
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data", "stocks")
RES_DIR = os.path.join(BASE, "results")
UA = {"User-Agent": "Mozilla/5.0"}

ASSETS = {
    # 이름: (소스, 코드, 매수 비용, 매도 비용)
    "SPY": ("yahoo", "SPY", 0.0012, 0.0012),
    "QQQ": ("yahoo", "QQQ", 0.0012, 0.0012),
    "코스피": ("naver_index", "KOSPI", 0.00065, 0.00065),
    "삼성전자": ("naver_item", "005930", 0.00065, 0.00265),
}
PERIODS = {"2022": "2022-01-03", "2001": "2001-01-02"}
DATA_START = "1999-01-01"
MAS = [20, 50, 60, 100, 120, 150, 200, 250]
MAIN_MA = 200
BUFFERS = [0.01, 0.02, 0.03, 0.04, 0.05]
CONFIRMS = [2, 3, 5]
WD = ["월", "화", "수", "목", "금"]
ANN = 252


# ---------------------------------------------------------------------------
# 데이터
# ---------------------------------------------------------------------------
def fetch_yahoo(code):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{code}",
                     params={"period1": int(pd.Timestamp(DATA_START).timestamp()),
                             "period2": int(time.time()), "interval": "1d", "events": "div,split"},
                     headers=UA, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({"date": pd.to_datetime(res["timestamp"], unit="s").normalize(),
                       "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"]})
    if "adjclose" in res["indicators"]:
        # 배당 재투자 반영: 시가, 고가, 저가도 같은 비율로 조정
        f = np.array(res["indicators"]["adjclose"][0]["adjclose"], float) / df["close"].to_numpy(float)
        for c in ["open", "high", "low", "close"]:
            df[c] = df[c] * f
    return df


def fetch_naver(kind, code):
    frames = []
    end = pd.Timestamp.today()
    for y in range(pd.Timestamp(DATA_START).year, end.year + 1):
        r = requests.get(f"https://api.stock.naver.com/chart/domestic/{kind}/{code}/day",
                         params={"startDateTime": f"{y}01010000", "endDateTime": f"{y}12312359"},
                         headers=UA, timeout=30)
        r.raise_for_status()
        rows = r.json()
        if rows:
            frames.append(pd.DataFrame(rows))
        time.sleep(0.2)
    df = pd.concat(frames, ignore_index=True)
    return pd.DataFrame({"date": pd.to_datetime(df["localDate"]), "open": df["openPrice"],
                         "high": df["highPrice"], "low": df["lowPrice"], "close": df["closePrice"]})


def load(name, refresh=False):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{name}.csv")
    if os.path.exists(path) and not refresh:
        df = pd.read_csv(path, parse_dates=["date"])
    else:
        src, code = ASSETS[name][:2] if name in ASSETS else ("yahoo", name)
        if src == "yahoo":
            df = fetch_yahoo(code)
        else:
            df = fetch_naver("index" if src == "naver_index" else "item", code)
        df.to_csv(path, index=False)
    df = df.dropna(subset=["close"]).drop_duplicates("date").sort_values("date").set_index("date")
    # 시가가 비거나 0인 날(일부 옛 지수 데이터)은 전일 종가로 대체
    bad = df["open"].isna() | (df["open"] <= 0)
    df.loc[bad, "open"] = df["close"].shift(1)[bad]
    return df.dropna(subset=["open"])


def load_tbill(refresh=False):
    """미국 13주 국채 금리(연 %) -> 거래일 기준 일 이자율"""
    df = load("^IRX", refresh)
    return (df["close"] / 100 / ANN).clip(lower=0)


# ---------------------------------------------------------------------------
# 신호
# ---------------------------------------------------------------------------
def above_state(close, ma):
    return (close > ma).to_numpy()


def buffer_state(close, ma, b):
    """종가가 이평선 x (1+b) 위로 올라서면 보유, x (1-b) 아래로 내려가면 현금, 그 사이는 이전 상태 유지"""
    c, m = close.to_numpy(float), ma.to_numpy(float)
    st, s = np.zeros(len(c), bool), False
    for i in range(len(c)):
        if np.isnan(m[i]):
            continue
        if not s and c[i] > m[i] * (1 + b):
            s = True
        elif s and c[i] < m[i] * (1 - b):
            s = False
        st[i] = s
    return st


def confirm_state(close, ma, n):
    """n일 연속으로 이평선 반대편에서 마감해야 상태가 바뀜"""
    raw = above_state(close, ma)
    valid = ~np.isnan(ma.to_numpy(float))
    st, s, run = np.zeros(len(raw), bool), False, 0
    for i in range(len(raw)):
        if not valid[i]:
            continue
        run = run + 1 if raw[i] != s else 0
        if run >= n:
            s, run = raw[i], 0
        st[i] = s
    return st


def decision_days(idx, weekday):
    """주마다 weekday 이후 첫 거래일 (그 요일이 휴장이면 같은 주의 다음 거래일, 그 주에 없으면 건너뜀)"""
    s = pd.Series(idx, index=idx)
    wk = idx.to_period("W-SUN")
    cand = s[idx.weekday >= weekday]
    first = cand.groupby(wk[idx.weekday >= weekday]).first()
    out = np.zeros(len(idx), bool)
    out[idx.get_indexer(first.values)] = True
    return out


def positions(state, dec):
    """state: 각 날 종가 기준 상태. 다음 날 시가에 반영. dec: 판단하는 날 (None이면 매일)"""
    prev = np.concatenate([[False], state[:-1]])
    if dec is None:
        return prev
    pos, cur = np.zeros(len(state), bool), False
    for i in range(len(state)):
        if dec[i]:
            cur = prev[i]
        pos[i] = cur
    return pos


# ---------------------------------------------------------------------------
# 시뮬레이션과 지표
# ---------------------------------------------------------------------------
def simulate(o, c, pos, buy_cost, sell_cost, cash_rate=None):
    """시가 체결, 종가 평가. 원금 1. 반환: 일별 종가 기준 자산, 매매 횟수(매수, 매도 각각 1회)"""
    cash, units, n = 1.0, 0.0, 0
    eq = np.empty(len(o))
    for t in range(len(o)):
        if pos[t] and units == 0:
            units, cash, n = cash * (1 - buy_cost) / o[t], 0.0, n + 1
        elif not pos[t] and units > 0:
            cash, units, n = units * o[t] * (1 - sell_cost), 0.0, n + 1
        if cash_rate is not None and units == 0:
            cash *= 1 + cash_rate[t]
        eq[t] = cash + units * c[t]
    return eq, n


def metrics(eq, dates, base=1.0):
    eq = np.asarray(eq, float)
    years = (dates[-1] - dates[0]).days / 365.25
    total = eq[-1] / base - 1
    cagr = (eq[-1] / base) ** (1 / years) - 1 if years > 0 and eq[-1] > 0 else np.nan
    full = np.concatenate([[base], eq])
    peak = np.maximum.accumulate(full)
    dd = full / peak - 1
    mdd = dd.min()
    r = np.diff(full) / full[:-1]
    sharpe = r.mean() / r.std() * np.sqrt(ANN) if r.std() > 0 else np.nan
    calmar = cagr / abs(mdd) if mdd < 0 else np.nan
    s = pd.Series(eq, index=dates)
    m_end = s.resample("ME").last()
    m_ret = m_end / m_end.shift(1).fillna(base) - 1
    # 전고점 회복 기간: 고점에서 다시 그 고점을 넘을 때까지 걸린 달력 일수 중 최장
    under = dd[1:] < -1e-12
    longest, cur_start, ongoing = 0, None, False
    for i, u in enumerate(under):
        if u and cur_start is None:
            cur_start = dates[i - 1] if i > 0 else dates[0]
        elif not u and cur_start is not None:
            longest = max(longest, (dates[i] - cur_start).days)
            cur_start = None
    if cur_start is not None:
        days = (dates[-1] - cur_start).days
        if days > longest:
            longest, ongoing = days, True
    return dict(total=total, cagr=cagr, mdd=mdd, sharpe=sharpe, calmar=calmar,
                worst_month=m_ret.min(), loss_month_ratio=(m_ret < 0).mean(),
                recovery_days=longest, recovery_ongoing=ongoing)


def yearly(eq, dates, base=1.0):
    s = pd.Series(eq, index=dates)
    y_end = s.groupby(s.index.year).last()
    return (y_end / y_end.shift(1).fillna(base) - 1).to_dict()


def evaluate(eq, dates, n_trades, exposure):
    split = dates[0] + (dates[-1] - dates[0]) * 2 / 3
    m = dates < split
    row = {f"full_{k}": v for k, v in metrics(eq, dates).items()}
    row.update({f"is_{k}": v for k, v in metrics(eq[m], dates[m]).items() if k in ("cagr", "mdd", "sharpe")})
    oos_base = eq[m][-1]
    row.update({f"oos_{k}": v for k, v in metrics(eq[~m], dates[~m], oos_base).items() if k in ("cagr", "mdd", "sharpe")})
    row.update(trades=n_trades, exposure=exposure, split=split.date())
    return row


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
def run_asset(name, df, start, tbill=None):
    buy, sell = ASSETS[name][2:]
    close = df["close"]
    i0 = df.index.searchsorted(pd.Timestamp(start))
    sl = slice(i0, None)
    idx = df.index[sl]
    o, c = df["open"].to_numpy(float)[sl], close.to_numpy(float)[sl]
    rows, curves = [], {}

    def add(rule, ma, param, state, dec, label=None):
        pos = positions(state, dec)[sl]
        eq, n = simulate(o, c, pos, buy, sell)
        row = dict(asset=name, start=idx[0].date(), rule=rule, ma=ma, param=param)
        row.update(evaluate(eq, idx, n, pos.mean()))
        rows.append(row)
        curves[label or (rule, ma, param)] = eq
        return pos

    # 단순 보유
    hold = c / o[0]
    row = dict(asset=name, start=idx[0].date(), rule="보유", ma=0, param="")
    row.update(evaluate(hold, idx, 1, 1.0))
    rows.append(row)
    curves["보유"] = hold

    mon = decision_days(df.index, 0)
    for ma_n in MAS:
        ma = close.rolling(ma_n).mean()
        if ma.iloc[:i0].isna().all() or np.isnan(ma.iloc[i0 - 1]):
            continue
        st = above_state(close, ma)
        add("주1회", ma_n, "", st, mon)
        add("매일", ma_n, "", st, None)

    ma = close.rolling(MAIN_MA).mean()
    st = above_state(close, ma)
    for b in BUFFERS:
        add("버퍼(매일)", MAIN_MA, f"±{b * 100:g}%", buffer_state(close, ma, b), None)
        add("버퍼(주1회)", MAIN_MA, f"±{b * 100:g}%", buffer_state(close, ma, b), mon)
    for n in CONFIRMS:
        add("확인(매일)", MAIN_MA, f"{n}일", confirm_state(close, ma, n), None)
    sleeves, n_sum = [], 0
    for d in range(5):
        pos = add("요일", MAIN_MA, WD[d], st, decision_days(df.index, d))
        eq, n = simulate(o, c, pos, buy, sell)
        sleeves.append(eq / 5)
        n_sum += n
    split_eq = np.sum(sleeves, axis=0)
    row = dict(asset=name, start=idx[0].date(), rule="요일 분할", ma=MAIN_MA, param="5등분")
    row.update(evaluate(split_eq, idx, n_sum, np.nan))
    rows.append(row)
    curves[("요일 분할", MAIN_MA, "5등분")] = split_eq

    # 현금 이자 반영 (SPY, QQQ)
    if tbill is not None:
        rate = tbill.reindex(df.index).ffill().fillna(0).to_numpy(float)[sl]
        pos = positions(st, mon)[sl]
        eq, n = simulate(o, c, pos, buy, sell, rate)
        row = dict(asset=name, start=idx[0].date(), rule="주1회+국채이자", ma=MAIN_MA, param="")
        row.update(evaluate(eq, idx, n, pos.mean()))
        rows.append(row)
        curves[("주1회+국채이자", MAIN_MA, "")] = eq
    return rows, curves, idx


def pct(x):
    return "" if pd.isna(x) else f"{x * 100:,.1f}%"


def fmt_table(df, cols=None):
    cols = cols or ["rule", "ma", "param", "full_total", "full_cagr", "full_mdd", "full_sharpe", "full_calmar",
                    "is_sharpe", "oos_sharpe", "oos_cagr", "oos_mdd", "full_worst_month",
                    "full_loss_month_ratio", "full_recovery_days", "trades", "exposure"]
    names = {"rule": "규칙", "ma": "이평", "param": "옵션", "full_total": "누적", "full_cagr": "CAGR",
             "full_mdd": "MDD", "full_sharpe": "샤프", "full_calmar": "칼마", "is_sharpe": "학습샤프",
             "oos_sharpe": "검증샤프", "oos_cagr": "검증CAGR", "oos_mdd": "검증MDD",
             "full_worst_month": "최악월", "full_loss_month_ratio": "손실월", "full_recovery_days": "회복일",
             "trades": "매매", "exposure": "보유비중"}
    d = df[cols].copy()
    for k in ["full_total", "full_cagr", "full_mdd", "oos_cagr", "oos_mdd", "full_worst_month",
              "full_loss_month_ratio", "exposure"]:
        if k in d:
            d[k] = d[k].map(pct)
    for k in ["full_sharpe", "full_calmar", "is_sharpe", "oos_sharpe"]:
        if k in d:
            d[k] = d[k].map(lambda x: "" if pd.isna(x) else f"{x:.2f}")
    if "full_recovery_days" in d:
        rec = df["full_recovery_days"].astype(int).astype(str)
        d["full_recovery_days"] = rec + np.where(df["full_recovery_ongoing"].astype(bool), "+", "")
    d["ma"] = d["ma"].replace(0, "")
    return d.rename(columns=names).to_string(index=False)


def summarize(grid, yearly_rows):
    L = [f"생성 시각: {datetime.now():%Y-%m-%d %H:%M}",
         "기본 규칙: 매주 첫 거래일 시가에 판단, 전일 종가 > N일 이평선이면 보유, 아니면 현금 (현금 이자 0)",
         "비용(편도): SPY·QQQ 0.12%, 코스피(ETF 가정) 0.065%, 삼성전자 매수 0.065% / 매도 0.265%(거래세 0.20% 포함)",
         "데이터: SPY·QQQ 배당 재투자 반영, 코스피·삼성전자 배당 미반영(가격만)",
         "회복일: 전고점 회복까지 걸린 최장 달력 일수, + 는 아직 회복 중",
         "학습/검증: 기간 앞 2/3, 뒤 1/3", ""]
    for start in grid["start"].unique():
        g0 = grid[grid.start == start]
        L.append("#" * 110)
        L.append(f"# {start}부터")
        L.append("#" * 110)
        main = g0[((g0.rule == "주1회") & (g0.ma == MAIN_MA)) | (g0.rule == "보유")]
        L.append("\n[핵심] 200일선 주 1회 vs 단순 보유")
        L.append(fmt_table(main.assign(rule=main.asset + " " + main.rule)))
        for a in g0.asset.unique():
            g = g0[g0.asset == a]
            L.append(f"\n{'=' * 110}\n{a} ({start}부터, 학습/검증 분할 {g.split.iloc[0]})\n{'=' * 110}")
            L.append("\n이평선 기간별 (주 1회 / 매일)")
            L.append(fmt_table(g[g.rule.isin(["보유", "주1회", "매일"])].sort_values(["rule", "ma"])))
            L.append(f"\n{MAIN_MA}일선 변형 (버퍼, 확인 필터, 요일, 요일 분할, 국채 이자)")
            L.append(fmt_table(g[~g.rule.isin(["주1회", "매일"]) | ((g.ma == MAIN_MA) & g.rule.isin(["주1회", "매일"]))]
                               .query("rule != '보유'")))
            y = yearly_rows[(yearly_rows.asset == a) & (yearly_rows.start == start)]
            L.append("\n연도별 수익률")
            L.append(y.drop(columns=["asset", "start"]).set_index("전략").dropna(axis=1, how="all")
                     .sort_index(axis=1).map(pct).to_string())
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    os.makedirs(RES_DIR, exist_ok=True)
    tbill = load_tbill(args.refresh)
    all_rows, yrows = [], []
    for name in ASSETS:
        df = load(name, args.refresh)
        print(f"{name}: {df.index[0].date()}부터 {df.index[-1].date()}까지 {len(df)}일")
        for pname, start in PERIODS.items():
            rows, curves, idx = run_asset(name, df, start, tbill if ASSETS[name][0] == "yahoo" else None)
            all_rows += rows
            for key, label in [("보유", "단순 보유"), (("주1회", MAIN_MA, ""), "200일선 주1회"),
                               (("매일", MAIN_MA, ""), "200일선 매일"),
                               (("버퍼(매일)", MAIN_MA, "±2%"), "200일선 버퍼±2%(매일)")]:
                y = yearly(curves[key], idx)
                yrows.append(dict(asset=name, start=idx[0].date(), 전략=label, **{str(k): v for k, v in y.items()}))
    grid = pd.DataFrame(all_rows)
    yr = pd.DataFrame(yrows)
    grid.to_csv(os.path.join(RES_DIR, "grid_stock_ma.csv"), index=False, encoding="utf-8-sig")
    yr.to_csv(os.path.join(RES_DIR, "yearly_stock_ma.csv"), index=False, encoding="utf-8-sig")
    text = summarize(grid, yr)
    with open(os.path.join(RES_DIR, "summary_stock_ma.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
