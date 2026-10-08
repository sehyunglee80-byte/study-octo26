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


def equity(closes, pos, fee=FEE):
    """stats()와 같은 규칙으로 날짜별 자산 곡선을 만든다(첫날 1.0)."""
    eq, out, prev = 1.0, [1.0], 0
    for i in range(len(closes) - 1):
        if pos[i] != prev:
            eq *= 1 - fee
            prev = pos[i]
        if pos[i]:
            eq *= closes[i + 1] / closes[i]
        out.append(eq)
    return out


def drawdowns(dates, eq):
    """고점→저점→회복 구간 목록과 일별 낙폭."""
    eps, dd, peak_i, trough_i = [], [], 0, 0
    for i, v in enumerate(eq):
        if v >= eq[peak_i]:
            if trough_i > peak_i and eq[trough_i] < eq[peak_i]:
                eps.append((peak_i, trough_i, i))
            peak_i = trough_i = i
        elif v < eq[trough_i]:
            trough_i = i
        dd.append(v / eq[peak_i] - 1)
    if trough_i > peak_i and eq[trough_i] < eq[peak_i]:
        eps.append((peak_i, trough_i, None))  # 아직 회복 못 함
    return eps, dd


def worst_window(eq, n):
    return min(eq[i + n] / eq[i] - 1 for i in range(len(eq) - n))


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

    # 하락 지표 비교
    L.append("\n## 하락 지표 비교\n")
    L.append("수수료 반영. '고점 아래 일수'는 자산이 직전 최고치보다 낮았던 날의 비율, '손실일'은 자산이 전날보다 줄어든 날의 비율입니다.\n")
    for name, start in periods:
        k, d, c = window(start)
        both = [a & b for a, b in zip(above(120, k), above(200, k))]
        cand = {"주 1회 200일선": weekly(d, above(200, k)), "매일 둘 다 위": both, "계속 보유(참고)": [1] * len(d)}
        rows, top = {}, {}
        for label, p in cand.items():
            eq = equity(c, p)
            eps, dd = drawdowns(d, eq)
            x = stats(d, c, p)
            n = len(eq) - 1
            deep = min(eps, key=lambda e: eq[e[1]] / eq[e[0]])
            rec = lambda e: (d[e[2]] - d[e[0]]).days if e[2] is not None else (d[-1] - d[e[0]]).days
            longest = max(eps, key=rec)
            rows[label] = [
                pct(x["mdd"]),
                f"{d[deep[0]]} → {d[deep[1]]} → {d[deep[2]] if deep[2] is not None else '미회복'}",
                f"{(d[deep[1]] - d[deep[0]]).days}일 / {f"{(d[deep[2]] - d[deep[1]]).days}일" if deep[2] is not None else "미회복"}",
                f"{rec(longest)}일 ({d[longest[0]]}~{d[longest[2]] if longest[2] is not None else '진행 중'})",
                f"{sum(v < 0 for v in dd) / len(dd) * 100:.0f}%",
                f"{sum(eq[i + 1] < eq[i] for i in range(n)) / n * 100:.0f}%",
                pct(sum(dd) / len(dd)),
                f"{sum(eq[e[1]] / eq[e[0]] - 1 <= -0.10 for e in eps)}회 / {sum(eq[e[1]] / eq[e[0]] - 1 <= -0.20 for e in eps)}회",
                f"{pct(worst_window(eq, 1))} / {pct(worst_window(eq, 7))} / {pct(worst_window(eq, 30))}",
                f"{x['cagr'] / abs(x['mdd']):.2f}" if x["mdd"] else "-",
                pct(dd[-1]),
            ]
            top[label] = sorted(eps, key=lambda e: eq[e[1]] / eq[e[0]])[:3], eq
        L.append(f"\n### {name} ({d[0]} → {d[-1]})\n")
        L.append("| 지표 | " + " | ".join(cand) + " |\n|" + " --- |" * (len(cand) + 1))
        names = ["최대낙폭(MDD)", "MDD 고점 → 저점 → 회복", "하락 기간 / 회복 기간", "가장 긴 고점 회복 기간",
                 "고점 아래 일수", "손실일", "평균 낙폭", "-10% 이상 / -20% 이상 하락 횟수",
                 "최악의 1일 / 7일 / 30일", "연환산 ÷ MDD", "현재 낙폭"]
        for j, nm in enumerate(names):
            L.append(f"| {nm} | " + " | ".join(rows[lb][j] for lb in cand) + " |")
        for label in list(cand)[:2]:
            eps3, eq = top[label]
            L.append(f"\n{label} 큰 하락 3개: " + "; ".join(
                f"{pct(eq[e[1]] / eq[e[0]] - 1)} ({d[e[0]]}~{d[e[1]]}, 회복 {d[e[2]] if e[2] is not None else '안 됨'})" for e in eps3))

    # 두 전략이 갈리는 구간: 200일선 위 & 120일선 아래 (매일 판단)
    L.append("\n## '200일선만 위'와 '둘 다 위'가 갈리는 구간 (매일 판단)\n")
    L.append("종가가 200일선 위, 120일선 아래인 날에만 두 전략이 다릅니다(200일선만 위 = 보유, 둘 다 위 = 현금). "
             "구간 수익률은 그 기간 BTC 등락이며, 플러스면 '200일선만 위'가 유리했던 구간입니다.\n")
    k = first_valid
    eps, i = [], k
    while i < len(C) - 1:
        if MA[200][i] < C[i] <= MA[120][i]:
            j, r = i, 1.0
            while j < len(C) - 1 and MA[200][j] < C[j] <= MA[120][j]:
                r *= C[j + 1] / C[j]
                j += 1
            end = "진행 중" if j == len(C) - 1 and MA[200][j] < C[j] <= MA[120][j] else (
                "120일선 위로 복귀" if C[j] > MA[120][j] else "200일선 아래로 이탈")
            eps.append((D[i], D[j], j - i, r - 1, end))
            i = j
        else:
            i += 1
    L.append("| 시작(종가 기준) | 종료 | 일수 | 구간 BTC 등락 | 끝난 방식 |\n| --- | --- | --- | --- | --- |")
    for a, b, n, r, e in eps:
        if n >= 3 or abs(r) >= 0.03:
            L.append(f"| {a} | {b} | {n} | {pct(r)} | {e} |")
    up = [x for x in eps if x[4] == "120일선 위로 복귀"]
    dn = [x for x in eps if x[4] == "200일선 아래로 이탈"]
    tot = 1.0
    for x in eps:
        tot *= 1 + x[3]
    days = sum(x[2] for x in eps)
    L.append(f"\n- 전체 {len(eps)}구간, {days}일({days / (len(C) - 1 - k) * 100:.1f}%). 3일 미만이면서 등락 3% 미만인 짧은 구간은 표에서 뺐습니다.")
    L.append(f"- 120일선 위로 복귀하며 끝남 {len(up)}회(평균 {pct(sum(x[3] for x in up) / len(up) if up else float('nan'))}), "
             f"200일선 아래로 이탈하며 끝남 {len(dn)}회(평균 {pct(sum(x[3] for x in dn) / len(dn) if dn else float('nan'))})")
    L.append(f"- 이 구간들을 모두 보유했을 때 누적 {pct(tot - 1)} (플러스면 200일선만 위가 유리, 수수료 제외)")
    L.append(f"- 현재: {'두 전략 같음 (120일선 < 200일선이거나 종가가 120일선 위)' if not (MA[200][-1] < C[-1] <= MA[120][-1]) else '갈리는 구간 안'}")
    for name, start in periods:
        sub = [x for x in eps if x[0] >= start]
        t = 1.0
        for x in sub:
            t *= 1 + x[3]
        L.append(f"  - {name}: {len(sub)}구간, 누적 {pct(t - 1)}")

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

    # 사고팔기 반복을 줄이는 장치 비교 (2022년~, 매일 '둘 다 위')
    k, d, c = window(date(2022, 1, 3))
    m120, m200 = MA[120][k:], MA[200][k:]

    def filtered(band_in, band_out, n_in, n_out):
        """band: 이평선 대비 여유 폭(0.01 = 1%), n: 조건이 며칠 연속이어야 바꾸는지."""
        out, state, cnt = [], 0, 0
        for i in range(len(c)):
            if state == 0:
                ok = c[i] > m120[i] * (1 + band_in) and c[i] > m200[i] * (1 + band_in)
            else:
                ok = c[i] <= m120[i] * (1 - band_out) or c[i] <= m200[i] * (1 - band_out)
            cnt = cnt + 1 if ok else 0
            if cnt >= (n_in if state == 0 else n_out):
                state, cnt = 1 - state, 0
            out.append(state)
        return out

    def trade_list(p):
        res, entry = [], None
        for i in range(len(p)):
            prev = p[i - 1] if i else 0
            if p[i] and not prev:
                entry = i
            if prev and not p[i]:
                res.append(((d[i] - d[entry]).days, c[i] / c[entry] * (1 - FEE) ** 2 - 1))
        return res

    variants = [
        ("기본 (여유 폭 없음, 당일)", filtered(0, 0, 1, 1)),
        ("여유 폭 0.5% (사고팔 때 모두)", filtered(0.005, 0.005, 1, 1)),
        ("여유 폭 1%", filtered(0.01, 0.01, 1, 1)),
        ("여유 폭 1.5%", filtered(0.015, 0.015, 1, 1)),
        ("여유 폭 2%", filtered(0.02, 0.02, 1, 1)),
        ("여유 폭 2.5%", filtered(0.025, 0.025, 1, 1)),
        ("여유 폭 3%", filtered(0.03, 0.03, 1, 1)),
        ("여유 폭 1% (살 때만)", filtered(0.01, 0, 1, 1)),
        ("여유 폭 2% (살 때만)", filtered(0.02, 0, 1, 1)),
        ("여유 폭 2% (팔 때만)", filtered(0, 0.02, 1, 1)),
        ("이틀 연속 (사고팔 때 모두)", filtered(0, 0, 2, 2)),
        ("사흘 연속 (사고팔 때 모두)", filtered(0, 0, 3, 3)),
        ("이틀 연속 (살 때만)", filtered(0, 0, 2, 1)),
        ("이틀 연속 (팔 때만)", filtered(0, 0, 1, 2)),
        ("여유 폭 1% + 이틀 연속", filtered(0.01, 0.01, 2, 2)),
        ("참고: 주 1회 200일선", weekly(d, above(200, k))),
    ]
    L.append("\n## 사고팔기 반복 줄이기: 여유 폭 vs 연속 일수 (2022-01-03 → 최근, 매일 '둘 다 위')\n")
    L.append("여유 폭: 살 때는 종가가 두 이평선보다 X% 이상 위, 팔 때는 어느 한 이평선보다 X% 이상 아래여야 바꿉니다. "
             "연속: 조건이 N일 연속 맞아야 바꿉니다. 수수료 반영.\n")
    L.append("| 방식 | 누적 | 최대낙폭 | 가장 긴 고점 회복 | 매매 | 승률 | 7일 내 단기 매매 (합계) | 현재 |\n| --- | --- | --- | --- | --- | --- | --- | --- |")
    for label, p in variants:
        x = stats(d, c, p)
        eq = equity(c, p)
        eps, _ = drawdowns(d, eq)
        longest = max(((d[e[2]] if e[2] is not None else d[-1]) - d[e[0]]).days for e in eps) if eps else 0
        tl = trade_list(p)
        sh = [r for n, r in tl if n <= 7]
        shs = 1.0
        for r in sh:
            shs *= 1 + r
        L.append(f"| {label} | {pct(x['total'], 0)} | {pct(x['mdd'])} | {longest}일 | {x['trades']} | {x['win'] * 100:.0f}% | "
                 f"{len(sh)}번 ({pct(shs - 1)}) | {'보유' if p[-1] else '현금'} |")

    # 여유 폭 촘촘히: 2%가 우연인지 확인
    L.append("\n## 여유 폭 촘촘히 (0.25% 간격, 사고팔 때 모두)\n")
    L.append("시작일을 바꿔도 같은 경향인지 함께 봅니다. 칸 = 누적 / 최대낙폭 / 매매 횟수\n")
    shift = [date(2022, 1, 3), date(2022, 7, 1), date(2023, 1, 2), date(2023, 7, 3)]
    L.append("| 여유 폭 | " + " | ".join(f"{x} 시작" for x in shift) + " |\n|" + " --- |" * (len(shift) + 1))
    full_pos = {}
    for b in [i * 0.0025 for i in range(17)]:
        full_pos[b] = filtered(b, b, 1, 1)
    for b, p in full_pos.items():
        cells = []
        for st in shift:
            j = next(i for i, x in enumerate(d) if x >= st)
            # 필터 상태는 2022-01-03부터 이어 오고, 시작일에 보유 상태면 그날 종가에 산 것으로 본다
            pp = p[j:]
            x = stats(d[j:], c[j:], pp)
            cells.append(f"{pct(x['total'], 0)} / {pct(x['mdd'], 0)} / {x['trades']}")
        L.append(f"| {b * 100:.2f}% | " + " | ".join(cells) + " |")

    # 차트용 데이터: 2022-01-03부터, 매일 '둘 다 위' 전략
    k, d, c = window(date(2022, 1, 3))
    both = [a & b for a, b in zip(above(120, k), above(200, k))]
    w200 = weekly(d, above(200, k))
    trades, entry = [], None
    for i in range(len(both)):
        prev = both[i - 1] if i else 0
        if both[i] and not prev:
            entry = i
        if prev and not both[i]:
            trades.append({"buy": d[entry].isoformat(), "buyPrice": c[entry], "sell": d[i].isoformat(), "sellPrice": c[i],
                           "ret": round((c[i] / c[entry]) * (1 - FEE) ** 2 - 1, 5), "days": (d[i] - d[entry]).days})
    if both[-1]:
        trades.append({"buy": d[entry].isoformat(), "buyPrice": c[entry], "sell": None, "sellPrice": c[-1],
                       "ret": round((c[-1] / c[entry]) * (1 - FEE) - 1, 5), "days": (d[-1] - d[entry]).days})
    chart = {
        "generated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "fee": FEE,
        "dates": [x.isoformat() for x in d],
        "close": [round(x) for x in c],
        "ma120": [round(MA[120][i]) for i in range(k, len(C))],
        "ma200": [round(MA[200][i]) for i in range(k, len(C))],
        "pos": both,
        "eqBoth": [round(x, 5) for x in equity(c, both)],
        "eqWeekly200": [round(x, 5) for x in equity(c, w200)],
        "eqHold": [round(x, 5) for x in equity(c, [1] * len(c))],
        "trades": trades,
    }
    with open(os.path.join(os.path.dirname(OUT), "chart-data.json"), "w", encoding="utf-8") as f:
        json.dump(chart, f, separators=(",", ":"))

    text = "\n".join(L) + "\n"
    print(text)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main()
