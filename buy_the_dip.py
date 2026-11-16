"""
S&P500 바이더딥 기준 비교 (2000년 이후)
=======================================

질문: 현금을 들고 기다리다가 "충분히 조정받았을 때" 산다면, 어떤 기준이 안전하고 좋은 가격인가

비교 기준 (신호는 S&P500 지수 ^GSPC 종가, VIX 종가로 판단)
  고점 대비 하락: -10%, -15%, -20%, -30% (52주 고점, 즉 최근 252거래일 최고 종가 대비)
  200일선 대비 -10% 이하
  RSI(14) 30 미만
  VIX 30 이상, 40 이상
  결합: 고점 대비 -10% 이하이면서 VIX 30 이상
  조정 후 추세 회복: 고점 대비 -15% 이상 빠진 뒤 처음으로 200일선 위에서 마감

매수 가정
  신호가 처음 나온 날 다음 거래일 시가에 SPY 매수 (배당 재투자 수정주가)
  한 번의 조정에서 한 번만 산다: 고점 대비 기준은 52주 신고가를 다시 낼 때까지,
  나머지 기준은 6개월(126거래일) 동안 신호가 없다가 다시 나와야 새 기회로 본다

평가
  - 매수 뒤 3개월, 1년, 3년, 5년 수익률
  - 매수 뒤 1년 안에 더 빠진 최대폭, 본전 회복까지 걸린 기간 (물려 있던 기간)
  - 기다림의 비용: 2000년 이후 아무 날이나 돈이 생겼을 때 바로 사지 않고
    다음 신호까지 기다렸다 사면 (기다리는 동안 미국 단기국채 이자) 더 많이 살 수 있었던 비율
  - 2000년 이후 52주 고점 대비 10% 이상 조정 일지, 현재 위치와 기준별 가격
  - 분할 매수 예시: 조정마다 예비자금을 1/3씩 나눠 단계별로 매수했다면

사용법: python buy_the_dip.py   결과: results/summary_buy_the_dip.txt
"""

import os

import numpy as np
import pandas as pd

import backtest_stock_ma as bs
import validate_buffer as vb

START = "2000-01-03"
COOLDOWN = 126
HORIZONS = {"3개월": 63, "1년": 252, "3년": 756, "5년": 1260}


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def load():
    g = vb.load_long("^GSPC")
    spy = vb.load_long("SPY")
    vix = vb.load_long("^VIX")["close"]
    rf = (vb.load_long("^IRX")["close"] / 100 / bs.ANN).clip(lower=0)
    df = pd.DataFrame({"close": g["close"]})
    df["ath"] = df["close"].cummax()
    df["hi52"] = df["close"].rolling(252, min_periods=1).max()
    df["dd"] = df["close"] / df["hi52"] - 1
    df["ma200"] = df["close"].rolling(200).mean()
    df["dev200"] = df["close"] / df["ma200"] - 1
    df["rsi"] = rsi(df["close"])
    df["vix"] = vix.reindex(df.index).ffill()
    df = df[df.index >= pd.Timestamp("1999-01-01")]
    df["spy_open"] = spy["open"].reindex(df.index)
    df["spy_close"] = spy["close"].reindex(df.index)
    df["rf"] = rf.reindex(df.index).ffill().fillna(0)
    df = df.dropna(subset=["spy_close"]).copy()
    # 이번 조정(52주 신고가 이후)에서 -15%를 찍은 적이 있는지
    seen, s = np.zeros(len(df), bool), False
    for i, x in enumerate(df["dd"].to_numpy()):
        s = False if x >= 0 else (s or x <= -0.15)
        seen[i] = s
    df["seen15"] = seen
    return df


