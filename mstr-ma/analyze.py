"""스트래터지(MSTR) 주가에 120일·200일 이동평균 조건을 적용해 비트코인과 비교 분석한다.

표준 라이브러리만 사용한다. 결과는 마크다운으로 출력하고 analysis.md로도 저장한다.
  START  분석 시작일(기본 2020-08-11, MSTR이 비트코인을 처음 매입한 날)
"""
import json
import math
import os
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

START = date.fromisoformat(os.environ.get("START", "2020-08-11"))
FETCH_FROM = START - timedelta(days=500)  # 200일선을 시작일부터 쓰기 위한 여유
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis.md")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)", "Accept": "application/json"}


def get_json(url):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.load(r)
        except Exception as e:  # 429 등 일시 오류는 재시도
            if i == 3:
                raise
            print(f"retry {url}: {e}")
            time.sleep(2 ** (i + 1))


def yahoo(symbol):
    """[(date, 수정종가)] 과거→현재. 수정종가라 2024-08 MSTR 10:1 액면분할이 반영된다."""
    p1 = int(datetime(FETCH_FROM.year, FETCH_FROM.month, FETCH_FROM.day, tzinfo=timezone.utc).timestamp())
    p2 = int(time.time()) + 86400
    j = get_json(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={p1}&period2={p2}&interval=1d&events=split"
    )["chart"]["result"][0]
    tz = timedelta(seconds=j["meta"].get("gmtoffset", 0))
    adj = j["indicators"].get("adjclose", [{}])[0].get("adjclose") or j["indicators"]["quote"][0]["close"]
    rows = {}
    for t, c in zip(j["timestamp"], adj):
        if c is not None:
            rows[(datetime.fromtimestamp(t, timezone.utc) + tz).date()] = float(c)
    return sorted(rows.items())


def sma(vals, n):
    out, s = [None] * len(vals), 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def stats(dates, closes, pos, per_year):
    """pos[i]=1 이면 i일 종가→i+1일 종가 구간을 보유. 수수료·슬리피지는 넣지 않았다."""
    eq, peak, mdd, rets, trades, inpos = 1.0, 1.0, 0.0, [], [], 0
    entry = None
    for i in range(len(closes) - 1):
        r = closes[i + 1] / closes[i] - 1 if pos[i] else 0.0
        eq *= 1 + r
        rets.append(r)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
        inpos += pos[i]
        if pos[i] and (i == 0 or not pos[i - 1]):
            entry = closes[i]
        if pos[i] and (i + 1 == len(closes) - 1 or not pos[i + 1]):
            trades.append(closes[i + 1] / entry - 1)
    years = (dates[-1] - dates[0]).days / 365.25
    m = sum(rets) / len(rets)
    vol = math.sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1) * per_year)
    return {
        "total": eq - 1,
        "cagr": eq ** (1 / years) - 1,
        "mdd": mdd,
        "vol": vol,
        "exposure": inpos / (len(closes) - 1),
        "trades": len(trades),
        "win": sum(t > 0 for t in trades) / len(trades) if trades else float("nan"),
        "best": max(trades) if trades else float("nan"),
        "worst": min(trades) if trades else float("nan"),
    }


def weekly(dates, sig):
    """주 1회 판단: 그 주 마지막 거래일 종가로 정한 포지션을 다음 주 마지막 거래일까지 유지한다.
    BTC는 일요일, 미국 주식은 보통 금요일이 마지막 거래일이다."""
    out, cur = [], 0
    for i, d in enumerate(dates):
        if i == len(dates) - 1 or dates[i + 1].isocalendar()[:2] != d.isocalendar()[:2]:
            cur = sig[i]
        out.append(cur)
    return out


