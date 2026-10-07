"""BTC 200일선 주 1회 판단 전략의 매매 시점 차트 생성 -> charts/btc_ma200_weekly.html"""
import json
import os

import numpy as np
import pandas as pd

import backtest_momentum as bm

START = "2022-01-01"
MA = 200

raw = pd.read_csv(os.path.join(bm.DATA_DIR, "upbit_daily.csv"), parse_dates=["date"])
P = bm.build_panels(raw)
idx = P["close"].index
btc = P["close"]["BTC"]
ma = btc.rolling(MA).mean()
ok = (btc > ma).shift(1).fillna(False).to_numpy(bool)

A = lambda c: P[c][["BTC"]].to_numpy(float)
one = np.ones((len(idx), 1))
rebal = np.array([i for i, d in enumerate(idx) if d >= pd.Timestamp(START) and d.weekday() == 0])
rebal = rebal[rebal < len(idx) - 1]
eq, _ = bm.simulate(A("open"), A("high"), A("low"), A("close"), one.astype(bool), one, ok, rebal, 0, 0, False, k=1)
sd = idx[rebal[0]:]
opn = P["open"]["BTC"]
hold = btc[sd] / opn[sd[0]]

# 매매 내역: 주간 판단이 바뀐 월요일 시가에 체결
trades, prev = [], False
for i in rebal:
    if ok[i] != prev:
        trades.append(dict(date=idx[i].strftime("%Y-%m-%d"), side="buy" if ok[i] else "sell",
                           price=float(opn.iloc[i]), equity=float(eq[i - rebal[0]] * 1e8)))
        prev = ok[i]
# 매도 시 직전 매수 대비 수익률
last_buy = None
for t in trades:
    if t["side"] == "buy":
        last_buy = t["price"]
    elif last_buy:
        t["ret"] = t["price"] / last_buy * (1 - 0.0015) ** 2 - 1

inv = [bool(ok[rebal[np.searchsorted(rebal, i, side="right") - 1]]) for i in range(rebal[0], len(idx))]
data = dict(
    date=[d.strftime("%Y-%m-%d") for d in sd],
    close=[round(float(x)) for x in btc[sd]],
    ma=[round(float(x)) for x in ma[sd]],
    inv=inv,
    eq=[round(float(x) * 1e8) for x in eq],
    hold=[round(float(x) * 1e8) for x in hold],
    trades=trades,
)

html = open(os.path.join(bm.BASE, "charts", "btc_ma200_template.html"), encoding="utf-8").read()
html = html.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False))
out = os.path.join(bm.BASE, "charts", "btc_ma200_weekly.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print(out, len(trades), "건", f"최종 {eq[-1]*1e8:,.0f} / 보유 {hold.iloc[-1]*1e8:,.0f}")
for t in trades:
    print(t)
