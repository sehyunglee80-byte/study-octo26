"""
바이 더 딥을 나스닥100 + TQQQ로 하면 (투자지침 3.9 변형, tqqq_moonshot.py 데이터 재사용)
=====================================================================================

포트폴리오 (투자지침 2026-11-17): VOO 35 / QQQM 15 / SGOV 25 / 원화 현금 10 / 비트코인 자리 10 / 금 5
  투더문 자리(원화 현금 10%)와 비트코인 자리는 비교를 위해 원화 현금으로 둔다 (바이 더 딥 차이만 보기 위해)

바이 더 딥 (지침 3.9 방식)
  - 기준 지수가 고점 대비 각 문턱에 닿으면 다음 거래일 종가에 SGOV 목표치(25%)의 1/3씩 딥 자산 매수
    (차수당 그날 포트폴리오의 8.3%, SGOV가 모자라면 남은 만큼)
  - 기준 지수가 고점을 다시 넘으면 다음 거래일 종가에 목표 비중으로 리밸런싱 (딥 자산 전량 매도)
  - 진행 중에는 11월 정기 리밸런싱을 건너뛴다
  - 고점은 시뮬레이션 시작 이후 최고 종가 (시작일을 지수 고점 근처로 잡음)
  안: A 지침 그대로 (S&P500 -10/-20/-30% → VOO) / B 나스닥100 -10/-20/-30% → QQQM
      C 나스닥100 -10/-20/-30% → TQQQ / D 나스닥100 -15/-30/-45% → TQQQ / E 바이 더 딥 없음
      F S&P500 -10/-20%는 VOO, -30%만 TQQQ / G 나스닥100 -10/-20%는 QQQM, -30%만 TQQQ (3차만 레버리지)

공통: 매년 11월 첫 거래일 리밸런싱, 거래 비용 0.1%, 원금 5억원.
원화 구간은 해외주식 양도세(연 250만원 공제 후 22%, 다음 해 5월, 평균단가) 반영, 달러 구간은 세금 없음.
TQQQ는 2010-02 이전 가상 (3 x 나스닥100 - 2 x 단기금리 - 연 1.95%). 금은 2000-08 이전 수익 0으로 둠.

결과: results/summary_dip_tqqq.txt
"""

import os

import numpy as np
import pandas as pd

import backtest_stock_ma as bs
import tqqq_moonshot as tm
import validate_buffer as vb

W = np.array([0.35, 0.15, 0.25, 0.20, 0.05, 0.0, 0.0])  # VOO, QQQM, SGOV, 원화현금(투더문+비트코인 자리), 금, 딥 1·2차, 딥 3차
TAXABLE = [0, 1, 2, 5, 6]
PLANS = [
    ("A 지침: S&P500 -10/-20/-30% → VOO", "spx", (0.10, 0.20, 0.30), ("voo", "voo")),
    ("B 나스닥100 -10/-20/-30% → QQQM", "ndx", (0.10, 0.20, 0.30), ("qqqm", "qqqm")),
    ("C 나스닥100 -10/-20/-30% → TQQQ", "ndx", (0.10, 0.20, 0.30), ("tqqq", "tqqq")),
    ("D 나스닥100 -15/-30/-45% → TQQQ", "ndx", (0.15, 0.30, 0.45), ("tqqq", "tqqq")),
    ("E 바이 더 딥 없음", None, (), (None, None)),
    ("F S&P500 -10/-20% VOO, -30% TQQQ", "spx", (0.10, 0.20, 0.30), ("voo", "tqqq")),
    ("G 나스닥100 -10/-20% QQQM, -30% TQQQ", "ndx", (0.10, 0.20, 0.30), ("qqqm", "tqqq")),
]


def load():
    d = tm.load()
    d["spx"] = vb.load_long("^GSPC")["close"].reindex(d.index).ffill()
    return d


