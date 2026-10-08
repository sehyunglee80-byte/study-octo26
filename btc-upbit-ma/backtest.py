"""업비트 원화(KRW-BTC) 일봉으로 이동평균 조건별 보유 전략을 비교한다.

표준 라이브러리만 사용한다. 결과는 마크다운으로 출력하고 analysis.md로도 저장한다.
  FEE  매수·매도 한 번당 수수료(기본 0.0005 = 업비트 원화 마켓 0.05%)

규칙
  - 업비트 일봉은 09:00 KST에 마감한다. 진행 중인 봉은 쓰지 않는다.
  - 매일 판단: 마감 종가가 조건을 만족하면 그 종가에 사서 다음 마감까지 보유한다.
  - 주 1회 판단: 정한 요일의 일봉(기본 일요일 = 월요일 09:00 마감) 종가로 정하고 일주일 유지한다.
"""
import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
FEE = float(os.environ.get("FEE", "0.0005"))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis.md")
UPBIT = "https://api.upbit.com/v1/candles/days"
WEEKDAYS = "월화수목금토일"


def get_json(url):
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "btc-upbit-ma"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception as e:
            if i == 3:
                raise
            print(f"retry: {e}")
            time.sleep(2 ** (i + 1))


def fetch_all():
    """상장 이후 마감된 일봉 전체 [(KST 시작일, 종가)] 과거→현재."""
    now, candles, to = datetime.now(KST), {}, None
    for _ in range(40):
        url = f"{UPBIT}?market=KRW-BTC&count=200" + (f"&to={urllib.parse.quote(to)}" if to else "")
        page = get_json(url)
        for r in page:
            start = datetime.fromisoformat(r["candle_date_time_kst"]).replace(tzinfo=KST)
            if start + timedelta(days=1) <= now:
                candles[start.date()] = float(r["trade_price"])
        if len(page) < 200:
            break
        to = page[-1]["candle_date_time_utc"].replace("T", " ")
        time.sleep(0.15)  # 업비트 시세 API 초당 요청 제한
    return sorted(candles.items())


def sma(vals, n):
    out, s = [None] * len(vals), 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def weekly(dates, sig, weekday=6):
    """weekday 요일 일봉의 마감 종가로만 포지션을 바꾼다. 첫 판단 전에는 현금."""
    out, cur = [], 0
    for d, s in zip(dates, sig):
        if d.weekday() == weekday:
            cur = s
        out.append(cur)
    return out


def stats(dates, closes, pos, fee=FEE):
    """pos[i]=1 이면 i일 마감→i+1일 마감 구간을 보유. 포지션이 바뀔 때마다 수수료를 뗀다."""
    eq, peak, mdd, trades, entry_eq, held = 1.0, 1.0, 0.0, [], None, 0
    prev = 0
    for i in range(len(closes) - 1):
        if pos[i] != prev:
            eq *= 1 - fee
            if pos[i]:
                entry_eq = eq
            else:
                trades.append(eq / entry_eq - 1)
            prev = pos[i]
        if pos[i]:
            eq *= closes[i + 1] / closes[i]
            held += 1
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    if prev:  # 마지막까지 보유 중이면 평가만 하고 매도 수수료는 떼지 않는다
        trades.append(eq / entry_eq - 1)
    years = (dates[-1] - dates[0]).days / 365.25
    return {
        "total": eq - 1,
        "cagr": eq ** (1 / years) - 1 if eq > 0 else -1,
        "mdd": mdd,
        "exposure": held / (len(closes) - 1),
        "trades": len(trades),
        "win": sum(t > 0 for t in trades) / len(trades) if trades else float("nan"),
    }


def pct(x, digits=1):
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.{digits}f}%"


def won(v):
    return f"{v / 1e4:,.0f}만 원"


