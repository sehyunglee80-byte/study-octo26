"""
코스피 박스피 걱정: 비중 투자(리밸런싱)하면 박스권에 갇히는가
================================================================

질문: S&P500처럼 코스피도 정해진 비중으로 들고 가면, 박스피가 길게 이어질 때 갇히지 않을까

비교 (원화 기준, 2006-01부터)
  코스피200 단독 보유
  자산배분 포트폴리오: 한국주식 25 / 미국주식 25 / 현금 25 (미국 단기채 12.5 + 원화 CMA 12.5) / 금 25
    (투자지침 2.1의 자산배분 구조. 비트코인은 2006년 데이터가 없어 그 몫을 금에 넣음)
    리밸런싱: 안 함 / 연 1회(1월) / 연 2회(5월, 11월) / 밴드(어느 자산이든 목표에서 ±5%p 벗어나면)
    참고: 연 1회 + 한국주식에만 200일선 필터 / 한국 비중 축소(한국 12.5, 미국 37.5)

데이터와 가정
  한국주식: 코스피200 지수(네이버) + 배당 연 1.71%
    (2009-05부터 2026-11까지 KODEX200 배당 포함 수익률과 코스피200 가격 지수의 연 수익률 차이를 실측한 값)
  미국주식: SPY 배당 포함 수정주가 x 원달러 환율 / 미국 단기채: 미국 13주 국채 금리 x 환율
  원화 CMA: 연 2.5% 고정 (가정, 실제 금리 데이터 없음) / 금: 금 선물 근월물(GC=F) x 환율
  리밸런싱은 그날 종가로, 거래 금액의 0.1%를 비용으로 차감. 세금과 환전 비용은 반영하지 않음

결과: results/summary_kospi_box.txt
"""

import os

import numpy as np
import pandas as pd

import backtest_stock_ma as bs
import validate_buffer as vb

DIV_KR = 0.0171
CMA = 0.025
COST = 0.001
START = "2006-01-02"
ASSETS = ["한국", "미국", "단기채", "CMA", "금"]
GROUPS = {"한국": [0], "미국": [1], "현금": [2, 3], "금": [4]}
BASE_W = np.array([0.25, 0.25, 0.125, 0.125, 0.25])
LOW_KR_W = np.array([0.125, 0.375, 0.125, 0.125, 0.25])
PERIODS = [
    ("전체", START, None),
    ("박스피 (2011-05 코스피 고점부터 2016년 말까지)", "2011-05-02", "2016-12-29"),
    ("최악의 시작 (2007-10 고점부터 2020-11까지)", "2007-10-31", "2020-11-30"),
    ("2021-06 고점부터 2025-06까지", "2021-06-25", "2025-06-30"),
    ("2026-06 고점부터 현재까지", "2026-06-22", None),
]


def load():
    bs.ASSETS["코스피200"] = ("naver_index", "KPI200", 0.00065, 0.00065)
    k2 = bs.load("코스피200")
    fx = vb.load_long("KRW=X")["close"]
    spy = vb.load_long("SPY")["close"]
    gold = vb.load_long("GC=F")["close"]
    irx = vb.load_long("^IRX")["close"]
    idx = k2.index[k2.index >= pd.Timestamp("2005-01-01")]
    f = lambda s: s.reindex(s.index.union(idx)).ffill().reindex(idx)
    d = pd.DataFrame({"k200": k2["close"].reindex(idx), "fx": f(fx), "spy": f(spy), "gold": f(gold),
                      "irx": f(irx)})
    d["ma200"] = d["k200"].rolling(200).mean()
    days = d.index.to_series().diff().dt.days.fillna(1).to_numpy()
    g = pd.DataFrame(index=d.index)
    g["한국"] = d["k200"] / d["k200"].shift(1) * (1 + DIV_KR) ** (days / 365.25)
    g["미국"] = (d["spy"] * d["fx"]) / (d["spy"] * d["fx"]).shift(1)
    g["단기채"] = (1 + d["irx"].shift(1) / 100) ** (days / 365.25) * d["fx"] / d["fx"].shift(1)
    g["CMA"] = (1 + CMA) ** (days / 365.25)
    g["금"] = (d["gold"] * d["fx"]) / (d["gold"] * d["fx"]).shift(1)
    return d, g.fillna(1.0)


