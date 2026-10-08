# 업비트 KRW-BTC 이동평균 전략 백테스트

업비트 원화 일봉으로 120일·200일선 조건별 보유 전략을 비교합니다. 수수료 0.05%(매수·매도 각각)를 반영합니다.

- `backtest.py`: 매일·주 1회 판단 비교, 판단 요일과 이평선 기간 민감도, 하락 지표(MDD, 회복 기간 등),
  여유 폭과 연속 일수 비교를 `analysis.md`로 출력하고 차트용 `chart-data.json`을 만듭니다.
- 업비트 API가 필요해 GitHub Actions에서 실행합니다. Actions 탭 → `BTC Upbit MA backtest` → Run workflow.
  결과는 실행 요약(Summary)과 `btc-upbit-ma-backtest` 아티팩트에 있습니다.
- 여기서 고른 규칙(여유 폭 2%)의 매일 알림은 `btc-ma-band-daily/`에 있습니다.
