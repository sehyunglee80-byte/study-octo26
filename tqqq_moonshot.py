"""
투더문 10%를 TQQQ로 하면 (투자지침 2026-11-17 목표 포트폴리오 기준)
==================================================================

TQQQ: 나스닥100 일간 수익률의 3배 ETF (2010-02 상장). 그 전은 가상 TQQQ로 계산
  가상 TQQQ 일간 수익률 = 3 x 기초 수익률 - 2 x 단기금리 - 연 1.95%
  기초: QQQ 배당 포함 수정주가 (1999-03 이전은 나스닥100 가격 지수)
  연 1.95%는 실제 TQQQ와 맞춘 값 (2010-02부터 실제 연 37.2%, 가상 37.1%, 일간 상관 0.999)

1. TQQQ 단독 (달러): QQQ 보유 / TQQQ 보유 / TQQQ + 200일선
   200일선 규칙: 매주 첫 거래일, 전일 나스닥100 종가가 200일선 위면 TQQQ, 아래면 미국 단기채
2. 목표 포트폴리오 안의 투더문 10% 자리 (원화, 2004년부터 / 2010년부터)
   VOO 35 / QQQM 15 / SGOV 25 / 투더문 자리 10 / 비트코인 자리 10 / 금 5
   투더문 자리: CMA(투더문 안 함) / QQQM / TQQQ 보유 / TQQQ + 200일선 (현금일 때는 SGOV)
   비트코인 자리는 모든 경우 원화 현금으로 둠 (비트코인 효과를 빼고 투더문 차이만 보기 위해)
   매년 11월 첫 거래일 목표 비중으로 리밸런싱. 바이 더 딥 규칙(3.9)은 반영하지 않음
   양도세: 미국 상장 ETF(VOO, QQQM, SGOV, TQQQ) 실현 차익 연 250만원 공제 후 22%, 다음 해 5월 납부,
   평균단가, 원금 5억원. 배당·이자 과세와 환전 비용은 미반영. 거래 비용 0.1%
   원화 CMA 연 2.5% 가정. VOO는 SPY, QQQM은 QQQ, SGOV는 미국 13주 국채 금리, 금은 금 선물(GC=F)로 대신함
3. 닷컴 붕괴 포함 (달러, 2000-09부터, 세금 없음): 위 포트폴리오를 달러로

결과: results/summary_tqqq_moonshot.txt
"""

import os

import numpy as np
import pandas as pd

import backtest_stock_ma as bs
import validate_buffer as vb

FEE = 0.0195
CMA = 0.025
COST = 0.001
TAX, DEDUCTION, CAPITAL = 0.22, 2_500_000, 500_000_000
NAMES = ["VOO", "QQQM", "SGOV", "슬롯", "슬롯현금", "원화현금", "금"]
TAXABLE = [0, 1, 2, 3, 4]       # 미국 상장 ETF (슬롯이 CMA일 때는 과세 안 함, 아래에서 처리)


def load():
    q = vb.load_long("QQQ")["close"]
    n = vb.load_long("^NDX")["close"]
    spy = vb.load_long("SPY")["close"]
    gold = vb.load_long("GC=F")["close"]
    fx = vb.load_long("KRW=X")["close"]
    irx = vb.load_long("^IRX")["close"]
    idx = n.index
    ff = lambda s: s.reindex(s.index.union(idx)).ffill().reindex(idx)
    d = pd.DataFrame(index=idx)
    rq = q.reindex(idx).pct_change()
    d["r_u"] = rq.where(q.reindex(idx).notna() & q.reindex(idx).shift(1).notna(), n.pct_change())
    d["rf"] = (ff(irx).fillna(0) / 100 / bs.ANN).shift(1).fillna(0)
    d["r_tq"] = 3 * d["r_u"] - 2 * d["rf"] - FEE / bs.ANN
    d["r_spy"] = ff(spy).pct_change()
    d["r_gold"] = ff(gold).pct_change()
    d["fx"] = ff(fx)
    d["ndx"] = n
    d["ma200"] = n.rolling(200).mean()
    d["days"] = idx.to_series().diff().dt.days.fillna(1).to_numpy()
    d["signal"] = bs.positions((n > d["ma200"]).to_numpy(), bs.decision_days(idx, 0))
    return d