def simulate(d, g, start, end, rule, w=BASE_W, trend=False):
    """rule: none / annual / semi / band. 반환: 자산곡선, 한국주식 매매 기록, 한국 몫 가치 기록"""
    sl = slice(pd.Timestamp(start), pd.Timestamp(end) if end else None)
    G = g.loc[sl].to_numpy()
    dates = g.loc[sl].index
    k200 = d["k200"].loc[sl].to_numpy()
    above = (d["k200"] > d["ma200"]).shift(1).fillna(True).loc[sl].to_numpy()
    first_week = bs.decision_days(d.index, 0)[d.index.get_indexer(dates)]
    vals = w.copy()
    eq, kr_val, trades = np.empty(len(dates)), np.empty(len(dates)), []
    tgt = w.copy()
    for t in range(len(dates)):
        if t > 0:
            vals = vals * G[t]
        total = vals.sum()
        new_tgt = tgt
        if trend and first_week[t]:
            new_tgt = w.copy()
            if not above[t]:
                new_tgt = w.copy()
                new_tgt[3] += new_tgt[0]
                new_tgt[0] = 0.0
        dt = dates[t]
        prev = dates[t - 1] if t > 0 else None
        cal = {"annual": prev is not None and dt.year != prev.year,
               "semi": prev is not None and dt.month != prev.month and dt.month in (5, 11),
               "none": False}.get(rule, False)
        bandhit = False
        if rule == "band":
            wt = vals / total
            bandhit = any(abs(wt[ix].sum() - new_tgt[ix].sum()) > 0.05 for ix in GROUPS.values())
        if cal or bandhit or (new_tgt is not tgt and not np.allclose(new_tgt, tgt)):
            tgt = new_tgt
            trade = total * tgt - vals
            total -= np.abs(trade).sum() * COST
            new_vals = total * tgt
            if abs(new_vals[0] - vals[0]) > 1e-9:
                trades.append(dict(date=dt, level=k200[t], amount=new_vals[0] - vals[0]))
            vals = new_vals
        tgt = new_tgt
        eq[t] = vals.sum()
        kr_val[t] = vals[0]
    return pd.Series(eq, dates), pd.DataFrame(trades), pd.Series(kr_val, dates)


def irr(flows):
    """flows: (날짜, 금액) 리스트. 투자자 기준 (넣으면 음수, 빼면 양수). 연 수익률"""
    t0 = flows[0][0]
    yrs = np.array([(dt - t0).days / 365.25 for dt, _ in flows])
    amt = np.array([a for _, a in flows])
    npv = lambda r: (amt / (1 + r) ** yrs).sum()
    lo, hi = -0.9, 1.0
    if npv(lo) * npv(hi) > 0:
        return np.nan
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def stats(s, krv=None, trades=None, w0=0.25):
    eq = s.to_numpy() / s.iloc[0]
    years = (s.index[-1] - s.index[0]).days / 365.25
    m = bs.metrics(eq, s.index)
    r = np.diff(eq) / eq[:-1]
    out = dict(cagr=(eq[-1]) ** (1 / years) - 1, mdd=m["mdd"], vol=r.std() * np.sqrt(bs.ANN),
               worst_month=m["worst_month"], recovery=f"{m['recovery_days']}" + ("+" if m["recovery_ongoing"] else ""))
    me = s.resample("ME").last()
    out["worst_12m"] = (me / me.shift(12) - 1).min()
    if krv is not None:
        flows = [(s.index[0], -w0 * s.iloc[0])]
        for tr in (trades.itertuples() if len(trades) else []):
            flows.append((tr.date, -tr.amount))
        flows.append((s.index[-1], krv.iloc[-1]))
        out["kr_irr"] = irr(flows)
        if len(trades):
            b, sd = trades[trades.amount > 0], trades[trades.amount < 0]
            out["buy_lv"] = (b.level * b.amount).sum() / b.amount.sum() if len(b) else np.nan
            out["sell_lv"] = (sd.level * sd.amount).sum() / sd.amount.sum() if len(sd) else np.nan
            out["n_tr"] = len(trades)
    return out


