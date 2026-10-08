"""비트코인 120일·200일선 '여유 폭 2%' 매일 신호를 계산해 텔레그램으로 알린다 (반자동 매매용).

규칙 (btc-upbit-ma/backtest.py 의 '여유 폭 2%'와 동일)
  - 매일 09:00 KST 업비트 일봉 마감 직후, 마감된 전일 종가로 판단
  - 현금일 때: 종가가 120일선과 200일선을 둘 다 2% 넘게 웃돌면 매수
  - 보유일 때: 종가가 120일선이나 200일선 중 하나라도 2% 넘게 밑돌면 매도
  - 그 사이에서는 직전 포지션을 유지한다
  포지션은 2022-01-03부터 같은 규칙으로 다시 계산해 정하므로 상태 파일이 어긋나도 스스로 맞춰진다.

표준 라이브러리만 사용한다. 환경 변수:
  BAND                여유 폭(기본 0.02)
  MANUAL              1이면 수동 실행: 처리 여부와 상관없이 현재 상태를 알리고 상태 파일은 바꾸지 않음
  TELEGRAM_BOT_TOKEN  텔레그램 봇 토큰(없으면 콘솔 출력만)
  TELEGRAM_CHAT_ID    알림 받을 채팅 ID
  STATE_FILE          상태 저장 파일(기본 btc-ma-band-daily/state.json)
  NOW                 테스트용 현재 시각(KST, 예: 2026-10-12T09:05). 평소에는 비워 둔다
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
BAND = float(os.environ.get("BAND", "0.02"))
MANUAL = os.environ.get("MANUAL") == "1"
STATE_FILE = os.environ.get(
    "STATE_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
)
REPLAY_FROM = date(2022, 1, 3)
UPBIT = "https://api.upbit.com/v1/candles/days"


def get_json(url):
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "btc-ma-band-daily"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception:
            if i == 3:
                raise
            time.sleep(2 ** (i + 1))


def fetch_completed_closes(now, since):
    """since 이후(이평선 계산용 여유 포함) 마감된 일봉 [(KST 시작일, 종가)] 과거→현재."""
    candles, to = {}, None
    for _ in range(40):
        url = f"{UPBIT}?market=KRW-BTC&count=200" + (f"&to={urllib.parse.quote(to)}" if to else "")
        page = get_json(url)
        for r in page:
            start = datetime.fromisoformat(r["candle_date_time_kst"]).replace(tzinfo=KST)
            if start + timedelta(days=1) <= now:
                candles[start.date()] = float(r["trade_price"])
        if len(page) < 200 or min(candles) <= since:
            break
        to = page[-1]["candle_date_time_utc"].replace("T", " ")
        time.sleep(0.15)
    return sorted(candles.items())


def replay(series, band=BAND):
    """일별 (날짜, 종가, 120일선, 200일선, 포지션) 목록. 포지션 1=보유, 0=현금."""
    closes = [c for _, c in series]
    out, state = [], 0
    for i, (d, c) in enumerate(series):
        if i < 199 or d < REPLAY_FROM:
            continue
        m120 = sum(closes[i - 119:i + 1]) / 120
        m200 = sum(closes[i - 199:i + 1]) / 200
        if state == 0 and c > m120 * (1 + band) and c > m200 * (1 + band):
            state = 1
        elif state == 1 and (c <= m120 * (1 - band) or c <= m200 * (1 - band)):
            state = 0
        out.append((d, c, m120, m200, state))
    return out


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


def gap(c, m):
    return f"{(c / m - 1) * 100:+.2f}%"


def message(kind, row, held_since):
    d, c, m120, m200, pos = row
    pct = BAND * 100
    head = {
        "buy": "🟢 매수 신호 · BTC를 매수하세요",
        "sell": "🔴 매도 신호 · BTC를 전량 매도하세요",
        "keep": "⚪ 유지 · " + ("BTC 보유를 계속하세요" if pos else "현금을 계속 유지하세요"),
        "manual": "ℹ️ 상태 점검 (수동 실행) · 지금 포지션: " + ("보유" if pos else "현금"),
    }[kind]
    lines = [
        head,
        f"{d} 종가 {won(c)}",
        f"120일선 {won(m120)} ({gap(c, m120)}) · 200일선 {won(m200)} ({gap(c, m200)})",
    ]
    if pos:
        line = max(m120, m200) * (1 - BAND)
        lines.append(f"매도 기준: 종가 {won(line)} 이하 (높은 이평선 -{pct:g}%, 지금보다 {gap(line, c)})")
    else:
        line = max(m120, m200) * (1 + BAND)
        lines.append(f"매수 기준: 종가 {won(line)} 초과 (높은 이평선 +{pct:g}%, 지금보다 {gap(line, c)})")
    if held_since:
        lines.append(f"{'보유' if pos else '현금'} 중: {held_since} 일봉 종가부터")
    lines.append("다음 판단: 내일 09:00 마감 종가 기준")
    return "\n".join(lines)


def main():
    now = datetime.fromisoformat(os.environ["NOW"]).replace(tzinfo=KST) if os.environ.get("NOW") else datetime.now(KST)
    try:
        series = fetch_completed_closes(now, REPLAY_FROM - timedelta(days=210))
        rows = replay(series)
        if len(rows) < 2:
            raise RuntimeError(f"계산할 일봉이 부족합니다: {len(rows)}개")
        today, prev = rows[-1], rows[-2]
        if (now.date() - today[0]).days > 2:
            raise RuntimeError(f"최근 마감 일봉이 오래되었습니다: {today[0]}")
    except Exception as e:  # 업비트 장애, 데이터 이상 모두 알린다
        notify(f"⚠️ BTC 2% 여유 폭 신호 계산 실패\n{e}\n{now:%Y-%m-%d %H:%M} KST")
        sys.exit(1)

    pos = today[4]
    since = next((r[0] for r in reversed(rows) if r[4] != pos), None)
    held_since = (since + timedelta(days=1)).isoformat() if since else None
    print(f"[{now:%Y-%m-%d %H:%M} KST] {today[0]} 종가 {today[1]:,.0f} / 120 {today[2]:,.0f} / 200 {today[3]:,.0f} → {'보유' if pos else '현금'}")

    if MANUAL:
        notify(message("manual", today, held_since))
        return
    if today[0] != now.date() - timedelta(days=1):
        print("어제 일봉이 아직 마감되지 않았습니다 (09:00 KST 이후 다시 실행)")
        return
    state = load_state()
    if state.get("close_date") == today[0].isoformat():
        print("오늘은 이미 처리했습니다")
        return

    kind = "keep" if prev[4] == pos else ("buy" if pos else "sell")
    notify(message(kind, today, held_since))
    state.update(
        close_date=today[0].isoformat(),
        position="hold" if pos else "cash",
        close=today[1],
        ma120=round(today[2]),
        ma200=round(today[3]),
        since=held_since,
    )
    save_state(state)


if __name__ == "__main__":
    main()