def simulate(d, start, end, plan, krw=True):
    _, base, ths, assets = plan
    x = d.loc[start:end]
    dates = x.index
    fxg = (x["fx"] / x["fx"].shift(1)).fillna(1).to_numpy() if krw else np.ones(len(x))
    cash = (1 + tm.CMA) ** (x["days"].to_numpy() / 365.25) if krw else 1 + x["rf"].to_numpy()
    rmap = {"voo": x["r_spy"], "qqqm": x["r_u"], "tqqq": x["r_tq"], None: x["rf"]}
    r_dip1, r_dip3 = (rmap[a].fillna(0).to_numpy() for a in assets)
    G = np.column_stack([(1 + x["r_spy"].fillna(0)).to_numpy() * fxg, (1 + x["r_u"].fillna(0)).to_numpy() * fxg,
                         (1 + x["rf"]).to_numpy() * fxg, cash, (1 + x["r_gold"].fillna(0)).to_numpy() * fxg,
                         (1 + r_dip1) * fxg, (1 + r_dip3) * fxg])
    G[0] = 1
    level = x[base].to_numpy() if base else None
    taxable = TAXABLE if krw else []
    vals = W * tm.CAPITAL
    basis = vals.copy()
    realized, eq = {}, np.empty(len(dates))
    peak, done, pending_buy, pending_exit = (level[0] if base else 0), 0, False, False
    episodes, ep = [], None

    def sell(i, amt, year):
        if i in taxable and vals[i] > 0:
            realized[year] = realized.get(year, 0.0) + amt - basis[i] * amt / vals[i]
            basis[i] -= basis[i] * amt / vals[i]

    def rebalance(year):
        nonlocal vals
        total = vals.sum()
        total -= np.abs(total * W - vals).sum() * tm.COST
        new = total * W
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
            due = tm.TAX * max(0.0, realized.get(dt.year - 1, 0.0) - tm.DEDUCTION)
            if due > 0:
                f = due / vals.sum()
                for i in range(len(vals)):
                    sell(i, vals[i] * f, dt.year)
                vals = vals * (1 - f)
        if ep is not None:
            ep["low"] = min(ep["low"], (vals[5] + vals[6]) / ep["cost"] - 1) if ep["cost"] > 0 else 0
        # 전날 신호 실행
        if pending_exit:
            ep.update(end=dt.date(), ret=(vals[5] + vals[6]) / ep["cost"] - 1,
                      ret3=vals[6] / ep["cost3"] - 1 if ep["cost3"] else np.nan)
            episodes.append(ep)
            ep, done, pending_exit = None, 0, False
            rebalance(dt.year)
        elif pending_buy:
            amt = min(vals[2], 0.25 / 3 * vals.sum())
            sell(2, amt, dt.year)
            vals[2] -= amt
            amt *= 1 - tm.COST
            col = 6 if done == 2 else 5
            vals[col] += amt
            if col in taxable:
                basis[col] += amt
            if ep is None:
                ep = dict(start=dt.date(), cost=0.0, cost3=0.0, low=0.0, buys=[])
            ep["cost"] += amt
            if col == 6:
                ep["cost3"] += amt
            ep["buys"].append(str(dt.date()))
            done += 1
            pending_buy = False
        # 11월 정기 리밸런싱 (진행 중이면 건너뜀)
        if t > 0 and dt.month == 11 and dates[t - 1].month != 11 and done == 0:
            rebalance(dt.year)
        # 오늘 종가로 신호 판단
        if base:
            if level[t] > peak:
                peak = level[t]
                if done > 0:
                    pending_exit = True
            elif done < len(ths) and level[t] / peak - 1 <= -ths[done]:
                pending_buy = True
        eq[t] = vals.sum()

    if ep is not None:
        ep.update(end="진행 중", ret=(vals[5] + vals[6]) / ep["cost"] - 1,
                  ret3=vals[6] / ep["cost3"] - 1 if ep["cost3"] else np.nan)
        episodes.append(ep)
    s = pd.Series(eq / tm.CAPITAL, dates)
    m = bs.metrics(s.to_numpy(), dates)
    me = s.resample("ME").last()
    years = (dates[-1] - dates[0]).days / 365.25
    ey = dates[-1].year
    unreal = sum(vals[i] - basis[i] for i in taxable)
    unpaid = sum(tm.TAX * max(0.0, realized.get(y, 0.0) - tm.DEDUCTION) for y in (ey - 1, ey)
                 if not (y == ey - 1 and dates[-1].month > 5)) if taxable else 0.0
    liq = (tm.TAX * max(0.0, realized.get(ey, 0.0) + unreal - tm.DEDUCTION)
           - tm.TAX * max(0.0, realized.get(ey, 0.0) - tm.DEDUCTION)) if taxable else 0.0
    return dict(cagr=(eq[-1] / tm.CAPITAL) ** (1 / years) - 1,
                cagr_after=((eq[-1] - unpaid - liq) / tm.CAPITAL) ** (1 / years) - 1,
                mdd=m["mdd"], worst_12m=(me / me.shift(12) - 1).min(),
                recovery=f"{m['recovery_days']}" + ("+" if m["recovery_ongoing"] else ""),
                n_ep=len(episodes)), episodes


