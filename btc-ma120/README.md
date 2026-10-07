# 비트코인 120일 이동평균선 크로스 알림

현재가가 일봉 종가 기준 120일 이동평균선을 위로 뚫거나(골든크로스) 아래로 내려가면(데드크로스) 알려 줍니다.

| 구성 | 동작 방식 | 알림 수단 |
| --- | --- | --- |
| `index.html` | 브라우저에서 1/5/15/60분마다 점검 (페이지가 열려 있을 때만) | 브라우저 알림 + 알림음 |
| `check.py` + `.github/workflows/btc-ma120.yml` | GitHub Actions가 매시간 점검 (PC를 꺼 둬도 동작) | 텔레그램 |

## 판정 기준
- 이동평균 = 최근 120개 일봉 종가 평균. 진행 중인 오늘 봉은 현재가로 계산합니다(업비트 일봉 기준 시각은 09:00 KST).
- 직전에 저장된 위치(위/아래)와 이번 위치가 다를 때만 알립니다. 처음 실행할 때는 위치만 저장합니다.
- `여유 폭(BAND_PCT)`: 이평선 근처에서 오르내릴 때 알림이 반복되는 것을 막습니다. 예를 들어 1이면 이평선 ±1%를 넘어야 위치가 바뀝니다.

## 텔레그램 알림 설정 (GitHub Actions)
1. 텔레그램에서 `@BotFather`로 봇을 만들고 토큰을 받습니다.
2. 만든 봇에게 아무 메시지를 보낸 뒤 `https://api.telegram.org/bot<토큰>/getUpdates`에서 `chat.id`를 확인합니다.
3. 저장소 Settings → Secrets and variables → Actions에 Secret 두 개를 등록합니다.
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
4. (선택) 같은 화면 Variables 탭에서 `BTC_SOURCE`(`upbit`/`binance`), `BTC_MA_DAYS`(기본 120), `BTC_BAND_PCT`(기본 0)를 바꿀 수 있습니다.
5. 워크플로는 저장소의 기본 브랜치(현재 `claude/nifty-darwin-5pafy8`)에 있어야 예약 실행됩니다. Actions 탭에서 `BTC MA120 cross check` → Run workflow → `notify_status` 체크로 텔레그램 연결을 테스트할 수 있습니다.

참고: 위치가 바뀔 때마다 `btc-ma120/state.json`이 자동 커밋됩니다. 공개 저장소는 60일 동안 커밋이 없으면 GitHub이 예약 실행을 멈추므로, 30일 넘게 커밋이 없으면 `btc-ma120/keepalive.txt`에 시각을 적어 자동 커밋합니다.

## 로컬 실행
```bash
python btc-ma120/check.py               # 콘솔 출력
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python btc-ma120/check.py
```
`index.html`은 브라우저로 바로 열면 됩니다.
