"""개별 종목(M7) 대시보드용 데이터를 야후 파이낸스에서 받아 out/ 에 CSV 로 저장한다.

종목별 항목
  - 가격: 종가(정수), 시가총액(조 달러), 전일 대비, 종가 기준 전고점과 전고점 대비 %
  - 추세: 50일·200일 이동평균과 종가의 위치, 52주 고점·저점 사이 현재 위치(0% = 저점, 100% = 고점)
  - 밸류: 후행 PER, 선행 PER (야후 값)
  - 이익 전망: 다음 회계연도 EPS 컨센서스와 30일·90일 전 대비 변화율
  - 일정: 다음 실적 발표일
  - 차트용 최근 1년 종가와 이동평균

표준 라이브러리만 사용한다. 종목을 늘리려면 STOCKS 에 추가한다.
"""
import csv
import http.cookiejar
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36", "Accept": "*/*"}

# (야후 티커, 표시 이름)
STOCKS = [
    ("AAPL", "애플"),
    ("MSFT", "마이크로소프트"),
    ("GOOGL", "알파벳"),
    ("AMZN", "아마존"),
    ("META", "메타"),
    ("NVDA", "엔비디아"),
    ("TSLA", "테슬라"),
    ("000660.KS", "SK하이닉스"),
    ("SPCX", "SpaceX"),
    ("ANT", "Anthropic"),
]

# 야후 섹터 분류 → 대시보드 섹터 카드의 이름
SECTOR_KO = {
    "Technology": "IT", "Communication Services": "통신", "Consumer Cyclical": "경기소비",
    "Consumer Defensive": "필수소비", "Healthcare": "헬스케어", "Financial Services": "금융",
    "Industrials": "산업재", "Energy": "에너지", "Basic Materials": "소재", "Utilities": "유틸",
    "Real Estate": "부동산",
}
MAS = (50, 200)

_cj = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj))
_crumb = None


def get_json(url, tries=4):
    last = None
    for i in range(tries):
        try:
            with _opener.open(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def crumb():
    """quoteSummary 는 쿠키와 crumb 가 필요하다."""
    global _crumb
    if _crumb:
        return _crumb
    try:
        _opener.open(urllib.request.Request("https://fc.yahoo.com", headers=UA), timeout=30)
    except Exception:  # noqa: BLE001  (404 를 주지만 쿠키는 남는다)
        pass
    last = None
    for host in ("query2", "query1"):
        try:
            with _opener.open(urllib.request.Request(f"https://{host}.finance.yahoo.com/v1/test/getcrumb", headers=UA), timeout=30) as r:
                _crumb = r.read().decode().strip()
            if _crumb and "<" not in _crumb:
                return _crumb
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"crumb 실패: {last}")


def chart(sym):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1=0&period2={int(time.time())}&interval=1d"
    res = get_json(url)["chart"]["result"][0]
    assert res["meta"].get("dataGranularity") == "1d", f"{sym}: 일봉이 아님"
    off = res["meta"].get("gmtoffset", 0)
    chart.currency = res["meta"].get("currency")
    closes = res["indicators"]["quote"][0].get("close") or []
    pts = []
    for t, c in zip(res.get("timestamp") or [], closes):
        if c is not None:
            pts.append((datetime.fromtimestamp(t + off, tz=timezone.utc).strftime("%Y-%m-%d"), c))
    return pts


def summary(sym):
    mods = "summaryDetail,defaultKeyStatistics,earningsTrend,calendarEvents,assetProfile"
    c = urllib.request.quote(crumb())
    last = None
    for host in ("query2", "query1"):
        try:
            url = f"https://{host}.finance.yahoo.com/v10/finance/quoteSummary/{sym}?modules={mods}&crumb={c}"
            return get_json(url, tries=2)["quoteSummary"]["result"][0]
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(last)


def raw(d, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    if isinstance(d, dict):
        d = d.get("raw")
    return d


def ma(closes, i, n):
    return sum(closes[i - n + 1:i + 1]) / n if i + 1 >= n else None


def pct(a, b):
    return round((a / b - 1) * 100, 1) if a is not None and b else None


def stock_block(sym, name):
    pts = chart(sym)
    closes = [c for _, c in pts]
    i = len(pts) - 1
    d_last, c_last = pts[-1]
    c_prev = closes[-2] if len(closes) > 1 else None
    d_ath, c_ath = max(pts, key=lambda x: x[1])
    yr = closes[-252:]
    hi52, lo52 = max(yr), min(yr)
    ma50, ma200 = ma(closes, i, 50), ma(closes, i, 200)
    row = {
        "ticker": sym,
        "name": name,
        "currency": chart.currency,
        "sector": None,
        "date": d_last,
        "close": int(round(c_last)),
        "market_cap_tn": None,
        "change_pct": pct(c_last, c_prev),
        "ath_close": round(c_ath, 2),
        "ath_date": d_ath,
        "from_ath_pct": pct(c_last, c_ath),
        "high_52w": round(hi52, 2),
        "low_52w": round(lo52, 2),
        "pos_52w_pct": round((c_last - lo52) / (hi52 - lo52) * 100, 0) if hi52 > lo52 else None,
        "ma50": round(ma50, 2) if ma50 else None,
        "ma200": round(ma200, 2) if ma200 else None,
        "vs_ma50_pct": pct(c_last, ma50),
        "vs_ma200_pct": pct(c_last, ma200),
        "trailing_pe": None,
        "forward_pe": None,
        "eps_cur_fy": None,
        "eps_cur_fy_end": None,
        "eps_next_fy": None,
        "eps_next_fy_end": None,
        "eps_next_fy_analysts": None,
        "eps_30d_ago": None,
        "eps_90d_ago": None,
        "eps_rev_30d_pct": None,
        "eps_rev_90d_pct": None,
        "next_earnings": None,
    }
    try:
        s = summary(sym)
        sec = (s.get("assetProfile") or {}).get("sector")
        row["sector"] = SECTOR_KO.get(sec, sec)
        mc = raw(s, "summaryDetail", "marketCap")
        row["market_cap_tn"] = round(mc / 1e12, 2) if mc else None
        tpe = raw(s, "summaryDetail", "trailingPE")
        fpe = raw(s, "summaryDetail", "forwardPE") or raw(s, "defaultKeyStatistics", "forwardPE")
        row["trailing_pe"] = round(tpe, 1) if tpe else None
        row["forward_pe"] = round(fpe, 1) if fpe else None
        for t in (s.get("earningsTrend") or {}).get("trend", []):
            if t.get("period") == "0y":
                c0 = raw(t, "epsTrend", "current")
                row["eps_cur_fy"] = round(c0, 2) if c0 else None
                row["eps_cur_fy_end"] = t.get("endDate")
            if t.get("period") == "+1y":
                row["eps_next_fy_end"] = t.get("endDate")
                row["eps_next_fy_analysts"] = raw(t, "earningsEstimate", "numberOfAnalysts")
                cur = raw(t, "epsTrend", "current")
                e30, e90 = raw(t, "epsTrend", "30daysAgo"), raw(t, "epsTrend", "90daysAgo")
                row["eps_30d_ago"] = round(e30, 2) if e30 else None
                row["eps_90d_ago"] = round(e90, 2) if e90 else None
                row["eps_next_fy"] = round(cur, 2) if cur else None
                row["eps_rev_30d_pct"] = pct(cur, raw(t, "epsTrend", "30daysAgo"))
                row["eps_rev_90d_pct"] = pct(cur, raw(t, "epsTrend", "90daysAgo"))
        dates = ((s.get("calendarEvents") or {}).get("earnings") or {}).get("earningsDate") or []
        if dates:
            row["next_earnings"] = datetime.fromtimestamp(dates[0]["raw"], tz=KST).strftime("%Y-%m-%d")
    except Exception as e:  # noqa: BLE001  (밸류 항목만 비우고 가격은 그대로 낸다)
        print(f"{sym} quoteSummary 실패: {e}")
    hist = []
    for j in range(max(0, len(pts) - 252), len(pts)):
        r2 = lambda x: round(x, 2) if x is not None else ""
        hist.append((pts[j][0], r2(closes[j]), r2(ma(closes, j, 50)), r2(ma(closes, j, 200))))
    return row, hist


def main():
    os.makedirs(OUT, exist_ok=True)
    rows, hist = [], {}
    for sym, name in STOCKS:
        row, h = stock_block(sym, name)
        rows.append(row)
        for d, c, m50, m200 in h:
            hist.setdefault(d, {}).update({sym: c, f"{sym}_ma50": m50, f"{sym}_ma200": m200})
    with open(os.path.join(OUT, "stocks.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    cols = [c for sym, _ in STOCKS for c in (sym, f"{sym}_ma50", f"{sym}_ma200")]
    with open(os.path.join(OUT, "history.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date"] + cols)
        for d in sorted(hist):
            w.writerow([d] + [hist[d].get(c, "") for c in cols])
    meta = {"generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"), "source": "Yahoo Finance"}
    with open(os.path.join(HERE, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    print(json.dumps(meta, ensure_ascii=False))
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
