"""
SPY, QQQ 200일선 버퍼 전략 상세 검증 (backtest_stock_ma.py 의 함수 재사용)
=========================================================================

질문: 200일선에 버퍼(±N%)를 두면 정말 나아지는가, 아니면 2001년 이후 데이터에 맞춘 우연인가

검증 항목
  1. 이평선 x 버퍼 격자 (매일 / 주 1회 판단): 좋은 값이 한 점인지 넓은 고원인지
  2. 비대칭 버퍼 (매수 문턱과 매도 문턱 따로): 효과가 매수 쪽인지 매도 쪽인지
  3. 표본 외 기간: SPY 상장 전 S&P500 지수(1930-1993), QQQ 상장 전 나스닥100 지수(1987-1999)
  4. 5년 롤링 구간: 버퍼가 기본 규칙(주 1회, 버퍼 없음)을 이긴 구간 비율
  5. 워크포워드: 매년 그 전까지의 데이터로 버퍼를 고르고 다음 해에 적용
  6. 비용 민감도: 편도 0.05%에서 0.5%까지
  7. 매매 통계: 승률, 평균 수익과 손실, 보유 기간
  8. 세후 비교: 해외주식 양도세 22% (연 250만 원 공제, 원금 1억 원 가정)

공통 가정
  - 매수·매도는 판단일 시가 체결, 비용 편도 0.12% (수수료 0.07% + 슬리피지 0.05%)
  - 현금에는 미국 13주 국채 금리(^IRX) 이자, 1960년 이전은 0
  - 샤프는 국채 금리를 뺀 초과수익 기준
  - SPY, QQQ는 배당 재투자 반영. S&P500, 나스닥100 지수는 배당 미반영(가격 지수)
  - S&P500 지수의 1962년 이전 데이터는 시가가 없어 종가에 체결 (신호 다음 날 종가)

사용법
  python validate_buffer.py
결과
  results/summary_buffer_validation.txt, results/buffer_grid.csv, results/buffer_walkforward.csv
"""

import os

import numpy as np
import pandas as pd

import backtest_stock_ma as bs

COST = 0.0012
MA_MAIN = 200
ASSETS = {
    # 이름: (야후 코드, [(구간 이름, 시작, 끝)])
    "SPY": ("SPY", [("1994-", "1994-01-03", None), ("2001-", "2001-01-02", None), ("2022-", "2022-01-03", None)]),
    "QQQ": ("QQQ", [("2000-", "2000-01-03", None), ("2001-", "2001-01-02", None), ("2022-", "2022-01-03", None)]),
    "S&P500지수": ("^GSPC", [("1930-1993(SPY 이전)", "1930-01-02", "1993-12-31"), ("1930-", "1930-01-02", None)]),
    "나스닥100지수": ("^NDX", [("1987-1999(QQQ 이전)", "1987-01-02", "1999-12-31"), ("1987-", "1987-01-02", None)]),
}
GRID_MAS = [100, 150, 200, 250]
GRID_BUF = [0, 0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.035, 0.04, 0.045, 0.05, 0.06, 0.07, 0.08]
ASYM = [0, 0.01, 0.02, 0.03, 0.04, 0.05]
COSTS = [0.0005, 0.0012, 0.003, 0.005]
WF_POOL = [0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06]
# 집중 비교 후보: (이름, 이평, 매수 문턱, 매도 문턱, 판단 주기 D=매일 W=주1회)
CANDS = [("주1회 버퍼0(기본)", 200, 0, 0, "W"), ("매일 버퍼0", 200, 0, 0, "D"),
         ("매일 ±2%", 200, .02, .02, "D"), ("매일 ±3%", 200, .03, .03, "D"),
         ("매일 ±4%", 200, .04, .04, "D"), ("매일 ±5%", 200, .05, .05, "D"),
         ("주1회 ±2%", 200, .02, .02, "W"), ("주1회 ±4%", 200, .04, .04, "W")]
BASE_NAME = "주1회 버퍼0(기본)"


