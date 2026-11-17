"""
리밸런싱 주기와 해외주식 양도세 (kospi_box.py 의 데이터와 포트폴리오 재사용)
==========================================================================

포트폴리오: 한국 25 / 미국 25 / 미국 단기채(SGOV) 12.5 / 원화 CMA 12.5 / 금 25, 원화 기준, 2006-01부터
리밸런싱 주기: 안 함 / 연 1회(1월) / 연 2회(5월, 11월) / 분기(1, 4, 7, 10월) / 월 1회 / 밴드 ±5%p(매주 첫 거래일 확인)
  각 주기의 첫 거래일 종가로 목표 비중에 맞춤. 거래 비용 0.1%

양도세 (해외주식 직접 투자)
  - 과세 대상: 미국주식과 SGOV (SGOV도 미국 상장 ETF라 매도 시 환차익까지 과세). 두 자산의 손익은 같은 해 안에서 통산
  - 해마다 실현 차익에서 250만원 공제 후 22%. 다음 해 5월 마지막 거래일에 포트폴리오에서 비중대로 빼서 납부
  - 취득가는 평균단가(원화 기준). 한국 지수 ETF와 KRX 금현물은 매매차익 비과세
  - 반영하지 않은 것: 배당·분배금 과세, CMA 이자소득세 (주기와 관계없이 비슷하게 깎임), 환전 비용
  - 세후(보유): 아직 안 낸 세금만 뺀 평가액. 세후(전량 매도): 마지막 날 전부 팔아 미실현 차익까지 세금을 낸 금액

결과: results/summary_rebalance_tax.txt
"""

import os

import numpy as np
import pandas as pd

import backtest_stock_ma as bs
import kospi_box as kb
import validate_buffer as vb

TAX = 0.22
DEDUCTION = 2_500_000
TAXABLE = [1, 2]            # 미국, 단기채(SGOV)
RULES = [("안 함", "none"), ("연 1회 (1월)", "annual"), ("연 2회 (5월, 11월)", "semi"),
         ("분기 (1, 4, 7, 10월)", "quarter"), ("월 1회", "month"), ("밴드 ±5%p (매주 확인)", "band")]


def is_rebal_day(rule, dt, prev, week_first):
    if prev is None:
        return False
    new_month = dt.month != prev.month
    return {"none": False, "annual": dt.year != prev.year, "semi": new_month and dt.month in (5, 11),
            "quarter": new_month and dt.month in (1, 4, 7, 10), "month": new_month}.get(rule, False)


def simulate(g, start, end, rule, capital, w=kb.BASE_W, tax=True):
    G = g.loc[start:end].to_numpy()
    dates = g.loc[start:end].index
    week_first = bs.decision_days(g.index, 0)[g.index.get_indexer(dates)]
    vals = w * capital
    basis = vals.copy()
    realized = {}
    eq, paid_total, n_rebal, n_us_sell, us_sold = np.empty(len(dates)), 0.0, 0, 0, 0.0

    def sell(i, amount, year):
        """i 자산을 amount만큼 매도했을 때 실현손익 기록 (평균단가)"""
        if i in TAXABLE and vals[i] > 0:
            realized[year] = realized.get(year, 0.0) + amount - basis[i] * amount / vals[i]
            basis[i] -= basis[i] * amount / vals[i]

    for t in range(len(dates)):
        dt, prev = dates[t], dates[t - 1] if t > 0 else None
        if t > 0:
            vals = vals * G[t]
        # 세금 납부: 5월 마지막 거래일에 전년도 실현 차익분
        if tax and dt.month == 5 and (t + 1 == len(dates) or dates[t + 1].month != 5):
            due = TAX * max(0.0, realized.get(dt.year - 1, 0.0) - DEDUCTION)
            if due > 0:
                f = due / vals.sum()
                for i in range(len(vals)):
                    sell(i, vals[i] * f, dt.year)
                vals = vals * (1 - f)
                paid_total += due
        total = vals.sum()
        do = is_rebal_day(rule, dt, prev, week_first[t])
        if rule == "band" and week_first[t]:
            wt = vals / total
            do = any(abs(wt[ix].sum() - w[ix].sum()) > 0.05 for ix in kb.GROUPS.values())
        if do:
            trade = total * w - vals
            total -= np.abs(trade).sum() * kb.COST
            target = total * w
            for i in range(len(vals)):
                if target[i] < vals[i]:
                    sell(i, vals[i] - target[i], dt.year)
                    if i == 1:
                        n_us_sell += 1
                        us_sold += vals[i] - target[i]
                elif i in TAXABLE:
                    basis[i] += target[i] - vals[i]
            vals = target
            n_rebal += 1
        eq[t] = vals.sum()

    end_year = dates[-1].year
    unpaid = sum(TAX * max(0.0, realized.get(y, 0.0) - DEDUCTION) for y in (end_year - 1, end_year)
                 if not (y == end_year - 1 and dates[-1].month > 5))
    # 전량 매도: 올해 실현 차익에 미실현 차익을 더해 올해 세금을 다시 계산
    unreal = sum(vals[i] - basis[i] for i in TAXABLE)
    this_year = TAX * max(0.0, realized.get(end_year, 0.0) - DEDUCTION)
    liquid_tax = TAX * max(0.0, realized.get(end_year, 0.0) + unreal - DEDUCTION) - this_year
    years = (dates[-1] - dates[0]).days / 365.25
    s = pd.Series(eq / capital, dates)
    m = bs.metrics(s.to_numpy(), dates)
    me = s.resample("ME").last()
    hold_after = eq[-1] - unpaid
    liq_after = hold_after - liquid_tax
    return dict(cagr=(eq[-1] / capital) ** (1 / years) - 1,
                cagr_hold=(hold_after / capital) ** (1 / years) - 1,
                cagr_liq=(liq_after / capital) ** (1 / years) - 1,
                final=liq_after, paid=paid_total + unpaid + liquid_tax, mdd=m["mdd"],
                worst_12m=(me / me.shift(12) - 1).min(), n_rebal=n_rebal, n_us_sell=n_us_sell,
                us_sold=us_sold / years)


