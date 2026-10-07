"""주식·지수 200일선 주 1회 전략의 매매 시점 차트 생성

  python make_stock_ma200_chart.py                     # 2022-01-03부터, 네 종목 모두
  python make_stock_ma200_chart.py --start 2001-01-02  # 긴 구간
  python make_stock_ma200_chart.py --asset SPY --buffer 0.04   # 매일 판단, 버퍼 ±4%

먼저 python backtest_stock_ma.py 를 실행해 data/stocks/ 에 데이터를 받아 두어야 합니다.
결과: charts/stock_ma200_{종목}_{시작연도}.html
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

import backtest_stock_ma as bs

MA = 200
FILES = {"SPY": "spy", "QQQ": "qqq", "코스피": "kospi", "삼성전자": "samsung"}
UNITS = {"SPY": "$", "QQQ": "$", "코스피": "pt", "삼성전자": "원"}
COST = {"SPY": "편도 0.12%(수수료 0.07%, 슬리피지 0.05%)",
        "QQQ": "편도 0.12%(수수료 0.07%, 슬리피지 0.05%)",
        "코스피": "편도 0.065%(ETF 매매 가정, 수수료 0.015%, 슬리피지 0.05%)",
        "삼성전자": "매수 0.065%, 매도 0.265%(거래세 0.20% 포함)"}


def make(name, start, buffer=None):
    df = bs.load(name)
    close, opn = df["close"], df["open"]
    ma = close.rolling(MA).mean()
    if buffer is None:
        pos_all = bs.positions(bs.above_state(close, ma), bs.decision_days(df.index, 0))
    else:
        pos_all = bs.positions(bs.buffer_state(close, ma, buffer), None)
    i0 = df.index.searchsorted(pd.Timestamp(start))
    idx = df.index[i0:]
    o, c, pos = opn.to_numpy(float)[i0:], close.to_numpy(float)[i0:], pos_all[i0:]
    buy, sell = bs.ASSETS[name][2:]
    eq, _ = bs.simulate(o, c, pos, buy, sell)
    hold = c / o[0]

    trades, prev, last_buy = [], False, None
    for i in range(len(idx)):
        if pos[i] != prev:
            t = dict(date=idx[i].strftime("%Y-%m-%d"), side="buy" if pos[i] else "sell",
                     price=float(o[i]), equity=float(eq[i] * 1e8))
            if pos[i]:
                last_buy = t["price"]
            elif last_buy:
                t["ret"] = t["price"] / last_buy * (1 - buy) * (1 - sell) - 1
            trades.append(t)
            prev = pos[i]

    rnd = 2 if UNITS[name] != "원" else 0
    data = dict(date=[d.strftime("%Y-%m-%d") for d in idx],
                close=[round(float(x), rnd) for x in c],
                ma=[round(float(x), rnd) for x in ma.to_numpy(float)[i0:]],
                inv=[bool(x) for x in pos], eq=[round(float(x) * 1e8) for x in eq],
                hold=[round(float(x) * 1e8) for x in hold], trades=trades, unit=UNITS[name])
    m = bs.metrics(eq, idx)
    period = f"{idx[0]:%Y-%m-%d}부터 {idx[-1]:%Y-%m-%d}까지"
    perf = f"비용 {COST[name]} · 원금 1억 원 기준 · CAGR {m['cagr'] * 100:.1f}%, 샤프 {m['sharpe']:.2f}"
    band, suffix = "", ""
    if buffer is None:
        title, strat = f"{name} 200일선 · 주 1회 판단 매매 기록", "200일선 주 1회 전략"
        sub = f"{period} · 매주 첫 거래일 시가에 판단, 전일 종가가 200일선 위면 보유, 아래면 현금 · {perf}"
    else:
        b = buffer * 100
        data["upper"] = [round(float(x) * (1 + buffer), rnd) for x in ma.to_numpy(float)[i0:]]
        data["lower"] = [round(float(x) * (1 - buffer), rnd) for x in ma.to_numpy(float)[i0:]]
        title, strat = f"{name} 200일선 · 버퍼 ±{b:g}% 매매 기록", f"200일선 버퍼 ±{b:g}% 전략"
        sub = (f"{period} · 매일 판단, 종가가 200일선 +{b:g}% 위로 올라서면 다음 날 시가 매수, "
               f"-{b:g}% 아래로 내려가면 다음 날 시가 매도 · {perf}")
        band = (f'      <span><i class="sw" style="background:repeating-linear-gradient(90deg,var(--s2) 0 4px,transparent 4px 7px)"></i>'
                f'매수선 +{b:g}% / 매도선 -{b:g}%</span>\n')
        suffix = f"_buffer{b:g}"
    adj = ("가격은 배당 재투자를 반영한 수정주가(달러)라 실제 거래 가격과 다릅니다. "
           if UNITS[name] == "$" else "가격은 수정주가이며 배당은 반영하지 않았습니다. ")
    note = f"체결가는 판단일 시가. 수익률은 매수에서 매도까지, 왕복 비용 반영. {adj}현금 이자는 0으로 가정."
    html = open(os.path.join(bs.BASE, "charts", "stock_ma200_template.html"), encoding="utf-8").read()
    for k, v in {"/*TITLE*/": title, "/*SUB*/": sub, "/*STRAT*/": strat, "/*BANDLEGEND*/": band,
                 "/*ASSET*/": name, "/*UNITNAME*/": {"$": "달러", "pt": "포인트", "원": "원화"}[UNITS[name]],
                 "/*NOTE*/": note, "/*DATA*/null": json.dumps(data, ensure_ascii=False)}.items():
        html = html.replace(k, v)
    path = os.path.join(bs.BASE, "charts", f"stock_ma200_{FILES[name]}{suffix}_{idx[0].year}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(path, f"매매 {len(trades)}건, 최종 {eq[-1] * 1e8:,.0f}원 / 보유 {hold[-1] * 1e8:,.0f}원")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01-03")
    ap.add_argument("--asset", choices=list(bs.ASSETS), default=None, help="생략하면 네 종목 모두")
    ap.add_argument("--buffer", type=float, default=None, help="버퍼 비율 (예: 0.04). 생략하면 주 1회 판단")
    args = ap.parse_args()
    for name in [args.asset] if args.asset else bs.ASSETS:
        make(name, args.start, args.buffer)


if __name__ == "__main__":
    main()