# ---------------------------------------------------------------------------
# 데이터
# ---------------------------------------------------------------------------
def load_long(code):
    path = os.path.join(bs.DATA_DIR, f"{code.replace('^', '_')}_long.csv")
    if not os.path.exists(path):
        os.makedirs(bs.DATA_DIR, exist_ok=True)
        saved, bs.DATA_START = bs.DATA_START, "1927-01-01"
        bs.fetch_yahoo(code).to_csv(path, index=False)
        bs.DATA_START = saved
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.dropna(subset=["close"]).drop_duplicates("date").sort_values("date").set_index("date")
    bad = df["open"].isna() | (df["open"] <= 0)
    df.loc[bad, "open"] = df["close"][bad]
    return df


class Asset:
    def __init__(self, code, rf_all):
        self.df = load_long(code)
        self.idx = self.df.index
        self.o = self.df["open"].to_numpy(float)
        self.c = self.df["close"].to_numpy(float)
        self.rf = rf_all.reindex(self.idx).ffill().fillna(0).to_numpy(float)
        self.weekly = bs.decision_days(self.idx, 0)
        self._ma, self._st = {}, {}

    def ma(self, n):
        if n not in self._ma:
            self._ma[n] = self.df["close"].rolling(n).mean().to_numpy(float)
        return self._ma[n]

    def state(self, n, up, down):
        key = (n, up, down)
        if key not in self._st:
            self._st[key] = asym_state(self.c, self.ma(n), up, down)
        return self._st[key]

    def pos(self, n, up, down, mode):
        return bs.positions(self.state(n, up, down), self.weekly if mode == "W" else None)

    def span(self, start, end):
        i0 = self.idx.searchsorted(pd.Timestamp(start))
        i1 = len(self.idx) if end is None else self.idx.searchsorted(pd.Timestamp(end), side="right")
        return i0, i1


def asym_state(c, m, up, down):
    """종가가 이평선 x (1+up) 위로 올라서면 보유, x (1-down) 아래로 내려가면 현금"""
    st, s = np.zeros(len(c), bool), False
    for i in range(len(c)):
        if np.isnan(m[i]):
            continue
        if not s and c[i] > m[i] * (1 + up):
            s = True
        elif s and c[i] < m[i] * (1 - down):
            s = False
        st[i] = s
    return st


# ---------------------------------------------------------------------------
# 시뮬레이션과 지표
# ---------------------------------------------------------------------------
def run(a, pos, i0, i1, cost=COST, interest=True):
    eq, n = bs.simulate(a.o[i0:i1], a.c[i0:i1], pos[i0:i1], cost, cost, a.rf[i0:i1] if interest else None)
    return eq, n


def fast_metrics(eq, rf, years):
    full = np.concatenate([[1.0], eq])
    r = np.diff(full) / full[:-1]
    ex = r - rf
    cagr = full[-1] ** (1 / years) - 1 if full[-1] > 0 else np.nan
    mdd = (full / np.maximum.accumulate(full) - 1).min()
    sharpe = ex.mean() / ex.std() * np.sqrt(bs.ANN) if ex.std() > 0 else np.nan
    return cagr, mdd, sharpe


def metrics(a, eq, i0, i1, interest=True):
    dates = a.idx[i0:i1]
    m = bs.metrics(eq, dates)
    rf = a.rf[i0:i1] if interest else np.zeros(i1 - i0)
    years = (dates[-1] - dates[0]).days / 365.25
    m["cagr"], m["mdd"], m["sharpe"] = fast_metrics(eq, rf, years)
    m["calmar"] = m["cagr"] / abs(m["mdd"]) if m["mdd"] < 0 else np.nan
    return m