CRITERIA = {
    "고점 대비 -10%": ("dd", -0.10),
    "고점 대비 -15%": ("dd", -0.15),
    "고점 대비 -20%": ("dd", -0.20),
    "고점 대비 -30%": ("dd", -0.30),
    "200일선 대비 -10%": ("cond", lambda d: d["dev200"] <= -0.10),
    "RSI(14) 30 미만": ("cond", lambda d: d["rsi"] < 30),
    "VIX 30 이상": ("cond", lambda d: d["vix"] >= 30),
    "VIX 40 이상": ("cond", lambda d: d["vix"] >= 40),
    "고점 -10% & VIX 30": ("cond", lambda d: (d["dd"] <= -0.10) & (d["vix"] >= 30)),
    "-15% 뒤 200일선 회복": ("dd_ma", lambda d: d["seen15"] & (d["close"] > d["ma200"])),
}


def signal_days(df, crit):
    """기준을 만족한 모든 날 (bool 배열)"""
    kind, arg = CRITERIA[crit]
    return (df["dd"] <= arg).to_numpy() if kind == "dd" else arg(df).fillna(False).to_numpy().astype(bool)


def episodes(df, crit):
    """한 번의 조정에서 첫 신호일만 고른다"""
    kind, _ = CRITERIA[crit]
    on = signal_days(df, crit)
    dd = df["dd"].to_numpy()
    out, armed, last_on = [], True, -10 ** 9
    for i in range(len(df)):
        if kind in ("dd", "dd_ma"):
            if dd[i] >= 0:
                armed = True
            if armed and on[i]:
                out.append(i)
                armed = False
        else:
            if on[i] and i - last_on > COOLDOWN:
                out.append(i)
            if on[i]:
                last_on = i
    return [i for i in out if df.index[i] >= pd.Timestamp(START) and i + 1 < len(df)]


def trade_stats(df, i):
    """i일 신호 -> i+1일 시가 매수"""
    e = df["spy_open"].iloc[i + 1]
    c = df["spy_close"].to_numpy()
    row = dict(date=df.index[i].date(), level=df["close"].iloc[i], dd=df["dd"].iloc[i],
               vix=df["vix"].iloc[i])
    for k, h in HORIZONS.items():
        j = i + 1 + h
        row[k] = c[j] / e - 1 if j < len(c) else np.nan
    win = c[i + 1:i + 1 + 252]
    row["more_drop"] = min(0.0, win.min() / e - 1)
    # 본전 회복: 매수가 아래로 내려간 뒤 다시 매수가 이상으로 마감할 때까지 걸린 달력 일수
    after = c[i + 1:]
    below = np.where(after < e)[0]
    if len(below) == 0:
        row["under_days"], row["recovered"] = 0, True
    else:
        low = int(np.argmin(after))
        back = np.where(after[low:] >= e)[0]
        if len(back):
            row["under_days"] = (df.index[i + 1 + low + back[0]] - df.index[i + 1 + below[0]]).days
            row["recovered"] = True
        else:
            row["under_days"] = (df.index[-1] - df.index[i + 1 + below[0]]).days
            row["recovered"] = False
    return row


def waiting_cost(df, crit):
    """2000년 이후 매일 돈이 생겼다고 가정. 바로 사는 것 대비 다음 신호까지 기다렸다 사는 것의 결과"""
    on = signal_days(df, crit)
    idx0 = df.index.searchsorted(pd.Timestamp(START))
    o = df["spy_open"].to_numpy()
    rf = df["rf"].to_numpy()
    growth = np.cumprod(1 + rf)           # 현금이 국채 이자로 불어난 배율
    nxt = np.full(len(df), -1)
    j = -1
    for i in range(len(df) - 1, -1, -1):  # i일 이후(포함) 첫 신호일
        if on[i]:
            j = i
        nxt[i] = j
    better, waits, ratio, never = [], [], [], 0
    for d in range(idx0, len(df) - 1):
        s = nxt[d]
        if s < 0 or s + 1 >= len(df):
            never += 1
            continue
        buy_now = 1 / o[d + 1]
        buy_wait = (growth[s + 1] / growth[d + 1]) / o[s + 1]
        better.append(buy_wait > buy_now)
        ratio.append(buy_wait / buy_now - 1)
        waits.append((df.index[s + 1] - df.index[d + 1]).days)
    total = len(better) + never
    w = np.array(waits)
    return dict(better=np.mean(better), gain_med=np.median(ratio), wait_med=np.median(w) / 30.4,
                wait_max=w.max() / 365.25, never=never / total)