def sleeve(d, start, end=None):
    x = d.loc[start:end]
    out = {}
    for name, r in [("QQQ 보유 (1배)", x["r_u"]), ("TQQQ 보유", x["r_tq"]),
                    ("TQQQ + 200일선", np.where(x["signal"], x["r_tq"], x["rf"]))]:
        r = pd.Series(r, x.index).fillna(0).copy()
        r.iloc[0] = 0
        eq = (1 + r).cumprod()
        m = bs.metrics(eq.to_numpy(), eq.index)
        ye = eq.resample("YE").last()
        yr = ye / ye.shift(1).fillna(1) - 1
        yrs = (eq.index[-1] - eq.index[0]).days / 365.25
        out[name] = dict(name=name, cagr=eq.iloc[-1] ** (1 / yrs) - 1, mdd=m["mdd"], low=eq.min() - 1,
                         worst_year=f"{yr.idxmin().year} {vb.pct(yr.min())}",
                         recovery=f"{m['recovery_days']}" + ("+" if m["recovery_ongoing"] else ""),
                         final=eq.iloc[-1])
    return list(out.values())


def portfolio(d, start, end, slot, krw=True, tax=True):
    """slot: cma / qqqm / tqqq / tqqq_ma"""
    x = d.loc[start:end]
    dates = x.index
    fxg = (x["fx"] / x["fx"].shift(1)).fillna(1).to_numpy() if krw else np.ones(len(x))
    cash_krw = (1 + CMA) ** (x["days"].to_numpy() / 365.25) if krw else 1 + x["rf"].to_numpy()
    r_slot = {"cma": None, "qqqm": x["r_u"], "tqqq": x["r_tq"], "tqqq_ma": x["r_tq"]}[slot]
    G = np.column_stack([
        (1 + x["r_spy"].fillna(0)).to_numpy() * fxg,
        (1 + x["r_u"].fillna(0)).to_numpy() * fxg,
        (1 + x["rf"]).to_numpy() * fxg,
        cash_krw if slot == "cma" else (1 + r_slot.fillna(0)).to_numpy() * fxg,
        (1 + x["rf"]).to_numpy() * fxg,
        cash_krw,
        (1 + x["r_gold"].fillna(0)).to_numpy() * fxg,
    ])
    G[0] = 1
    taxable = [i for i in TAXABLE if not (slot == "cma" and i == 3)] if krw and tax else []
    sig = x["signal"].to_numpy()
    week_first = bs.decision_days(d.index, 0)[d.index.get_indexer(dates)]

    def target(on):
        w = np.array([0.35, 0.15, 0.25, 0.10, 0.0, 0.10, 0.05])
        if slot == "tqqq_ma" and not on:
            w[3], w[4] = 0.0, 0.10
        return w

    vals = target(sig[0]) * CAPITAL
    basis = vals.copy()
    realized = {}
    eq = np.empty(len(dates))
    slot_ref, slot_low = vals[3] + vals[4], 0.0

    def sell(i, amt, year):
        if i in taxable and vals[i] > 0:
            realized[year] = realized.get(year, 0.0) + amt - basis[i] * amt / vals[i]
            basis[i] -= basis[i] * amt / vals[i]

    def rebalance(w, year):
        nonlocal vals
        total = vals.sum()
        total -= np.abs(total * w - vals).sum() * COST
        new = total * w
        for i in range(len(vals)):
            if new[i] < vals[i]:
                sell(i, vals[i] - new[i], year)
            elif i in taxable:
                basis[i] += new[i] - vals[i]
        vals = new

    for t in range(len(dates)):
        dt = dates[t]
        if t > 0:
            vals = vals * G[t]
        if taxable and dt.month == 5 and (t + 1 == len(dates) or dates[t + 1].month != 5):
            due = TAX * max(0.0, realized.get(dt.year - 1, 0.0) - DEDUCTION)
            if due > 0:
                f = due / vals.sum()
                for i in range(len(vals)):
                    sell(i, vals[i] * f, dt.year)
                vals = vals * (1 - f)
        nov = t > 0 and dt.month == 11 and dates[t - 1].month != 11
        if nov:
            rebalance(target(sig[t]), dt.year)
            slot_ref = vals[3] + vals[4]
        elif slot == "tqqq_ma" and week_first[t] and t > 0:
            on = sig[t]
            if (on and vals[3] == 0) or (not on and vals[4] == 0):
                total_slot = vals[3] + vals[4]
                src, dst = (4, 3) if on else (3, 4)
                amt = vals[src]
                sell(src, amt, dt.year)
                amt *= 1 - COST
                vals[src] = 0.0
                vals[dst] += amt
                if dst in taxable:
                    basis[dst] += amt
        slot_low = min(slot_low, (vals[3] + vals[4]) / slot_ref - 1)
        eq[t] = vals.sum()
    s = pd.Series(eq / CAPITAL, dates)
    m = bs.metrics(s.to_numpy(), dates)
    me = s.resample("ME").last()
    years = (dates[-1] - dates[0]).days / 365.25
    unreal = sum(vals[i] - basis[i] for i in taxable)
    ey = dates[-1].year
    unpaid = sum(TAX * max(0.0, realized.get(y, 0.0) - DEDUCTION) for y in (ey - 1, ey)
                 if not (y == ey - 1 and dates[-1].month > 5)) if taxable else 0.0
    liq_extra = (TAX * max(0.0, realized.get(ey, 0.0) + unreal - DEDUCTION)
                 - TAX * max(0.0, realized.get(ey, 0.0) - DEDUCTION)) if taxable else 0.0
    after = eq[-1] - unpaid - liq_extra
    return dict(cagr=(eq[-1] / CAPITAL) ** (1 / years) - 1, cagr_after=(after / CAPITAL) ** (1 / years) - 1,
                mdd=m["mdd"], worst_12m=(me / me.shift(12) - 1).min(),
                recovery=f"{m['recovery_days']}" + ("+" if m["recovery_ongoing"] else ""), slot_low=slot_low)