def trade_stats(a, pos, i0, i1, cost=COST):
    o, c, p = a.o[i0:i1], a.c[i0:i1], pos[i0:i1]
    rets, days, entry = [], [], None
    for t in range(len(p)):
        if p[t] and entry is None:
            entry = t
        elif not p[t] and entry is not None:
            rets.append(o[t] / o[entry] * (1 - cost) ** 2 - 1)
            days.append((a.idx[i0 + t] - a.idx[i0 + entry]).days)
            entry = None
    if entry is not None:   # 마지막 보유 중인 거래는 종가로 평가
        rets.append(c[-1] / o[entry] * (1 - cost) ** 2 - 1)
        days.append((a.idx[i1 - 1] - a.idx[i0 + entry]).days)
    r = np.array(rets)
    if not len(r):
        return {}
    loss_streak = max((len(s) for s in "".join("L" if x < 0 else "W" for x in r).split("W")), default=0)
    return dict(round_trips=len(r), win_rate=(r > 0).mean(), avg_win=r[r > 0].mean() if (r > 0).any() else np.nan,
                avg_loss=r[r <= 0].mean() if (r <= 0).any() else np.nan, best=r.max(), worst=r.min(),
                med_hold_days=float(np.median(days)), max_loss_streak=loss_streak,
                short_trades=int((np.array(days) <= 30).sum()))


def after_tax(a, pos, i0, i1, cost=COST, tax=0.22, deduction=0.025):
    """해외주식 양도세: 해마다 실현손익 합계에서 250만 원(원금 1억 원의 2.5%) 공제 후 22%.
    연말에 납부, 손실 이월 없음. 마지막 날 전량 매도해 세금까지 낸 금액을 반환 (환율 변동은 무시)"""
    o, c, p, rf = a.o[i0:i1], a.c[i0:i1], pos[i0:i1], a.rf[i0:i1]
    years = a.idx[i0:i1].year
    cash, units, basis, realized, paid = 1.0, 0.0, 0.0, 0.0, 0.0
    for t in range(len(p)):
        if p[t] and units == 0:
            units, basis, cash = cash * (1 - cost) / o[t], cash, 0.0
        elif not p[t] and units > 0:
            proceeds = units * o[t] * (1 - cost)
            realized += proceeds - basis
            cash, units, basis = proceeds, 0.0, 0.0
        if units == 0:
            cash *= 1 + rf[t]
        if t == len(p) - 1 or years[t + 1] != years[t]:
            if t == len(p) - 1 and units > 0:
                proceeds = units * c[t] * (1 - cost)
                realized += proceeds - basis
                cash, units, basis = cash + proceeds, 0.0, 0.0
            due = tax * max(0.0, realized - deduction)
            if due > cash:            # 보유 중이면 일부 매도해 납부 (그 매도의 손익은 무시)
                sell_units = (due - cash) / (c[t] * (1 - cost))
                frac = sell_units / units
                units -= sell_units
                basis *= 1 - frac
                cash = due
            cash -= due
            paid += due
            realized = 0.0
    yrs = (a.idx[i1 - 1] - a.idx[i0]).days / 365.25
    return dict(after_tax_final=cash, after_tax_cagr=cash ** (1 / yrs) - 1, tax_paid=paid)


def rolling_compare(a, curves, i0, i1, years=5):
    """월초마다 시작하는 5년 구간에서 각 후보의 CAGR, MDD, 샤프"""
    dates = a.idx[i0:i1]
    rf = a.rf[i0:i1]
    starts = pd.Series(np.arange(len(dates)), index=dates).groupby(dates.to_period("M")).first().to_numpy()
    rows = []
    for s in starts:
        e = dates.searchsorted(dates[s] + pd.DateOffset(years=years))
        if e >= len(dates):
            break
        for name, eq in curves.items():
            base = eq[s - 1] if s > 0 else 1.0
            sub = eq[s:e] / base
            cagr, mdd, sh = fast_metrics(sub, rf[s:e], (dates[e - 1] - dates[s]).days / 365.25)
            rows.append(dict(start=dates[s], cand=name, cagr=cagr, mdd=mdd, sharpe=sh))
    return pd.DataFrame(rows)