def summary(series, per_year, starts, lines, title):
    full_d = [d for d, _ in series]
    full_c = [c for _, c in series]
    m120, m200 = sma(full_c, 120), sma(full_c, 200)
    lines.append(f"\n### {title}\n")
    lines.append("| 시작일 | 판단 | 조건 | 누적 | 연환산 | 최대낙폭 | 매매 |\n| --- | --- | --- | --- | --- | --- | --- |")
    for st in starts:
        k = next(i for i, d in enumerate(full_d) if d >= st)
        d, c, a, b = full_d[k:], full_c[k:], m120[k:], m200[k:]
        rows = [("-", "계속 보유", [1] * len(c))]
        for label, ma in (("종가 > 120일선", a), ("종가 > 200일선", b)):
            sig = [int(c[i] > ma[i]) for i in range(len(c))]
            rows.append(("매일", label, sig))
            rows.append(("주 1회", label, weekly(d, sig)))
        for freq, label, p in rows:
            x = stats(d, c, p, per_year)
            lines.append(f"| {d[0]} | {freq} | {label} | {pct(x['total'])} | {pct(x['cagr'])} | {pct(x['mdd'])} | {x['trades'] if freq != '-' else '-'} |")


def crosses(dates, a, b):
    """a가 b를 위로(+1)/아래로(-1) 넘은 날 목록."""
    ev = []
    for i in range(1, len(dates)):
        if None in (a[i], b[i], a[i - 1], b[i - 1]):
            continue
        if a[i - 1] <= b[i - 1] and a[i] > b[i]:
            ev.append((dates[i], +1))
        elif a[i - 1] >= b[i - 1] and a[i] < b[i]:
            ev.append((dates[i], -1))
    return ev


def pct(x):
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.1f}%"