def corrections(df):
    """2000년 이후 52주 고점 대비 10% 이상 조정 일지 (52주 신고가를 다시 내면 조정 끝)"""
    d = df[df.index >= pd.Timestamp("2000-01-01")]
    dd, c = d["dd"].to_numpy(), d["close"].to_numpy()
    rows, i = [], 0
    while i < len(d):
        if dd[i] < 0:
            s = i
            while i < len(d) and dd[i] < 0:
                i += 1
            seg = dd[s:i]
            if seg.min() <= -0.10:
                t = s + int(np.argmin(seg))
                peak = s - 1 if s > 0 else s
                r = dict(peak=d.index[peak].date(), peak_level=c[peak], trough=d.index[t].date(),
                         trough_level=c[t], mdd=seg.min(),
                         to_trough=(d.index[t] - d.index[peak]).days,
                         recovered=d.index[i].date() if i < len(d) else "진행 중",
                         to_recover=(d.index[i] - d.index[t]).days if i < len(d) else np.nan)
                for th in (0.10, 0.15, 0.20, 0.30):
                    hit = np.where(seg <= -th)[0]
                    r[f"hit{int(th * 100)}"] = d.index[s + hit[0]].date() if len(hit) else ""
                rows.append(r)
        else:
            i += 1
    return pd.DataFrame(rows)


PLANS = {
    "A 가격만 (-10/-15/-20%)": [("dd", -0.10), ("dd", -0.15), ("dd", -0.20)],
    "B 가격+추세 (-10/-15%, 200일선 회복)": [("dd", -0.10), ("dd", -0.15), ("ma", None)],
}


def plan_by_episode(df, cr, plan):
    """조정 하나마다 예비자금을 1/3씩. 52주 신고가가 나오면 그 조정의 남은 몫은 쓰지 않고 끝"""
    d = df["dd"].to_numpy()
    seen, above = df["seen15"].to_numpy(), (df["close"] > df["ma200"]).to_numpy()
    o, c = df["spy_open"].to_numpy(), df["spy_close"].to_numpy()
    rows = []
    for r in cr.itertuples():
        s = df.index.get_loc(pd.Timestamp(r.peak)) + 1
        e = df.index.searchsorted(pd.Timestamp(r.recovered)) if r.recovered != "진행 중" else len(df) - 1
        fills = []
        for kind, th in plan:
            hit = [i for i in range(s, e) if (d[i] <= th if kind == "dd" else seen[i] and above[i])]
            if hit and hit[0] + 1 < len(df):
                fills.append(hit[0] + 1)
        if not fills:
            continue
        units = np.zeros(len(df))
        for f in fills:
            units[f:] += 1 / o[f]
        cost = np.zeros(len(df))
        for f in fills:
            cost[f:] += 1
        ev = min(fills[0] + 756, len(df) - 1)                 # 첫 매수 후 3년
        span = slice(fills[0], ev + 1)
        val = units[span] * c[span]
        rows.append(dict(episode=f"{r.peak} ({vb.pct(r.mdd, 0)})", n=len(fills),
                         dates=", ".join(str(df.index[f].date()) for f in fills),
                         vs_bottom=np.mean([o[f] for f in fills]) / c[df.index.get_loc(pd.Timestamp(r.trough))] - 1,
                         worst=(val / cost[span]).min() - 1,
                         r3y=val[-1] / cost[ev] - 1 if fills[0] + 756 < len(df) else np.nan))
    return pd.DataFrame(rows)