def main():
    d = load()
    pct = vb.pct
    ndx_ath2000 = d["ndx"].loc[:"2001-01-01"].max()
    back = d.index[(d.index > pd.Timestamp("2001-01-01")) & (d["ndx"] > ndx_ath2000)][0]
    L = ["바이 더 딥을 나스닥100 + TQQQ로 하면",
         f"데이터: {d.index[0].date()}부터 {d.index[-1].date()}까지. 나스닥100이 닷컴 고점({ndx_ath2000:,.0f})을 처음 넘은 날: {back.date()}",
         "포트폴리오: VOO 35 / QQQM 15 / SGOV 25 / 원화 현금 20 (투더문 10 + 비트코인 자리 10) / 금 5, 매년 11월 리밸런싱",
         "바이 더 딥: 문턱마다 SGOV 목표치의 1/3 투입, 기준 지수가 고점을 회복하면 목표 비중으로 되돌림, 진행 중 11월 리밸런싱 건너뜀", ""]
    cols = [("name", "안", None), ("cagr", "CAGR", pct), ("cagr_after", "세후 CAGR", pct), ("mdd", "MDD", pct),
            ("worst_12m", "최악 12개월", pct), ("recovery", "회복일", None), ("n_ep", "딥 횟수", None)]
    runs = [("원화, 2015년부터 (나스닥100 닷컴 고점 회복 이후, 양도세 반영)", str(back.date()), None, True),
            ("달러, 2000-03-27부터 (나스닥100 닷컴 고점, 세금 없음)", "2000-03-27", None, False),
            ("달러, 2007-10-09부터 (S&P500 고점, 세금 없음)", "2007-10-09", None, False)]
    logs = {}
    for title, s0, e0, krw in runs:
        rows = []
        for plan in PLANS:
            r, eps = simulate(d, s0, e0, plan, krw)
            rows.append(dict(name=plan[0], **r))
            logs[(title, plan[0])] = eps
        L.append("=" * 110 + f"\n{title}\n" + "=" * 110)
        L.append(vb.table(rows, cols if krw else [c for c in cols if c[0] != "cagr_after"]))
        L.append("")
    L.append("=" * 110 + "\n딥 기록 (투입 금액 기준 수익률, 최대 평가손실은 투입 후 가장 크게 물렸을 때)\n" + "=" * 110)
    for title, s0, e0, krw in runs[:2]:
        for plan in PLANS[:4] + PLANS[5:]:
            eps = logs[(title, plan[0])]
            L.append(f"\n[{title[:20]} · {plan[0]}]")
            L.append(vb.table(eps, [("start", "첫 매수", None), ("buys", "매수일", lambda b: ", ".join(b)),
                                    ("end", "종료(고점 회복)", None), ("ret", "딥 자금 수익", pct),
                                    ("ret3", "3차 수익", pct), ("low", "최대 평가손실", pct)]) if eps else "  없음")
    text = "\n".join(L)
    with open(os.path.join(bs.RES_DIR, "summary_dip_tqqq.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
