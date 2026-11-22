"""아침 지표 대시보드용 데이터를 야후 파이낸스에서 받아 data.json 으로 저장한다.

지표
  - S&P500(^GSPC), 나스닥100(^NDX), SOXL, 코스피(^KS11): 최근 종가, 전일 대비, 종가 기준 전고점과 전고점 대비 %
  - 원달러(KRW=X): 직전 서울 외환시장 마감 시점(15:30 KST) 환율, 최근 3개 연도 연평균과 3개년 평균,
    최근 3년(롤링) 평균
  - 차트용 최근 1년 종가

표준 라이브러리만 사용한다. 지표를 늘리려면 INDEXES 에 추가한다.
"""
import csv
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?{span}&interval={itv}"

# (키, 야후 티커, 표시 이름)
INDEXES = [
    ("sp500", "^GSPC", "S&P 500"),
    ("ndx", "^NDX", "나스닥 100"),
    ("soxl", "SOXL", "SOXL"),
    ("kospi", "^KS11", "코스피"),
]


def get_chart(sym, rng, itv):
    # range=max 나 긴 기간은 야후가 월봉으로 바꿔 주므로, 일봉은 period1/period2 로 요청한다
    if rng == "max":
        span = f"period1=0&period2={int(time.time())}"
    else:
        span = f"range={rng}"
    url = CHART.format(sym=urllib.request.quote(sym), span=span, itv=itv)
    last = None
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.load(r)["chart"]["result"][0]
            return res
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"{sym} {rng} {itv}: {last}")


def series(res):
    """(timestamp, close) 목록. 종가가 없는 행은 뺀다."""
    ts = res.get("timestamp") or []
    closes = res["indicators"]["quote"][0].get("close") or []
    return [(t, c) for t, c in zip(ts, closes) if c is not None]


DECIMAL1 = {"soxl"}  # 가격을 소수점 첫째 자리까지 보여 줄 종목
MAS = (50, 200)  # 차트 이동평균선(거래일 기준)


def with_ma(points, keep):
    """(ts, close) 전체 목록에 이동평균을 붙여 마지막 keep 개만 돌려준다: [(ts, close, ma50, ma200)]."""
    closes = [c for _, c in points]
    out = []
    for i in range(max(0, len(points) - keep), len(points)):
        row = [points[i][0], points[i][1]]
        for n in MAS:
            row.append(sum(closes[i - n + 1:i + 1]) / n if i + 1 >= n else None)
        out.append(row)
    return out


def hist_rows(points, res, keep):
    r2 = lambda x: round(x, 2) if x is not None else None
    return [[local_date(t, res), r2(c)] + [r2(m) for m in mas] for t, c, *mas in with_ma(points, keep)]


def local_date(ts, res):
    off = res["meta"].get("gmtoffset", 0)
    return datetime.fromtimestamp(ts + off, tz=timezone.utc).strftime("%Y-%m-%d")


def index_block(sym, name):
    res = get_chart(sym, "max", "1d")
    assert res["meta"].get("dataGranularity") == "1d", f"{sym}: 일봉이 아님"
    s = series(res)
    t_last, c_last = s[-1]
    _, c_prev = s[-2]
    t_ath, c_ath = max(s, key=lambda x: x[1])
    return {
        "name": name,
        "ticker": sym,
        "date": local_date(t_last, res),
        "close": round(c_last, 2),
        "prev_close": round(c_prev, 2),
        "change_pct": round((c_last / c_prev - 1) * 100, 2),
        "ath_close": round(c_ath, 2),
        "ath_date": local_date(t_ath, res),
        "from_ath_pct": round((c_last / c_ath - 1) * 100, 2),
        "history": hist_rows(s, res, 252),
    }


