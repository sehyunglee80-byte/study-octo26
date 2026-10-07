"""비트코인 200일 이동평균 주간 신호를 계산해 텔레그램으로 알린다 (반자동 매매용).

규칙 (backtest_momentum.py 의 BTC 백테스트와 동일)
  - 매주 월요일 09:00 KST 업비트 일봉 마감 직후 판단
  - 마감된 전일(일요일) 종가 > 최근 200개 마감 종가 평균 이면 이번 주 보유, 아니면 현금
  - 지난주와 포지션이 달라지면 매수/매도 알림, 같으면 유지 알림

표준 라이브러리만 사용한다. 환경 변수:
  MA_DAYS             이동평균 기간(기본 200)
  MANUAL              1이면 수동 실행: 요일이나 처리 여부와 상관없이 현재 상태를 알리고 상태 파일은 바꾸지 않음
  TELEGRAM_BOT_TOKEN  텔레그램 봇 토큰(없으면 콘솔 출력만)
  TELEGRAM_CHAT_ID    알림 받을 채팅 ID
  STATE_FILE          상태 저장 파일(기본 btc-ma200-weekly/state.json)
  NOW                 테스트용 현재 시각(KST, 예: 2026-10-12T09:05). 평소에는 비워 둔다
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
MA_DAYS = int(os.environ.get("MA_DAYS", "200"))
MANUAL = os.environ.get("MANUAL") == "1"
STATE_FILE = os.environ.get(
    "STATE_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
)
UPBIT = "https://api.upbit.com/v1/candles/days"


def get_json(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "btc-ma200-weekly"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def fetch_completed_closes(now, need):
    """마감된 일봉 (날짜, 종가) 목록을 과거→현재 순서로 need개 이상 돌려준다.

    업비트 일봉은 09:00 KST에 시작해 다음 날 09:00에 마감한다. 진행 중인 봉은 뺀다.
    """
    candles, to = {}, None
    for _ in range(30):                               # 200개씩, 최대 30페이지
        url = f"{UPBIT}?market=KRW-BTC&count=200"
        if to:
            url += "&to=" + urllib.parse.quote(to)
        page = get_json(url)
        for r in page:
            start = datetime.fromisoformat(r["candle_date_time_kst"]).replace(tzinfo=KST)
            if start + timedelta(days=1) <= now:      # 마감된 봉만
                candles[start.date()] = float(r["trade_price"])
        if len(candles) >= need or len(page) < 200:
            break
        to = page[-1]["candle_date_time_utc"].replace("T", " ")
    return sorted(candles.items())


def position_from(closes, ma_days=MA_DAYS):
    """마감 종가 목록(과거→현재)으로 포지션을 정한다. 마지막 종가가 이평선 위면 hold."""
    window = closes[-ma_days:]
    ma = sum(window) / ma_days
    price = closes[-1]
    return ("hold" if price > ma else "cash"), price, ma


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


def won(v):
    return f"{v / 1e4:,.0f}만 원"


def message(kind, pos, price, ma, close_date, week):
    gap = (price / ma - 1) * 100
    head = {
        "buy": "🟢 매수 신호 · 이번 주 BTC를 매수하세요",
        "sell": "🔴 매도 신호 · 이번 주 BTC를 전량 매도하세요",
        "keep": "⚪ 유지 · " + ("BTC 보유를 계속하세요" if pos == "hold" else "현금을 계속 유지하세요"),
        "manual": "ℹ️ 상태 점검 (수동 실행) · 지금 판단하면: " + ("보유" if pos == "hold" else "현금"),
    }[kind]
    lines = [
        head,
        f"{close_date} 종가 {won(price)}",
        f"{MA_DAYS}일 이동평균 {won(ma)} (괴리 {gap:+.2f}%)",
    ]
    if kind in ("keep", "manual"):
        if pos == "hold":
            lines.append(f"종가가 {won(ma)} 아래로 마감하면 다음 월요일 매도 신호")
        else:
            lines.append(f"종가가 {won(ma)} 위로 마감하면 다음 월요일 매수 신호")
    if kind == "manual":
        lines.append(f"다음 판단: {week} (월) 09:00 마감 종가 기준")
    else:
        lines.append(f"기준 주: {week} (월)")
    return "\n".join(lines)


def main():
    now = datetime.fromisoformat(os.environ["NOW"]).replace(tzinfo=KST) if os.environ.get("NOW") else datetime.now(KST)
    try:
        series = fetch_completed_closes(now, MA_DAYS)
        if len(series) < MA_DAYS:
            raise RuntimeError(f"마감 일봉이 부족합니다: {len(series)}개")
        close_date = series[-1][0]
        if (now.date() - close_date).days > 2:
            raise RuntimeError(f"최근 마감 일봉이 오래되었습니다: {close_date}")
        pos, price, ma = position_from([c for _, c in series])
    except Exception as e:  # 업비트 장애, 데이터 이상 모두 알린다
        notify(f"⚠️ BTC 200일선 신호 계산 실패\n{e}\n{now:%Y-%m-%d %H:%M} KST")
        sys.exit(1)

    week = (close_date + timedelta(days=1)).isoformat()   # 판단하는 월요일
    if MANUAL:
        nxt = now.date() + timedelta(days=(7 - now.weekday()) % 7 or 7)
        notify(message("manual", pos, price, ma, close_date, nxt.isoformat()))
        return
    state = load_state()
    print(f"[{now:%Y-%m-%d %H:%M} KST] 종가 {price:,.0f} / MA{MA_DAYS} {ma:,.0f} → {pos} (기준 주 {week})")

    if now.weekday() != 0:
        print("월요일이 아니라 건너뜁니다 (수동 실행은 MANUAL=1)")
        return
    if close_date != now.date() - timedelta(days=1):
        print("일요일 일봉이 아직 마감되지 않았습니다 (09:00 KST 이후 다시 실행)")
        return
    if state.get("week") == week:
        print("이번 주는 이미 처리했습니다")
        return

    prev = state.get("position")
    kind = "keep" if prev in (None, pos) else ("buy" if pos == "hold" else "sell")
    notify(message(kind, pos, price, ma, close_date, week))
    state.update(week=week, position=pos, close=price, ma=round(ma), close_date=close_date.isoformat())
    if kind != "keep" or "changed_week" not in state:
        state["changed_week"] = week
    save_state(state)


if __name__ == "__main__":
    main()