def main():
    d, g = load()
    pct, num = vb.pct, vb.num
    L = ["코스피 박스피 걱정: 비중 투자(리밸런싱)하면 박스권에 갇히는가",
         f"데이터: {d.index[0].date()}부터 {d.index[-1].date()}까지 (한국 거래일 기준, 해외 자산은 직전 값)",
         "포트폴리오: 한국 25 / 미국 25 / 미국 단기채 12.5 / 원화 CMA 12.5 / 금 25. 원화 기준",
         "한국 몫 실효 수익률: 리밸런싱으로 한국주식에 넣고 뺀 돈까지 감안한 연 수익률 (돈 가중 수익률)",
         "매수 평균/매도 평균: 리밸런싱으로 한국주식을 살 때와 팔 때의 코스피200 평균 지수 (금액 가중)", ""]

    # 1. 박스피 역사
    L.append("=" * 110 + "\n1. 코스피 고점에서 샀다면 (코스피 지수, 배당 미반영, 종가 기준)\n" + "=" * 110)
    kospi = bs.load("코스피")["close"]
    ath = kospi.cummax()
    dd = kospi / ath - 1
    rows, i, n, ks = [], 0, len(kospi), kospi.to_numpy()
    ddv = dd.to_numpy()
    while i < n:
        if ddv[i] < 0:
            s = i
            while i < n and ddv[i] < 0:
                i += 1
            seg = ddv[s:i]
            if seg.min() <= -0.20:
                t = s + int(np.argmin(seg))
                rows.append(dict(peak=kospi.index[s - 1].date(), peak_lv=ks[s - 1], trough=kospi.index[t].date(),
                                 trough_lv=ks[t], mdd=seg.min(),
                                 back=kospi.index[i].date() if i < n else "회복 전",
                                 years=((kospi.index[i] if i < n else kospi.index[-1]) - kospi.index[s - 1]).days / 365.25))
        else:
            i += 1
    L.append(vb.table(rows, [("peak", "고점일", None), ("peak_lv", "고점", lambda x: f"{x:,.0f}"),
                             ("trough", "바닥일", None), ("trough_lv", "바닥", lambda x: f"{x:,.0f}"),
                             ("mdd", "최대하락", pct), ("back", "고점 회복일", None),
                             ("years", "갇힌 기간(년)", lambda x: f"{x:.1f}")]))
    me = kospi.resample("ME").last()
    for yrs in (5, 10):
        r = (me / me.shift(12 * yrs)) ** (1 / yrs) - 1
        r = r[r.index >= pd.Timestamp("2000-01-01")].dropna()
        L.append(f"  {yrs}년 보유 수익률(월말 기준, 2000년 이후 끝나는 구간 {len(r)}개): 마이너스 {(r < 0).mean():.0%}, "
                 f"연 3% 미만 {(r < 0.03).mean():.0%}, 중앙값 연 {r.median():.1%}")

    # 2. 포트폴리오 비교
    variants = [("코스피200 단독 보유", None), ("포트폴리오 리밸런싱 안 함", ("none", BASE_W, False)),
                ("포트폴리오 연 1회(1월)", ("annual", BASE_W, False)),
                ("포트폴리오 연 2회(5월, 11월)", ("semi", BASE_W, False)),
                ("포트폴리오 밴드 ±5%p", ("band", BASE_W, False)),
                ("(참고) 연 1회 + 한국만 200일선", ("annual", BASE_W, True)),
                ("(참고) 연 1회, 한국 12.5 미국 37.5", ("annual", LOW_KR_W, False))]
    L.append("\n" + "=" * 110 + "\n2. 기간별 비교\n" + "=" * 110)
    for pname, start, end in PERIODS:
        rows = []
        for vname, spec in variants:
            if spec is None:
                kr = g["한국"].loc[start:end].cumprod()
                rows.append(dict(name=vname, **stats(kr)))
            else:
                rule, w, trend = spec
                s, trades, krv = simulate(d, g, start, end, rule, w, trend)
                rows.append(dict(name=vname, **stats(s, krv, trades, w[0])))
        k = d["k200"].loc[start:end]
        L.append(f"\n[{pname}] {k.index[0].date()}부터 {k.index[-1].date()}까지 · 코스피200 {k.iloc[0]:,.1f} -> {k.iloc[-1]:,.1f}")
        L.append(vb.table(rows, [("name", "전략", None), ("cagr", "CAGR", pct), ("vol", "변동성", pct),
                                 ("mdd", "MDD", pct), ("worst_12m", "최악 12개월", pct), ("recovery", "회복일", None),
                                 ("kr_irr", "한국 몫 실효", pct), ("n_tr", "한국 매매", lambda x: "" if pd.isna(x) else f"{x:.0f}"),
                                 ("buy_lv", "매수 평균", lambda x: "" if pd.isna(x) else f"{x:,.0f}"),
                                 ("sell_lv", "매도 평균", lambda x: "" if pd.isna(x) else f"{x:,.0f}")]))

    # 3. 박스피 기간 리밸런싱 매매 기록 (밴드)
    pname, start, end = PERIODS[1]
    for rule, label in [("annual", "연 1회"), ("band", "밴드 ±5%p")]:
        _, trades, _ = simulate(d, g, start, end, rule)
        L.append(f"\n[박스피 기간 한국주식 리밸런싱 기록: {label}] (금액은 포트폴리오 시작 금액 대비 %)")
        L.append(vb.table(trades.to_dict("records"),
                          [("date", "날짜", lambda x: str(x.date())), ("level", "코스피200", lambda x: f"{x:,.1f}"),
                           ("amount", "매수(+)/매도(-)", lambda x: f"{x * 100:+.1f}%")]))

    # 4. 현재
    last = d.iloc[-1]
    kp = kospi
    L.append("\n" + "=" * 110 + f"\n3. 현재 ({d.index[-1].date()} 종가)\n" + "=" * 110)
    L.append(f"  코스피 {kp.iloc[-1]:,.2f} · 사상 최고 {kp.max():,.2f} ({kp.idxmax().date()}) · 고점 대비 {pct(kp.iloc[-1] / kp.max() - 1)}")
    L.append(f"  코스피200 {last.k200:,.2f} · 200일선 {last.ma200:,.2f} (이격 {pct(last.k200 / last.ma200 - 1)})")

    text = "\n".join(L)
    with open(os.path.join(bs.RES_DIR, "summary_kospi_box.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