SLOTS = [("투더문 안 함 (CMA 10%)", "cma"), ("QQQM 10% (1배)", "qqqm"), ("TQQQ 10% 보유", "tqqq"),
         ("TQQQ 10% + 200일선", "tqqq_ma")]


def main():
    d = load()
    pct = vb.pct
    L = ["투더문 10%를 TQQQ로 하면",
         f"데이터: 나스닥100 {d.index[0].date()}부터 {d.index[-1].date()}까지. 1999-03 이전 기초는 나스닥100 가격 지수, 2010-02 이전 TQQQ는 가상",
         "가상 TQQQ = 3 x 기초 일간 수익률 - 2 x 단기금리 - 연 1.95% (실제 TQQQ와 맞춘 값)",
         "200일선 규칙: 매주 첫 거래일, 전일 나스닥100 종가가 200일선 위면 TQQQ, 아래면 미국 단기채", ""]

    L.append("=" * 110 + "\n1. TQQQ 단독 (달러, 세금 없음). 바닥: 시작 금액 대비 가장 낮았을 때\n" + "=" * 110)
    for start in ["1987-01-02", "2000-01-03", "2010-02-11", "2022-01-03"]:
        rows = sleeve(d, start)
        L.append(f"\n[{start[:4]}년 시작]")
        L.append(vb.table(rows, [("name", "전략", None), ("cagr", "CAGR", pct), ("mdd", "MDD", pct),
                                 ("low", "바닥", pct), ("worst_year", "최악의 해", None), ("recovery", "회복일", None),
                                 ("final", "최종 배수", lambda x: f"{x:,.1f}배")]))

    cols = [("name", "투더문 자리 10%", None), ("cagr", "세전 CAGR", pct), ("cagr_after", "세후 CAGR", pct),
            ("mdd", "MDD", pct), ("worst_12m", "최악 12개월", pct), ("recovery", "회복일", None),
            ("slot_low", "투더문 몫 최대 손실", pct)]
    L.append("\n" + "=" * 110 + "\n2. 목표 포트폴리오 안에서 (원화, 원금 5억원, 매년 11월 리밸런싱, 양도세 반영)\n" + "=" * 110)
    L.append("  투더문 몫 최대 손실: 리밸런싱으로 10%에 맞춘 뒤 다음 리밸런싱 전까지 그 몫이 가장 많이 줄어든 비율")
    for start in ["2004-01-02", "2010-02-11", "2022-01-03"]:
        rows = [dict(name=n, **portfolio(d, start, None, s)) for n, s in SLOTS]
        L.append(f"\n[{start[:4]}년 시작]")
        L.append(vb.table(rows, cols))

    L.append("\n" + "=" * 110 + "\n3. 닷컴 붕괴 포함 (달러, 2000-09부터, 세금 없음, 원화 현금 자리는 미국 단기채)\n" + "=" * 110)
    rows = [dict(name=n, **portfolio(d, "2000-09-01", None, s, krw=False)) for n, s in SLOTS]
    L.append(vb.table(rows, [c for c in cols if c[0] != "cagr_after"]))
    rows = [dict(name=n, **portfolio(d, "2000-09-01", "2009-12-31", s, krw=False)) for n, s in SLOTS]
    L.append("\n[2000-09부터 2009년 말까지]")
    L.append(vb.table(rows, [c for c in cols if c[0] != "cagr_after"]))

    text = "\n".join(L)
    with open(os.path.join(bs.RES_DIR, "summary_tqqq_moonshot.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