def main():
    df = load()
    pct, num = vb.pct, vb.num
    last = df.iloc[-1]
    L = ["S&P500 바이더딥 기준 비교 (2000년 이후)",
         f"데이터: {df.index[0].date()}부터 {df.index[-1].date()}까지. 신호는 S&P500 지수 종가, 매수는 다음 거래일 시가에 SPY",
         "한 조정에서 첫 신호일만 매수 (고점 기준은 52주 신고가 전까지, 나머지는 6개월 공백 뒤 새 기회)", ""]

    # 기준 없이 아무 날이나 샀을 때
    base = [trade_stats(df, i) for i in range(df.index.searchsorted(pd.Timestamp(START)), len(df) - 1)]
    base = pd.DataFrame(base)

    L.append("=" * 110 + "\n1. 기준별 요약 (수익률과 추가하락은 중앙값)\n" + "=" * 110)
    rows, details = [], {}
    for crit in CRITERIA:
        ep = pd.DataFrame([trade_stats(df, i) for i in episodes(df, crit)])
        details[crit] = ep
        wc = waiting_cost(df, crit)
        rows.append(dict(name=crit, n=len(ep), r3m=ep["3개월"].median(), r1y=ep["1년"].median(),
                         w1y=ep["1년"].min(), p1y=(ep["1년"].dropna() > 0).mean(), r3y=ep["3년"].median(),
                         r5y=ep["5년"].median(), drop=ep["more_drop"].median(), wdrop=ep["more_drop"].min(),
                         under=ep["under_days"].max(), **wc))
    rows.append(dict(name="(참고) 아무 날이나", n=len(base), r3m=base["3개월"].median(), r1y=base["1년"].median(),
                     w1y=base["1년"].min(), p1y=(base["1년"].dropna() > 0).mean(), r3y=base["3년"].median(),
                     r5y=base["5년"].median(), drop=base["more_drop"].median(), wdrop=base["more_drop"].min(),
                     under=base["under_days"].max()))
    L.append(vb.table(rows, [("name", "기준", None), ("n", "기회", None), ("r3m", "3개월", pct),
                             ("r1y", "1년", pct), ("w1y", "1년 최악", pct), ("p1y", "1년 플러스", pct),
                             ("r3y", "3년", pct), ("r5y", "5년", pct), ("drop", "추가하락", pct),
                             ("wdrop", "추가하락 최악", pct), ("under", "최장 물림(일)", None)]))
    L.append("\n  추가하락: 매수 뒤 1년 안에 매수가 대비 더 빠진 최대폭. 최장 물림: 매수가 아래로 내려간 뒤 본전 회복까지 걸린 최장 일수")
    L.append("\n기다림의 비용 (2000년 이후 매일 돈이 생겼다고 가정, 바로 사기 vs 다음 신호까지 국채 이자 받으며 기다렸다 사기)")
    L.append(vb.table(rows[:-1], [("name", "기준", None), ("better", "기다린 게 나은 비율", pct),
                                  ("gain_med", "더 산 수량(중앙)", pct), ("wait_med", "대기(중앙, 개월)", lambda x: f"{x:.1f}"),
                                  ("wait_max", "최장 대기(년)", lambda x: f"{x:.1f}"),
                                  ("never", "아직 신호 없음", pct)]))

    L.append("\n" + "=" * 110 + "\n2. 기준별 매수 기록\n" + "=" * 110)
    for crit, ep in details.items():
        L.append(f"\n[{crit}] {len(ep)}번")
        L.append(vb.table(ep.to_dict("records"),
                          [("date", "신호일", None), ("level", "지수", lambda x: f"{x:,.0f}"), ("dd", "고점대비", pct),
                           ("vix", "VIX", lambda x: f"{x:.0f}"), ("3개월", "3개월", pct), ("1년", "1년", pct),
                           ("3년", "3년", pct), ("5년", "5년", pct), ("more_drop", "추가하락", pct),
                           ("under_days", "물림(일)", lambda x: f"{x:.0f}")]))

    L.append("\n" + "=" * 110 + "\n3. 2000년 이후 52주 고점 대비 10% 이상 조정 일지 (종가 기준, 52주 신고가를 다시 내면 끝)\n" + "=" * 110)
    cr = corrections(df)
    L.append(vb.table(cr.to_dict("records"),
                      [("peak", "고점일", None), ("peak_level", "고점", lambda x: f"{x:,.0f}"),
                       ("trough", "바닥일", None), ("trough_level", "바닥", lambda x: f"{x:,.0f}"),
                       ("mdd", "최대하락", pct), ("to_trough", "바닥까지(일)", None),
                       ("hit10", "-10% 도달", None), ("hit15", "-15%", None), ("hit20", "-20%", None),
                       ("hit30", "-30%", None), ("recovered", "52주 신고가", None),
                       ("to_recover", "바닥→회복(일)", lambda x: "" if pd.isna(x) else f"{x:.0f}")]))

    L.append("\n" + "=" * 110 + "\n4. 분할 매수 예시 (조정마다 예비자금 3등분, 각 단계 신호 다음 날 시가에 SPY 매수)\n" + "=" * 110)
    L.append("  평균단가-바닥: 평균 매수가가 그 조정의 바닥(52주 고점 대비 최대 하락일) 대비 얼마나 위였는지")
    L.append("  최대 평가손실: 첫 매수부터 3년 동안 산 금액 대비 가장 크게 물렸던 폭. 3년 수익: 첫 매수 3년 뒤 산 금액 대비")
    for name, plan in PLANS.items():
        pe = plan_by_episode(df, cr, plan)
        L.append(f"\n[{name}] 3년 수익 중앙값 {pct(pe.r3y.median())}, 최대 평가손실 중앙값 {pct(pe.worst.median())}, "
                 f"최악 {pct(pe.worst.min())}")
        L.append(vb.table(pe.to_dict("records"),
                          [("episode", "조정(고점일, 최대하락)", None), ("n", "매수 횟수", None), ("dates", "매수일", None),
                           ("vs_bottom", "평균단가-바닥", pct), ("worst", "최대 평가손실", pct), ("r3y", "3년 수익", pct)]))

    L.append("\n" + "=" * 110 + f"\n5. 현재 위치 ({df.index[-1].date()} 종가)\n" + "=" * 110)
    ath_date = df.index[df["close"].to_numpy().argmax()].date()
    L.append(f"  S&P500 {last.close:,.2f} · 사상 최고 {last.ath:,.2f} ({ath_date}) · 52주 고점 {last.hi52:,.2f} · "
             f"52주 고점 대비 {pct(last.dd)}")
    L.append(f"  200일선 {last.ma200:,.2f} (이격 {pct(last.dev200)}) · RSI(14) {last.rsi:.1f} · VIX {last.vix:.2f}")
    voo = vb.load_long("VOO")["close"]
    to_voo = lambda lv: voo.iloc[-1] * lv / last.close    # VOO는 지수를 그대로 따라가므로 같은 비율로 환산
    L.append(f"  VOO {voo.iloc[-1]:,.2f} ({voo.index[-1].date()})")
    L.append("  기준별 가격 (VOO는 현재 지수 대비 비율로 환산한 근사치)")
    for th in (0.10, 0.15, 0.20, 0.30):
        lv = last.hi52 * (1 - th)
        L.append(f"    52주 고점 대비 -{th * 100:.0f}%: S&P500 {lv:,.0f}, VOO {to_voo(lv):,.0f}")
    lv = last.ma200 * 0.9
    L.append(f"    200일선 -10%: S&P500 {lv:,.0f}, VOO {to_voo(lv):,.0f} (200일선이 움직이므로 매일 달라짐)")

    text = "\n".join(L)
    with open(os.path.join(bs.RES_DIR, "summary_buy_the_dip.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
