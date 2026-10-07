"""
대형 알트 유니버스 백테스트 (backtest_momentum.py 의 시뮬레이션 재사용)

  - 유니버스: 전년도 말 글로벌 시총 순위 기준, 업비트 원화마켓에 있는 알트 상위 20개
              (BTC, 스테이블코인, 래핑 토큰 제외 / 업비트 미상장 코인은 건너뜀)
              해당 연도 동안 목록 고정, 상장 후 60일 미만 코인은 제외
  - BTC 필터: 120일 이동평균 고정
  - 나머지 파라미터(룩백, 손절, 익절, 플러스만 편입)는 기존 그리드 그대로

먼저 python backtest_momentum.py 를 실행해 data/upbit_daily.csv 를 만들어 두어야 합니다.
결과: results/summary_largecap.txt, results/grid_largecap.csv, results/yearly_largecap.csv
"""

import os

import numpy as np
import pandas as pd

import backtest_momentum as bm

# 연말 글로벌 시총 순위 (업비트 원화마켓 상장 알트만, 순위순).
# 출처: 작성자 기억 기반 근사치. 코인마켓캡 연말 스냅샷으로 검증 필요.
# EOS 는 업비트에서 A(Vaulta), MATIC 은 POL 로 이어짐.
YEAR_END_TOP = {
    2020: ["XRP", "BCH", "LINK", "ADA", "DOT", "XLM", "BSV", "A", "TRX", "XTZ",
           "THETA", "CRO", "VET", "ATOM", "NEO", "IOTA", "ETC", "WAVES", "ZIL", "BAT"],
    2021: ["SOL", "ADA", "XRP", "DOT", "DOGE", "POL", "CRO", "LINK", "ALGO", "NEAR",
           "TRX", "BCH", "ATOM", "XLM", "MANA", "AXS", "HBAR", "VET", "SAND", "ETC"],
    2022: ["XRP", "DOGE", "ADA", "POL", "DOT", "TRX", "SOL", "AVAX", "LINK", "ATOM",
           "ETC", "BCH", "XLM", "ALGO", "CRO", "NEAR", "VET", "HBAR", "A", "CHZ"],
    2023: ["SOL", "XRP", "ADA", "AVAX", "DOGE", "TRX", "DOT", "LINK", "POL", "SHIB",
           "BCH", "ATOM", "IMX", "XLM", "ETC", "APT", "HBAR", "NEAR", "STX", "ARB"],
    2024: ["XRP", "SOL", "DOGE", "ADA", "TRX", "AVAX", "LINK", "SHIB", "XLM", "SUI",
           "HBAR", "DOT", "BCH", "UNI", "PEPE", "NEAR", "APT", "AAVE", "ETC", "POL"],
    2025: ["XRP", "SOL", "TRX", "DOGE", "ADA", "BCH", "LINK", "XLM", "SUI", "AVAX",
           "HBAR", "SHIB", "CRO", "DOT", "UNI", "MNT", "WLFI", "NEAR", "AAVE", "ENA"],
}
BTC_MA = 120


def largecap_universe(P, alts):
    idx = P["close"].index
    elig = pd.DataFrame(False, index=idx, columns=alts)
    for y, syms in YEAR_END_TOP.items():
        rows = idx.year == y + 1
        cols = [s for s in syms if s in alts]
        missing = [s for s in syms if s not in alts]
        if missing:
            print(f"  {y}년 말 목록 중 데이터 없음: {missing}")
        elig.loc[rows, cols] = True
    age = P["close"][alts].notna().cumsum()
    return elig & (age >= bm.MIN_HISTORY)


def main():
    bm.BTC_FILTERS = [BTC_MA]
    bm.ORIGINAL = dict(lookback="7", btc_ma=BTC_MA, sl=0.10, tp=0.10, abs_mom=False)

    raw = pd.read_csv(os.path.join(bm.DATA_DIR, "upbit_daily.csv"), parse_dates=["date"])
    P = bm.build_panels(raw)
    alts = [s for s in P["close"].columns if s != "BTC" and s not in bm.STABLES]
    elig = largecap_universe(P, alts)

    label = f"연말 시총 상위 20 대형 알트 유니버스 (BTC MA{BTC_MA} 고정)"
    grid, curves, sim_dates, bench, split = bm.run_grid(P, elig, bm.TRADE_START, label)

    rows = []
    for k, eq in curves.items():
        r = dict(zip(["lookback", "btc_ma", "sl", "tp", "abs_mom"], k))
        s = pd.Series(eq, index=sim_dates)
        for y, g in s.groupby(s.index.year):
            prev = s[s.index < g.index[0]]
            base = prev.iloc[-1] if len(prev) else g.iloc[0]
            gg = pd.concat([pd.Series([base]), g.reset_index(drop=True)])
            r[f"ret_{y}"] = g.iloc[-1] / base - 1
            r[f"mdd_{y}"] = (gg / gg.cummax() - 1).min()
        rows.append(r)
    yearly = pd.DataFrame(rows)

    grid.to_csv(os.path.join(bm.RES_DIR, "grid_largecap.csv"), index=False, encoding="utf-8-sig")
    yearly.to_csv(os.path.join(bm.RES_DIR, "yearly_largecap.csv"), index=False, encoding="utf-8-sig")
    text = "\n".join([
        "유니버스: 전년도 말 시총 순위 기준 업비트 상장 알트 상위 20 (목록은 backtest_largecap.py 참고)",
        "주의: 목록은 기억 기반 근사치, 상장폐지 코인(LUNA 등)은 데이터에 없음(생존 편향)",
        "원안 행은 '룩백 7일, 손절 10%, 익절 10%'에 BTC MA120 필터를 적용한 것\n",
        bm.summarize(grid, curves, sim_dates, bench, split, label),
    ])
    with open(os.path.join(bm.RES_DIR, "summary_largecap.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