def main():
    d, g = kb.load()
    pct = vb.pct
    man = lambda x: f"{x / 1e4:,.0f}"
    L = ["리밸런싱 주기와 해외주식 양도세",
         f"데이터: {g.index[0].date()}부터 {g.index[-1].date()}까지. 포트폴리오 한국 25 / 미국 25 / SGOV 12.5 / CMA 12.5 / 금 25",
         "양도세: 미국주식과 SGOV 실현 차익 연 250만원 공제 후 22%, 다음 해 5월 납부, 평균단가. 한국 지수 ETF와 금현물은 비과세",
         "세후(보유): 아직 안 낸 세금만 뺀 평가액 기준. 세후(전량 매도): 마지막 날 전부 팔고 세금까지 낸 기준", ""]
    cols = [("name", "리밸런싱", None), ("cagr", "세전 CAGR", pct), ("cagr_hold", "세후(보유)", pct),
            ("cagr_liq", "세후(전량 매도)", pct), ("final", "세후 최종(만원)", man), ("paid", "세금 합계(만원)", man),
            ("mdd", "MDD", pct), ("worst_12m", "최악 12개월", pct), ("n_rebal", "리밸런싱 횟수", None),
            ("n_us_sell", "미국 매도", None), ("us_sold", "연평균 미국 매도(만원)", man)]
    for capital, label in [(60_000_000, "원금 6,000만원"), (300_000_000, "원금 3억원")]:
        start, end = kb.START, None
        L.append("=" * 120 + f"\n전체 기간, {label}\n" + "=" * 120)
        rows = []
        for n, r in RULES:
            x = simulate(g, start, end, r, capital)
            x["cagr"] = simulate(g, start, end, r, capital, tax=False)["cagr"]
            rows.append(dict(name=n, **x))
        L.append(vb.table(rows, cols))
        L.append("")
    L.append("=" * 120 + "\n구간별 세후(전량 매도) CAGR, 원금 6,000만원 (주기 순위가 구간마다 같은지)\n" + "=" * 120)
    periods = [("2006-2015", "2006-01-02", "2015-12-30"), ("2016-현재", "2016-01-04", None),
               ("박스피 2011-05~2016", "2011-05-02", "2016-12-29"), ("2021-06 고점부터", "2021-06-25", None)]
    rows = []
    for n, r in RULES:
        row = dict(name=n)
        for pn, s0, e0 in periods:
            x = simulate(g, s0, e0, r, 60_000_000)
            pre = simulate(g, s0, e0, r, 60_000_000, tax=False)["cagr"]
            row[pn] = f"{pct(x['cagr_liq'])} (세전 {pct(pre)})"
        rows.append(row)
    L.append(vb.table(rows, [("name", "리밸런싱", None)] + [(p[0], p[0], None) for p in periods]))
    text = "\n".join(L)
    with open(os.path.join(bs.RES_DIR, "summary_rebalance_tax.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