def main():
    series = fetch_all()
    D = [d for d, _ in series]
    C = [c for _, c in series]
    MA = {n: sma(C, n) for n in (60, 90, 100, 120, 150, 200, 250)}
    first_valid = next(i for i in range(len(C)) if MA[250][i] is not None)
    periods = [("전체", D[first_valid]), ("2020년~", date(2020, 1, 1)), ("2022년~", date(2022, 1, 3)), ("최근 3년", D[-1] - timedelta(days=365 * 3))]

    def window(start):
        k = next(i for i, d in enumerate(D) if d >= start)
        return k, D[k:], C[k:]

    def above(n, k):
        return [int(C[i] > MA[n][i]) for i in range(k, len(C))]

    L = [
        "# 업비트 KRW-BTC 이동평균 전략 백테스트\n",
        f"생성 {datetime.now(KST):%Y-%m-%d %H:%M} KST · 데이터 업비트 일봉 {D[0]} → {D[-1]} ({len(D)}일) · "
        f"수수료 매수·매도 각 {FEE * 100:.2f}% 반영\n",
    ]

    # 현재 상태
    last = len(C) - 1
    L.append(f"\n## 현재 상태 ({D[-1]} 마감, {D[-1] + timedelta(days=1)} 09:00 KST)\n")
    L.append("| 항목 | 값 |\n| --- | --- |")
    L.append(f"| 종가 | {won(C[last])} |")
    for n in (120, 200):
        L.append(f"| {n}일선 | {won(MA[n][last])} (괴리 {pct(C[last] / MA[n][last] - 1, 2)}) |")
    L.append(f"| 120일선 vs 200일선 | {'120 > 200' if MA[120][last] > MA[200][last] else '120 < 200'} ({pct(MA[120][last] / MA[200][last] - 1, 2)}) |")
    sun = max(i for i in range(len(D)) if D[i].weekday() == 6)
    L.append(f"| 직전 주간 판단 ({D[sun]} 일요일 봉) | 200일선 {'위 → 보유' if C[sun] > MA[200][sun] else '아래 → 현금'}, "
             f"120일선 {'위 → 보유' if C[sun] > MA[120][sun] else '아래 → 현금'} |")

    # 전략 비교
    def strategies(k, d):
        s = {
            "계속 보유": [1] * len(d),
            "매일 · 종가 > 120일선": above(120, k),
            "매일 · 종가 > 200일선": above(200, k),
            "매일 · 종가 > 120일선 & 200일선": [a & b for a, b in zip(above(120, k), above(200, k))],
            "매일 · 120일선 > 200일선": [int(MA[120][i] > MA[200][i]) for i in range(k, len(C))],
            "주 1회(일) · 종가 > 120일선": weekly(d, above(120, k)),
            "주 1회(일) · 종가 > 200일선": weekly(d, above(200, k)),
            "주 1회(일) · 종가 > 120일선 & 200일선": weekly(d, [a & b for a, b in zip(above(120, k), above(200, k))]),
        }
        return s

    for name, start in periods:
        k, d, c = window(start)
        L.append(f"\n## 전략 비교: {name} ({d[0]} → {d[-1]})\n")
        L.append("| 전략 | 누적 | 연환산 | 최대낙폭 | 보유비중 | 매매 | 승률 |\n| --- | --- | --- | --- | --- | --- | --- |")
        for label, p in strategies(k, d).items():
            x = stats(d, c, p)
            L.append(f"| {label} | {pct(x['total'], 0)} | {pct(x['cagr'])} | {pct(x['mdd'])} | {x['exposure'] * 100:.0f}% | "
                     f"{x['trades'] if label != '계속 보유' else '-'} | {'-' if label == '계속 보유' else f'{x['win'] * 100:.0f}%'} |")

    # 다른 세션 백테스트 재현 확인 (수수료 없이)
    k, d, c = window(date(2022, 1, 3))
    x = stats(d, c, weekly(d, above(200, k)), fee=0)
    L.append(f"\n참고: 2022-01-03부터 주 1회(일) 200일선, 수수료 없이 → 누적 {pct(x['total'], 0)}, 최대낙폭 {pct(x['mdd'])}, 매매 {x['trades']}회\n")

    # 판단 요일 민감도
    L.append("\n## 주 1회 판단 요일별 결과 (누적 / 최대낙폭)\n")
    L.append("요일은 판단에 쓰는 일봉의 시작 요일입니다. 일요일 봉은 월요일 09:00 KST에 마감합니다.\n")
    hdr = "| 판단 봉 | " + " | ".join(f"{p[0]} 120일선 | {p[0]} 200일선" for p in periods[1:3]) + " |"
    L.append(hdr + "\n|" + " --- |" * (1 + 2 * 2))
    for wd in range(7):
        cells = []
        for _, start in periods[1:3]:
            k, d, c = window(start)
            for n in (120, 200):
                x = stats(d, c, weekly(d, above(n, k), wd))
                cells.append(f"{pct(x['total'], 0)} / {pct(x['mdd'], 0)}")
        L.append(f"| {WEEKDAYS[wd]} | " + " | ".join(cells) + " |")

    # 이평선 기간 민감도
    L.append("\n## 이평선 기간별 결과 (누적 / 최대낙폭 / 매매)\n")
    L.append("| 기간 | " + " | ".join(f"{p[0]} 매일 | {p[0]} 주1회" for p in periods[1:3]) + " |\n|" + " --- |" * 5)
    for n in (60, 90, 100, 120, 150, 200, 250):
        cells = []
        for _, start in periods[1:3]:
            k, d, c = window(start)
            for p in (above(n, k), weekly(d, above(n, k))):
                x = stats(d, c, p)
                cells.append(f"{pct(x['total'], 0)} / {pct(x['mdd'], 0)} / {x['trades']}")
        L.append(f"| {n}일 | " + " | ".join(cells) + " |")

    # 연도별 수익률
    L.append("\n## 연도별 수익률\n")
    k, d, c = window(periods[0][1])
    picks = {
        "계속 보유": [1] * len(d),
        "매일 120일선": above(120, k),
        "매일 200일선": above(200, k),
        "주1회 120일선": weekly(d, above(120, k)),
        "주1회 200일선": weekly(d, above(200, k)),
    }
    L.append("| 연도 | " + " | ".join(picks) + " |\n|" + " --- |" * (len(picks) + 1))
    for y in range(d[0].year, d[-1].year + 1):
        idx = [i for i, dd in enumerate(d) if dd.year == y]
        lo, hi = max(idx[0] - 1, 0), idx[-1]  # 전년 마지막 종가부터
        cells = []
        for p in picks.values():
            x = stats(d[lo:hi + 1], c[lo:hi + 1], p[lo:hi + 1])
            cells.append(pct(x["total"], 0))
        L.append(f"| {y}{'(진행 중)' if y == d[-1].year else ''} | " + " | ".join(cells) + " |")

    text = "\n".join(L) + "\n"
    print(text)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main()
