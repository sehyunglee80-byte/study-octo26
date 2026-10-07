"""비트코인 시세와 120일 이동평균선의 크로스를 확인하고 텔레그램으로 알린다.

표준 라이브러리만 사용한다. 환경 변수:
  SOURCE              upbit(기본, KRW-BTC) 또는 binance(BTCUSDT)
  MA_DAYS             이동평균 기간(기본 120)
  BAND_PCT            크로스 판정 여유 폭 %(기본 0). 이평선 ±BAND_PCT% 를 넘어야 상태가 바뀐다.
  TELEGRAM_BOT_TOKEN  텔레그램 봇 토큰(없으면 콘솔 출력만)
  TELEGRAM_CHAT_ID    알림 받을 채팅 ID
  STATE_FILE          직전 상태 저장 파일(기본 btc-ma120/state.json)
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
SOURCE = os.environ.get("SOURCE", "upbit").lower()
MA_DAYS = int(os.environ.get("MA_DAYS", "120"))
BAND_PCT = float(os.environ.get("BAND_PCT", "0"))
STATE_FILE = os.environ.get(
    "STATE_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
)


def get_json(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "btc-ma120"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def fetch_closes():
    """과거→현재 순서의 일봉 종가 목록. 마지막 값은 진행 중인 오늘 봉(=현재가)."""
    if SOURCE == "binance":
        rows = get_json(
            f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit={MA_DAYS}"
        )
        return [float(r[4]) for r in rows], "BTC/USDT", "$"
    rows = get_json(f"https://api.upbit.com/v1/candles/days?market=KRW-BTC&count={MA_DAYS}")
    rows.reverse()  # 업비트는 최신순으로 내려준다
    return [float(r["trade_price"]) for r in rows], "BTC/KRW", "₩"


def judge(price, ma, prev):
    """여유 폭 안에서는 직전 상태를 유지한다(이평선 근처 잦은 알림 방지)."""
    upper, lower = ma * (1 + BAND_PCT / 100), ma * (1 - BAND_PCT / 100)
    if price > upper:
        return "above"
    if price < lower:
        return "below"
    return prev or ("above" if price >= ma else "below")


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


def notify(text):
    print(text)
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("(텔레그램 설정이 없어 콘솔에만 출력했습니다)")
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=20)


def main():
    closes, pair, unit = fetch_closes()
    if len(closes) < MA_DAYS:
        sys.exit(f"일봉 데이터가 부족합니다: {len(closes)}개")
    price = closes[-1]
    ma = sum(closes[-MA_DAYS:]) / MA_DAYS
    gap = (price / ma - 1) * 100

    state = load_state()
    key = f"{SOURCE}:{MA_DAYS}"
    prev = state.get(key, {}).get("position")
    now = judge(price, ma, prev)
    stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    print(f"[{stamp}] {pair} 현재가 {unit}{price:,.0f} / MA{MA_DAYS} {unit}{ma:,.0f} ({gap:+.2f}%) → {now}")

    if prev and prev != now:
        kind = "🟢 골든크로스(상향 돌파)" if now == "above" else "🔴 데드크로스(하향 이탈)"
        notify(
            f"{kind}\n{pair} 현재가 {unit}{price:,.0f}\n"
            f"{MA_DAYS}일 이동평균 {unit}{ma:,.0f}\n괴리율 {gap:+.2f}%\n{stamp}"
        )
    elif os.environ.get("NOTIFY_STATUS") == "1":
        notify(f"상태 점검: {pair} {unit}{price:,.0f}, MA{MA_DAYS} {unit}{ma:,.0f} ({gap:+.2f}%)")

    if prev != now:
        state[key] = {"position": now, "changed_at": stamp, "price": price, "ma": round(ma, 2)}
        save_state(state)


if __name__ == "__main__":
    main()
