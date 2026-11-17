"""코스피 지수에 비트코인과 같은 120일·200일선 규칙을 적용해 2000년 이후를 분석한다.

데이터: Yahoo Finance ^KS11 일봉 종가(배당 미포함 지수). 이평선은 거래일 기준.
수수료: 매수·매도 각 0.05%(FEE 환경 변수로 변경). 현금 구간 이자는 넣지 않았다.
결과는 마크다운으로 출력하고 analysis.md로도 저장한다.
"""
import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "btc-upbit-ma"))
from backtest import drawdowns, equity, pct, sma, stats, weekly  # noqa: E402

KST = timezone(timedelta(hours=9))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis.md")
START = date(2000, 1, 4)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)", "Accept": "application/json"}


def fetch():
    p1 = int(datetime(1998, 1, 1, tzinfo=timezone.utc).timestamp())
    p2 = int(time.time()) + 86400
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/%5EKS11?period1={p1}&period2={p2}&interval=1d"
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                j = json.load(r)["chart"]["result"][0]
            break
        except Exception as e:
            if i == 3:
                raise
            print("retry", e)
            time.sleep(2 ** (i + 1))
    tz = timedelta(seconds=j["meta"].get("gmtoffset", 32400))
    rows = {}
    for t, c in zip(j["timestamp"], j["indicators"]["quote"][0]["close"]):
        if c:  # 빈 값과 0 제거
            rows[(datetime.fromtimestamp(t, timezone.utc) + tz).date()] = float(c)
    return sorted(rows.items())


def band_rule(c, m120, m200, band):
    out, st = [], 0
    for i in range(len(c)):
        if st == 0 and c[i] > m120[i] * (1 + band) and c[i] > m200[i] * (1 + band):
            st = 1
        elif st == 1 and (c[i] <= m120[i] * (1 - band) or c[i] <= m200[i] * (1 - band)):
            st = 0
        out.append(st)
    return out


def main():
    series = fetch()
    D = [d for d, _ in series]
    C = [c for _, c in series]
    M120, M200 = sma(C, 120), sma(C, 200)
    L = [f"# 코스피 120일·200일선 규칙 분석\n\n생성 {datetime.now(KST):%Y-%m-%d %H:%M} KST · Yahoo ^KS11 {D[0]} → {D[-1]} · "
         "이평선은 거래일 기준 · 수수료 매수·매도 각 0.05% · 배당과 현금 이자 미포함\n"]

    last = len(C) - 1
    hi = max(M120[last], M200[last])
    L.append(f"\n## 현재 ({D[last]} 종가)\n")
    L.append(f"종가 {C[last]:,.2f} · 120일선 {M120[last]:,.2f} ({pct(C[last] / M120[last] - 1, 2)}) · "
             f"200일선 {M200[last]:,.2f} ({pct(C[last] / M200[last] - 1, 2)})")

    def window(start):
        k = next(i for i, d in enumerate(D) if d >= start)
        return k, D[k:], C[k:], M120[k:], M200[k:]

    def rules(k, d, c, a, b):
        both = [int(c[i] > a[i] and c[i] > b[i]) for i in range(len(c))]
        return {
            "계속 보유": [1] * len(c),
            "둘 다 위 (버퍼 없음)": both,
            "둘 다 위 + 여유 폭 1%": band_rule(c, a, b, 0.01),
            "둘 다 위 + 여유 폭 2% (BTC 알림 규칙)": band_rule(c, a, b, 0.02),
            "둘 다 위 + 여유 폭 3%": band_rule(c, a, b, 0.03),
            "종가 > 120일선": [int(c[i] > a[i]) for i in range(len(c))],
            "종가 > 200일선": [int(c[i] > b[i]) for i in range(len(c))],
            "주 1회 · 종가 > 200일선 (금요일 종가)": weekly(d, [int(c[i] > b[i]) for i in range(len(c))], weekday=4),
        }

    # 현재 신호(2% 규칙은 2000년부터 이어 온 상태)
    k, d, c, a, b = window(START)
    band_now = band_rule(c, a, b, 0.02)[-1]
    L.append(f"\n2% 규칙 현재 포지션: **{'보유' if band_now else '현금'}** · "
             f"{'매도' if band_now else '매수'} 기준 {hi * (0.98 if band_now else 1.02):,.2f}\n")

    periods = [("2000년~", START), ("2010년~", date(2010, 1, 4)), ("2020년~", date(2020, 1, 2)),
               ("최근 5년", D[-1] - timedelta(days=365 * 5))]
    for name, start in periods:
        k, d, c, a, b = window(start)
        L.append(f"\n## {name} ({d[0]} → {d[-1]})\n")
        L.append("| 규칙 | 누적 | 연환산 | 최대낙폭 | 가장 긴 고점 회복 | 보유비중 | 매매 | 승률 |\n| --- | --- | --- | --- | --- | --- | --- | --- |")
        for label, p in rules(k, d, c, a, b).items():
            x = stats(d, c, p)
            eps, _ = drawdowns(d, equity(c, p))
            longest = max(((d[e[2]] if e[2] is not None else d[-1]) - d[e[0]]).days for e in eps) if eps else 0
            hold = label == "계속 보유"
            L.append(f"| {label} | {pct(x['total'], 0)} | {pct(x['cagr'])} | {pct(x['mdd'])} | {longest}일 | "
                     f"{x['exposure'] * 100:.0f}% | {'-' if hold else x['trades']} | {'-' if hold else f'{x['win'] * 100:.0f}%'} |")

    # 2% 규칙 매매 목록과 연도별
    k, d, c, a, b = window(START)
    p = band_rule(c, a, b, 0.02)
    L.append("\n## 2% 규칙 보유 구간 (2000년~)\n")
    L.append("| 매수일 | 매도일 | 보유일 | 매수 → 매도 | 수익률 |\n| --- | --- | --- | --- | --- |")
    entry = None
    for i in range(len(p)):
        prev = p[i - 1] if i else 0
        if p[i] and not prev:
            entry = i
        if prev and not p[i]:
            L.append(f"| {d[entry]} | {d[i]} | {(d[i] - d[entry]).days} | {c[entry]:,.0f} → {c[i]:,.0f} | "
                     f"{pct(c[i] / c[entry] * (1 - 0.0005) ** 2 - 1)} |")
    if p[-1]:
        L.append(f"| {d[entry]} | 보유 중 | {(d[-1] - d[entry]).days} | {c[entry]:,.0f} → {c[-1]:,.0f} | {pct(c[-1] / c[entry] - 1)} |")

    L.append("\n## 연도별 수익률\n")
    picks = {"계속 보유": [1] * len(c), "둘 다 위": [int(c[i] > a[i] and c[i] > b[i]) for i in range(len(c))], "2% 규칙": p}
    L.append("| 연도 | " + " | ".join(picks) + " |\n|" + " --- |" * (len(picks) + 1))
    for y in range(d[0].year, d[-1].year + 1):
        idx = [i for i, x in enumerate(d) if x.year == y]
        lo, hi_i = max(idx[0] - 1, 0), idx[-1]
        cells = []
        for q in picks.values():
            eq = equity(c[lo:hi_i + 1], q[lo:hi_i + 1])
            cells.append(pct(eq[-1] - 1, 0))
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