def walk_forward(a, i0, i1, min_train_years=5):
    """해마다 그 전까지(구간 시작부터)의 샤프가 가장 높은 버퍼를 골라 그해에 적용. 매일 판단, 200일선"""
    pool = {b: a.pos(MA_MAIN, b, b, "D") for b in WF_POOL}
    years = a.idx[i0:i1].year
    first_year = years[0] + min_train_years
    wf_pos = np.zeros(len(a.idx), bool)
    picks = []
    for y in range(first_year, years[-1] + 1):
        tr_end = i0 + np.searchsorted(years, y)
        y_end = i0 + np.searchsorted(years, y + 1)
        best, best_sh = None, -np.inf
        for b, p in pool.items():
            eq, _ = run(a, p, i0, tr_end)
            sh = fast_metrics(eq, a.rf[i0:tr_end], (a.idx[tr_end - 1] - a.idx[i0]).days / 365.25)[2]
            if sh > best_sh:
                best, best_sh = b, sh
        wf_pos[tr_end:y_end] = pool[best][tr_end:y_end]
        picks.append(dict(year=y, buffer=best, train_sharpe=best_sh))
    s0 = i0 + np.searchsorted(years, first_year)
    return wf_pos, s0, pd.DataFrame(picks)


# ---------------------------------------------------------------------------
# 출력 보조
# ---------------------------------------------------------------------------
def pct(x, d=1):
    return "" if pd.isna(x) else f"{x * 100:.{d}f}%"


def num(x):
    return "" if pd.isna(x) else f"{x:.2f}"


def table(rows, cols):
    """rows: dict 리스트, cols: (키, 제목, 포맷) 리스트"""
    d = pd.DataFrame([{t: f(r.get(k)) if f else r.get(k) for k, t, f in cols} for r in rows])
    return d.to_string(index=False)


MAIN_COLS = [("name", "전략", None), ("cagr", "CAGR", pct), ("mdd", "MDD", pct), ("sharpe", "샤프", num),
             ("calmar", "칼마", num), ("worst_month", "최악월", pct), ("loss_month_ratio", "손실월", pct),
             ("recovery", "회복일", None), ("trades", "매매", None), ("exposure", "보유비중", pct)]


def cand_rows(a, i0, i1, interest=True, cost=COST):
    rows, curves = [], {}
    hold = np.ones(len(a.idx), bool)
    for name, n, up, down, mode in [("단순 보유", 0, 0, 0, "H")] + CANDS:
        p = hold if mode == "H" else a.pos(n, up, down, mode)
        eq, k = run(a, p, i0, i1, cost, interest)
        m = metrics(a, eq, i0, i1, interest)
        rec = f"{m['recovery_days']}" + ("+" if m["recovery_ongoing"] else "")
        rows.append(dict(name=name, recovery=rec, trades=k, exposure=p[i0:i1].mean(), **m))
        curves[name] = eq
    return rows, curves


