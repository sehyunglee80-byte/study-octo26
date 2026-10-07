"""BTC 200일선 전략의 매매 시점 차트 생성

  python make_btc_ma200_chart.py                 # 주 1회(월요일) 판단 -> charts/btc_ma200_weekly.html
  python make_btc_ma200_chart.py --buffer 0.02   # 매일 판단, 버퍼 ±2% -> charts/btc_ma200_buffer2.html
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

import backtest_momentum as bm

START = "2022-01-03"
MA = 200


def buffer_state(close, ma, b):
    """종가가 이평선 x (1+b) 위로 올라서면 보유, x (1-b) 아래로 내려가면 현금 (그 사이는 이전 상태 유지)"""
    st, s = np.zeros(len(close), bool), False
    for i, (c, m) in enumerate(zip(close, ma)):
        if np.isnan(m):
            continue
        if not s and c > m * (1 + b):
            s = True
        elif s and c < m * (1 - b):
            s = False
        st[i] = s
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--buffer", type=float, default=None, help="버퍼 비율 (예: 0.02). 생략하면 주 1회 월요일 판단")
    args = ap.parse_args()

    raw = pd.read_csv(os.path.join(bm.DATA_DIR, "upbit_daily.csv"), parse_dates=["date"])
    P = bm.build_panels(raw)
    idx = P["close"].index
    btc = P["close"]["BTC"]
    ma = btc.rolling(MA).mean()
    opn = P["open"]["BTC"]

    weekly = args.buffer is None
    if weekly:
        state = (btc > ma).to_numpy()
    else:
        state = buffer_state(btc.to_numpy(), ma.to_numpy(), args.buffer)
    ok = pd.Series(state, index=idx).shift(1).fillna(False).to_numpy(bool)   # 전일 종가 기준 신호

    rebal = np.array([i for i, d in enumerate(idx)
                      if d >= pd.Timestamp(START) and (not weekly or d.weekday() == 0)])
    rebal = rebal[rebal < len(idx) - 1]
    A = lambda c: P[c][["BTC"]].to_numpy(float)
    one = np.ones((len(idx), 1))
    eq, _ = bm.simulate(A("open"), A("high"), A("low"), A("close"), one.astype(bool), one, ok, rebal, 0, 0, False, k=1)
    sd = idx[rebal[0]:]
    hold = btc[sd] / opn[sd[0]]

    # 매매 내역: 신호가 바뀐 날 09:00 시가에 체결
    trades, prev, last_buy = [], False, None
    for i in rebal:
        if ok[i] != prev:
            t = dict(date=idx[i].strftime("%Y-%m-%d"), side="buy" if ok[i] else "sell",
                     price=float(opn.iloc[i]), equity=float(eq[i - rebal[0]] * 1e8))
            if ok[i]:
                last_buy = t["price"]
            elif last_buy:
                t["ret"] = t["price"] / last_buy * (1 - bm.FEE - bm.SLIPPAGE) ** 2 - 1
            trades.append(t)
            prev = ok[i]

    held = [bool(ok[rebal[np.searchsorted(rebal, i, side="right") - 1]]) for i in range(rebal[0], len(idx))]
    data = dict(
        date=[d.strftime("%Y-%m-%d") for d in sd],
        close=[round(float(x)) for x in btc[sd]],
        ma=[round(float(x)) for x in ma[sd]],
        inv=held,
        eq=[round(float(x) * 1e8) for x in eq],
        hold=[round(float(x) * 1e8) for x in hold],
        trades=trades,
    )
    period = f"{sd[0]:%Y-%m-%d}부터 {sd[-1]:%Y-%m-%d}까지"
    cost = "수수료 0.05%와 슬리피지 0.1% 반영 · 원금 1억 원 기준"
    if weekly:
        title = "BTC 200일선 · 주 1회 판단 매매 기록"
        sub = f"{period} · 매주 월요일 09:00, 전일 종가가 200일선 위면 보유, 아래면 현금 · {cost}"
        strat, band_legend = "200일선 주 1회 전략", ""
        note = "체결가는 해당 월요일 09:00 업비트 시가. 수익률은 매수에서 매도까지, 왕복 비용 반영."
        out = "btc_ma200_weekly.html"
    else:
        b = args.buffer * 100
        data["upper"] = [round(float(x) * (1 + args.buffer)) for x in ma[sd]]
        data["lower"] = [round(float(x) * (1 - args.buffer)) for x in ma[sd]]
        title = f"BTC 200일선 · 버퍼 ±{b:g}% 매매 기록"
        sub = (f"{period} · 매일 판단, 종가가 200일선 +{b:g}% 위로 올라서면 다음 날 09:00 매수, "
               f"-{b:g}% 아래로 내려가면 다음 날 09:00 매도 · {cost}")
        strat = f"200일선 버퍼 ±{b:g}% 전략"
        band_legend = (f'      <span><i class="sw" style="background:repeating-linear-gradient(90deg,var(--s2) 0 4px,transparent 4px 7px)"></i>'
                       f'매수선 +{b:g}% / 매도선 -{b:g}%</span>\n')
        note = "체결가는 신호 다음 날 09:00 업비트 시가. 수익률은 매수에서 매도까지, 왕복 비용 반영."
        out = f"btc_ma200_buffer{b:g}.html"

    html = open(os.path.join(bm.BASE, "charts", "btc_ma200_template.html"), encoding="utf-8").read()
    for k, v in {"/*TITLE*/": title, "/*SUB*/": sub, "/*STRAT*/": strat, "/*BANDLEGEND*/": band_legend,
                 "/*NOTE*/": note, "/*DATA*/null": json.dumps(data, ensure_ascii=False)}.items():
        html = html.replace(k, v)
    path = os.path.join(bm.BASE, "charts", out)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(path, f"매매 {len(trades)}건, 최종 {eq[-1] * 1e8:,.0f}원 / 보유 {hold.iloc[-1] * 1e8:,.0f}원")
    for t in trades:
        print(t["date"], t["side"], f"{t['price']:,.0f}", f"{t['ret']:+.1%}" if "ret" in t else "")


if __name__ == "__main__":
    main()