def analyze(name, series, per_year, lines, extra_signals=None):
    full_d = [d for d, _ in series]
    full_c = [c for _, c in series]
    m120, m200 = sma(full_c, 120), sma(full_c, 200)
    k = next(i for i, d in enumerate(full_d) if d >= START)
    d, c, a, b = full_d[k:], full_c[k:], m120[k:], m200[k:]

    lines.append(f"\n## {name}\n")
    lines.append(f"기간 {d[0]} → {d[-1]} ({len(d)}거래일), 이평선 단위는 {'거래일' if per_year == 252 else '달력일'}\n")
    last = len(d) - 1
    lines.append("| 항목 | 값 |\n| --- | --- |")
    lines.append(f"| 최근 종가 | {c[last]:,.2f} |")
    lines.append(f"| 120일선 | {a[last]:,.2f} (괴리 {pct(c[last] / a[last] - 1)}) |")
    lines.append(f"| 200일선 | {b[last]:,.2f} (괴리 {pct(c[last] / b[last] - 1)}) |")
    lines.append(f"| 120일선 vs 200일선 | {'120 > 200 (정배열)' if a[last] > b[last] else '120 < 200 (역배열)'} ({pct(a[last] / b[last] - 1)}) |")
    s20 = 20
    lines.append(f"| 120일선 20일 기울기 | {pct(a[last] / a[last - s20] - 1)} |")
    lines.append(f"| 200일선 20일 기울기 | {pct(b[last] / b[last - s20] - 1)} |")
    hi = max(c)
    lines.append(f"| 기간 최고가 대비 | {pct(c[last] / hi - 1)} (최고 {hi:,.2f}, {d[c.index(hi)]}) |")

    sigs = {
        "보유(Buy & Hold)": [1] * len(c),
        "종가 > 120일선": [int(c[i] > a[i]) for i in range(len(c))],
        "종가 > 200일선": [int(c[i] > b[i]) for i in range(len(c))],
        "120일선 > 200일선": [int(a[i] > b[i]) for i in range(len(c))],
        "종가 > 120일선 & 종가 > 200일선": [int(c[i] > a[i] and c[i] > b[i]) for i in range(len(c))],
    }
    if extra_signals:
        sigs.update(extra_signals(d))
    lines.append("\n**이평선 조건별 보유 전략 (당일 종가로 판단, 다음 날부터 반영, 비용 제외)**\n")
    lines.append("| 전략 | 누적 | 연환산 | 최대낙폭 | 변동성 | 보유비중 | 매매 | 승률 | 최고/최저 매매 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    res = {}
    for label, p in sigs.items():
        s = stats(d, c, p, per_year)
        res[label] = s
        win = "-" if math.isnan(s["win"]) else f"{s['win'] * 100:.0f}%"
        lines.append(
            f"| {label} | {pct(s['total'])} | {pct(s['cagr'])} | {pct(s['mdd'])} | {s['vol'] * 100:.0f}% | "
            f"{s['exposure'] * 100:.0f}% | {s['trades']} | {win} | "
            f"{pct(s['best'])} / {pct(s['worst'])} |"
        )

    lines.append("\n**최근 교차 기록**\n")
    for label, x, y in (("종가×120일선", c, a), ("종가×200일선", c, b), ("120일선×200일선", a, b)):
        ev = crosses(d, x, y)
        yrs = (d[-1] - d[0]).days / 365.25
        recent = ", ".join(f"{dt}{'↑' if s > 0 else '↓'}" for dt, s in ev[-6:])
        lines.append(f"- {label}: 총 {len(ev)}회(연 {len(ev) / yrs:.1f}회). 최근 {recent}")
    return {"dates": full_d, "closes": full_c, "m120": m120, "m200": m200, "res": res}


def matched_returns(mstr, btc):
    """MSTR 거래일 사이의 BTC 수익률을 맞춰 쌍으로 만든다(BTC는 주말에도 거래)."""
    bmap = dict(btc)
    bd = sorted(bmap)
    def btc_at(d):  # d 이하 가장 최근 BTC 종가
        lo, hi = 0, len(bd) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            lo, hi = (mid, hi) if bd[mid] <= d else (lo, mid - 1)
        return bmap[bd[lo]]
    pts = [(d, c, btc_at(d)) for d, c in mstr if d >= START]
    return [(pts[i][0], pts[i][1] / pts[i - 1][1] - 1, pts[i][2] / pts[i - 1][2] - 1) for i in range(1, len(pts))]


def beta_corr(pairs):
    xs, ys = [p[2] for p in pairs], [p[1] for p in pairs]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx, vy = sum((x - mx) ** 2 for x in xs), sum((y - my) ** 2 for y in ys)
    return cov / vx, cov / math.sqrt(vx * vy), math.sqrt(vy / vx)


def main():
    mstr, btc = yahoo("MSTR"), yahoo("BTC-USD")
    lines = [f"# 스트래터지(MSTR) 120일·200일 이동평균 분석\n\n생성 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC, 데이터 Yahoo Finance 수정종가"]

    # BTC 신호로 MSTR을 매매: 미래 정보를 쓰지 않도록 MSTR 거래일 전날까지의 BTC 종가로 판단
    bd = [x for x, _ in btc]
    bc = [x for _, x in btc]
    b120, b200 = sma(bc, 120), sma(bc, 200)
    bidx = {x: i for i, x in enumerate(bd)}

    def btc_signal(days, ma):
        out = []
        for dd in days:
            j = bidx.get(dd - timedelta(days=1))
            out.append(int(j is not None and ma[j] is not None and bc[j] > ma[j]))
        return out

    def extra(days):
        return {
            "BTC 종가 > BTC 120일선 (BTC 신호로 MSTR 매매)": btc_signal(days, b120),
            "BTC 종가 > BTC 200일선 (BTC 신호로 MSTR 매매)": btc_signal(days, b200),
        }

    analyze("MSTR (스트래터지)", mstr, 252, lines, extra)
    analyze("BTC-USD (비교용)", btc, 365, lines)

    lines.append("\n## 기간·판단 주기별 비교\n")
    starts = [START, date(2022, 1, 3)]
    summary(mstr, 252, starts, lines, "MSTR")
    summary(btc, 365, starts, lines, "BTC-USD")

    pairs = matched_returns(mstr, btc)
    lines.append("\n## MSTR과 비트코인의 관계\n")
    lines.append("MSTR 거래일 간격으로 맞춘 일간 수익률 기준\n")
    lines.append("| 구간 | 베타 | 상관계수 | 변동성 배수(MSTR/BTC) |\n| --- | --- | --- | --- |")
    last_d = pairs[-1][0]
    for label, since in (("전체", START), ("최근 2년", last_d - timedelta(days=730)),
                         ("최근 1년", last_d - timedelta(days=365)), ("최근 6개월", last_d - timedelta(days=182))):
        sub = [p for p in pairs if p[0] >= since]
        bt, cr, vr = beta_corr(sub)
        lines.append(f"| {label} | {bt:.2f} | {cr:.2f} | {vr:.2f}배 |")

    text = "\n".join(lines) + "\n"
    print(text)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main()