def fx_block(now):
    # 1) 직전 15:30 KST 환율: 30분봉 중 15:00~15:30 KST 봉의 종가
    intraday = get_chart("KRW=X", "5d", "30m")
    at_1530 = None
    for t, c in series(intraday):
        start = datetime.fromtimestamp(t, tz=KST)
        if start.hour == 15 and start.minute == 0:
            at_1530 = (start.strftime("%Y-%m-%d"), c)
    # 2) 연평균: 일봉 종가 평균
    daily = get_chart("KRW=X", "max", "1d")
    s = series(daily)
    by_year = {}
    for t, c in s:
        y = datetime.fromtimestamp(t, tz=KST).year
        by_year.setdefault(y, []).append(c)
    years = [now.year - 3, now.year - 2, now.year - 1]
    yearly = {str(y): round(sum(by_year[y]) / len(by_year[y]), 2) for y in years if by_year.get(y)}
    all3 = [c for y in years for c in by_year.get(y, [])]
    cutoff = (now - timedelta(days=365 * 3)).timestamp()
    rolling = [c for t, c in s if t >= cutoff]
    avg3 = round(sum(all3) / len(all3), 2) if all3 else None
    rate = round(at_1530[1], 2) if at_1530 else None
    return {
        "name": "원달러 환율",
        "ticker": "KRW=X",
        "basis": "서울 외환시장 마감 시점 15:30 KST (야후 30분봉)",
        "date": at_1530[0] if at_1530 else None,
        "rate_1530": rate,
        "latest": round(intraday["meta"]["regularMarketPrice"], 2),
        "yearly_avg": yearly,
        "avg_3y": avg3,
        "avg_3y_label": f"{years[0]}–{years[-1]} 일평균",
        "vs_avg_3y_pct": round((rate / avg3 - 1) * 100, 2) if rate and avg3 else None,
        "avg_rolling_3y": round(sum(rolling) / len(rolling), 2) if rolling else None,
        "history": hist_rows(s, daily, 300),
    }


def write_dashboard_files(data):
    """대시보드가 읽는 CSV 3개: indexes.csv, fx.csv, history.csv (out/ 폴더)."""
    out = os.path.join(os.path.dirname(OUT), "out")
    os.makedirs(out, exist_ok=True)
    cols = ["key", "name", "ticker", "date", "close", "prev_close", "change_pct", "ath_close", "ath_date", "from_ath_pct"]
    with open(os.path.join(out, "indexes.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for key, _, _ in INDEXES:
            v = data[key]
            row = [key]
            for c in cols[1:]:
                x = v[c]
                if c in ("close", "prev_close", "ath_close"):
                    x = round(x, 1) if key in DECIMAL1 else int(round(x))
                elif c in ("change_pct", "from_ath_pct"):
                    x = round(x, 1)
                row.append(x)
            w.writerow(row)
    fx = data["usdkrw"]
    with open(os.path.join(out, "fx.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["item", "label", "date", "rate"])
        r0 = lambda x: int(round(x)) if x is not None else ""
        w.writerow(["rate_1530", "15:30 KST 환율", fx["date"], r0(fx["rate_1530"])])
        w.writerow(["avg_rolling_3y", "최근 3년 롤링 평균", "", r0(fx["avg_rolling_3y"])])
    hist = {}
    keys = [k for k, _, _ in INDEXES] + ["usdkrw"]
    cols = []
    for k in keys:
        cols += [k] + [f"{k}_ma{n}" for n in MAS]
        for d, c, *mas in data[k]["history"]:
            row = hist.setdefault(d, {})
            row[k] = c
            for n, m in zip(MAS, mas):
                row[f"{k}_ma{n}"] = m
    first = data[INDEXES[0][0]]["history"][0][0]
    with open(os.path.join(out, "history.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date"] + cols)
        for d in sorted(hist):
            if d >= first:
                w.writerow([d] + [("" if hist[d].get(c) is None else hist[d][c]) for c in cols])


def main():
    now = datetime.now(KST)
    data = {"generated_at": now.strftime("%Y-%m-%d %H:%M KST"), "source": "Yahoo Finance"}
    for key, sym, name in INDEXES:
        data[key] = index_block(sym, name)
    data["usdkrw"] = fx_block(now)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    write_dashboard_files(data)
    summary = {k: v for k, v in data.items() if not isinstance(v, dict)}
    for k, v in data.items():
        if isinstance(v, dict):
            summary[k] = {kk: vv for kk, vv in v.items() if kk != "history"}
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