# ---------------------------------------------------------------------------
def main():
    rf_all = (load_long("^IRX")["close"] / 100 / bs.ANN).clip(lower=0)

    L = ["SPY, QQQ 200일선 버퍼 전략 상세 검증",
         "공통: 판단일 시가 체결, 비용 편도 0.12%, 현금에 미국 13주 국채 이자(1960년 이전 0), 샤프는 국채 초과수익 기준",
         "버퍼 ±b%: 종가가 200일선 x (1+b) 위로 올라서면 다음 날 시가 매수, x (1-b) 아래로 내려가면 다음 날 시가 매도",
         "주1회: 매주 첫 거래일에만 위 상태를 확인해 매매. 매일: 신호 다음 날 바로 매매",
         "회복일: 전고점 회복까지 걸린 최장 달력 일수 (+는 아직 회복 중)", ""]
    grid_rows, wf_rows = [], []
    A = {name: Asset(code, rf_all) for name, (code, _) in ASSETS.items()}

    # 1. 후보 비교 (모든 자산, 모든 구간)
    L.append("=" * 110 + "\n1. 후보 비교\n" + "=" * 110)
    long_curves = {}
    for name, (code, periods) in ASSETS.items():
        a = A[name]
        for pname, start, end in periods:
            i0, i1 = a.span(start, end)
            rows, curves = cand_rows(a, i0, i1)
            L.append(f"\n[{name} {a.idx[i0].date()}부터 {a.idx[i1 - 1].date()}까지]")
            L.append(table(rows, MAIN_COLS))
            long_curves[(name, pname)] = (curves, i0, i1)

    # 1-1. 이전 결과와 같은 기준 (현금 이자 0, 원래 샤프)
    L.append("\n[참고] 현금 이자 0 기준 (backtest_stock_ma.py 결과와 같은 기준)")
    for name in ["SPY", "QQQ"]:
        a = A[name]
        for pname, start, end in ASSETS[name][1][1:]:
            i0, i1 = a.span(start, end)
            rows, _ = cand_rows(a, i0, i1, interest=False)
            L.append(f"  {name} {pname}: " + ", ".join(f"{r['name']} 샤프 {r['sharpe']:.2f}" for r in rows))

    # 2. 이평 x 버퍼 격자
    L.append("\n" + "=" * 110 + "\n2. 이평선 x 버퍼 격자 (샤프). 좋은 값이 넓게 퍼져 있으면 견고, 한 칸만 튀면 우연\n" + "=" * 110)
    for name, (code, periods) in ASSETS.items():
        a = A[name]
        for pname, start, end in periods:
            i0, i1 = a.span(start, end)
            for n in GRID_MAS:
                for b in GRID_BUF:
                    for mode in ["D", "W"]:
                        eq, k = run(a, a.pos(n, b, b, mode), i0, i1)
                        cagr, mdd, sh = fast_metrics(eq, a.rf[i0:i1], (a.idx[i1 - 1] - a.idx[i0]).days / 365.25)
                        grid_rows.append(dict(asset=name, period=pname, ma=n, buffer=b, mode=mode,
                                              cagr=cagr, mdd=mdd, sharpe=sh, trades=k))
    grid = pd.DataFrame(grid_rows)
    for name, (code, periods) in ASSETS.items():
        for pname, _, _ in periods:
            g = grid[(grid.asset == name) & (grid.period == pname)]
            for mode, mname in [("D", "매일"), ("W", "주1회")]:
                pv = g[g["mode"] == mode].pivot(index="ma", columns="buffer", values="sharpe")
                pv.columns = [f"{c * 100:g}%" for c in pv.columns]
                L.append(f"\n[{name} {pname} · {mname}] 샤프")
                L.append(pv.round(2).to_string())
            g200 = g[(g.ma == 200) & (g["mode"] == "D")].set_index("buffer")
            L.append(f"  200일선 매일: 버퍼별 CAGR " + ", ".join(f"{b * 100:g}% {pct(r.cagr)}" for b, r in g200.iterrows()))
            L.append(f"  200일선 매일: 버퍼별 MDD  " + ", ".join(f"{b * 100:g}% {pct(r.mdd)}" for b, r in g200.iterrows()))
            L.append(f"  200일선 매일: 버퍼별 매매 " + ", ".join(f"{b * 100:g}% {int(r.trades)}" for b, r in g200.iterrows()))

    # 3. 비대칭 버퍼
    L.append("\n" + "=" * 110 + "\n3. 비대칭 버퍼 (200일선, 매일). 행: 매수 문턱 +%, 열: 매도 문턱 -%, 값: 샤프\n" + "=" * 110)
    for name, (code, periods) in ASSETS.items():
        a = A[name]
        pname, start, end = periods[-1] if name in ("S&P500지수", "나스닥100지수") else periods[0]
        i0, i1 = a.span(start, end)
        m = pd.DataFrame(index=[f"+{u * 100:g}%" for u in ASYM], columns=[f"-{d * 100:g}%" for d in ASYM], dtype=float)
        for u in ASYM:
            for d in ASYM:
                eq, _ = run(a, a.pos(200, u, d, "D"), i0, i1)
                m.loc[f"+{u * 100:g}%", f"-{d * 100:g}%"] = fast_metrics(eq, a.rf[i0:i1], (a.idx[i1 - 1] - a.idx[i0]).days / 365.25)[2]
        L.append(f"\n[{name} {pname}]")
        L.append(m.round(2).to_string())

    # 4. 5년 롤링
    L.append("\n" + "=" * 110 + f"\n4. 5년 롤링 구간 (월초 시작). 기준 = {BASE_NAME}\n" + "=" * 110)
    for name, (code, periods) in ASSETS.items():
        pname = periods[0][0] if name in ("SPY", "QQQ") else periods[-1][0]
        curves, i0, i1 = long_curves[(name, pname)]
        rc = rolling_compare(A[name], curves, i0, i1)
        base = rc[rc.cand == BASE_NAME].set_index("start")
        rows = []
        for cand in curves:
            if cand == BASE_NAME:
                continue
            x = rc[rc.cand == cand].set_index("start")
            rows.append(dict(name=cand, n=len(x),
                             sh_win=(x.sharpe > base.sharpe).mean(), sh_diff=(x.sharpe - base.sharpe).median(),
                             cagr_win=(x.cagr > base.cagr).mean(), cagr_diff=(x.cagr - base.cagr).median(),
                             mdd_win=(x.mdd > base.mdd).mean(), mdd_diff=(x.mdd - base.mdd).median(),
                             worst_cagr=x.cagr.min()))
        L.append(f"\n[{name} {pname}] 5년 구간 {rows[0]['n']}개, 기준의 최악 5년 CAGR {pct(base.cagr.min())}")
        L.append(table(rows, [("name", "전략", None), ("sh_win", "샤프 우위", pct), ("sh_diff", "샤프차(중앙)", num),
                              ("cagr_win", "CAGR 우위", pct), ("cagr_diff", "CAGR차(중앙)", lambda x: pct(x, 2)),
                              ("mdd_win", "MDD 우위", pct), ("mdd_diff", "MDD차(중앙)", pct),
                              ("worst_cagr", "최악 5년 CAGR", pct)]))

    # 5. 워크포워드
    L.append("\n" + "=" * 110 + "\n5. 워크포워드 (200일선 매일, 버퍼 0에서 6% 중 직전까지 샤프 최고를 매년 선택)\n" + "=" * 110)
    for name, (code, periods) in ASSETS.items():
        a = A[name]
        pname, start, end = periods[0] if name in ("SPY", "QQQ") else periods[-1]
        i0, i1 = a.span(start, end)
        wf_pos, s0, picks = walk_forward(a, i0, i1)
        rows = []
        eq, k = run(a, wf_pos, s0, i1)
        m = metrics(a, eq, s0, i1)
        rows.append(dict(name="워크포워드", trades=k, **m))
        for cname, n, up, down, mode in CANDS[:6]:
            eq, k = run(a, a.pos(n, up, down, mode), s0, i1)
            rows.append(dict(name=cname, trades=k, **metrics(a, eq, s0, i1)))
        L.append(f"\n[{name} 적용 {a.idx[s0].date()}부터, 학습 시작 {a.idx[i0].date()}]")
        L.append(table(rows, [("name", "전략", None), ("cagr", "CAGR", pct), ("mdd", "MDD", pct),
                              ("sharpe", "샤프", num), ("trades", "매매", None)]))
        sel = picks.groupby((picks.buffer != picks.buffer.shift()).cumsum()).agg(
            start=("year", "first"), end=("year", "last"), buffer=("buffer", "first"))
        L.append("  선택된 버퍼: " + ", ".join(f"{r.start}-{r.end} {r.buffer * 100:g}%" for r in sel.itertuples()))
        for r in picks.itertuples():
            wf_rows.append(dict(asset=name, year=r.year, buffer=r.buffer, train_sharpe=r.train_sharpe))

    # 6. 비용 민감도
    L.append("\n" + "=" * 110 + "\n6. 비용 민감도 (편도 비용별 CAGR / 샤프)\n" + "=" * 110)
    for name in ["SPY", "QQQ"]:
        a = A[name]
        pname, start, end = ASSETS[name][1][0]
        i0, i1 = a.span(start, end)
        rows = []
        for cname, n, up, down, mode in CANDS:
            r = dict(name=cname)
            for cst in COSTS:
                eq, _ = run(a, a.pos(n, up, down, mode), i0, i1, cst)
                cg, _, sh = fast_metrics(eq, a.rf[i0:i1], (a.idx[i1 - 1] - a.idx[i0]).days / 365.25)
                r[f"c{cst}"] = f"{pct(cg)} / {sh:.2f}"
            rows.append(r)
        L.append(f"\n[{name} {pname}]")
        L.append(table(rows, [("name", "전략", None)] + [(f"c{c}", f"편도 {c * 100:g}%", None) for c in COSTS]))

    # 7. 매매 통계
    L.append("\n" + "=" * 110 + "\n7. 매매 통계 (왕복 기준, 비용 반영)\n" + "=" * 110)
    for name in ["SPY", "QQQ"]:
        a = A[name]
        for pname, start, end in [ASSETS[name][1][0], ASSETS[name][1][2]]:
            i0, i1 = a.span(start, end)
            rows = [dict(name=cname, **trade_stats(a, a.pos(n, up, down, mode), i0, i1))
                    for cname, n, up, down, mode in CANDS]
            L.append(f"\n[{name} {pname}]")
            L.append(table(rows, [("name", "전략", None), ("round_trips", "왕복", None), ("win_rate", "승률", pct),
                                  ("avg_win", "평균 이익", pct), ("avg_loss", "평균 손실", pct),
                                  ("best", "최대 이익", pct), ("worst", "최대 손실", pct),
                                  ("med_hold_days", "보유일(중앙)", lambda x: f"{x:.0f}"),
                                  ("max_loss_streak", "최다 연속 손실", None),
                                  ("short_trades", "30일 이내 청산", None)]))

    # 8. 세후
    L.append("\n" + "=" * 110 + "\n8. 세후 비교 (해외주식 직접 투자, 양도세 22%, 연 250만 원 공제, 원금 1억 원, 마지막 날 전량 매도)\n" + "=" * 110)
    for name in ["SPY", "QQQ"]:
        a = A[name]
        for pname, start, end in ASSETS[name][1]:
            i0, i1 = a.span(start, end)
            rows = []
            for cname, n, up, down, mode in [("단순 보유", 0, 0, 0, "H")] + CANDS:
                p = np.ones(len(a.idx), bool) if mode == "H" else a.pos(n, up, down, mode)
                eq, _ = run(a, p, i0, i1)
                pre = metrics(a, eq, i0, i1)["cagr"]
                t = after_tax(a, p, i0, i1)
                rows.append(dict(name=cname, pre=pre, post=t["after_tax_cagr"], drag=pre - t["after_tax_cagr"],
                                 final=t["after_tax_final"] * 1e4, paid=t["tax_paid"] * 1e4))
            L.append(f"\n[{name} {pname}]")
            L.append(table(rows, [("name", "전략", None), ("pre", "세전 CAGR", pct), ("post", "세후 CAGR", pct),
                                  ("drag", "세금 영향", lambda x: pct(x, 2)),
                                  ("final", "세후 최종(만원)", lambda x: f"{x:,.0f}"),
                                  ("paid", "낸 세금(만원)", lambda x: f"{x:,.0f}")]))

    # 9. 연도별 (SPY, QQQ 긴 구간)
    L.append("\n" + "=" * 110 + "\n9. 연도별 수익률\n" + "=" * 110)
    for name in ["SPY", "QQQ"]:
        curves, i0, i1 = long_curves[(name, ASSETS[name][1][0][0])]
        a = A[name]
        dates = a.idx[i0:i1]
        pick = ["단순 보유", BASE_NAME, "매일 버퍼0", "매일 ±3%", "매일 ±4%", "매일 ±5%"]
        yt = pd.DataFrame({k: bs.yearly(curves[k], dates) for k in pick})
        yt["±4% - 기본"] = yt["매일 ±4%"] - yt[BASE_NAME]
        L.append(f"\n[{name}]")
        L.append(yt.map(pct).to_string())

    os.makedirs(bs.RES_DIR, exist_ok=True)
    grid.to_csv(os.path.join(bs.RES_DIR, "buffer_grid.csv"), index=False, encoding="utf-8-sig")
    pd.DataFrame(wf_rows).to_csv(os.path.join(bs.RES_DIR, "buffer_walkforward.csv"), index=False, encoding="utf-8-sig")
    text = "\n".join(L)
    with open(os.path.join(bs.RES_DIR, "summary_buffer_validation.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
