"""
SPY vs QQQ: 200일선 추세추종을 어느 쪽에 적용할지 비교 (validate_buffer.py 의 함수 재사용)
=====================================================================================

비교 대상 (각 종목에 검증에서 고른 규칙 적용)
  SPY: 매일 판단, 200일선 버퍼 ±4%     QQQ: 주 1회 판단, 버퍼 없음
  참고로 두 종목 모두 단순 보유, 주 1회 버퍼 없음, 매일 ±4% 도 같이 계산
  반반 혼합: SPY ±4% 50% + QQQ 주1회 50%, 매년 첫 거래일에 반반으로 재조정 (재조정 비용 무시)
  반반(둘 다 주1회): SPY, QQQ 모두 주 1회 버퍼 없음 규칙으로 반반 (운용이 가장 단순한 형태)

검증 항목
  1. 같은 기간 성과 (2000, 2001, 2010, 2022년 시작)
  2. 시작 연도별 성과: 어느 해에 시작했느냐에 따라 승자가 바뀌는지
  3. 5년, 10년 롤링 구간 승률
  4. 상장 전 지수 (S&P500 vs 나스닥100, 1987-1999, 배당 미반영)
  5. 두 종목 신호가 얼마나 겹치는지, 수익률 상관
  6. 세후 비교 (해외주식 양도세 22%, 연 250만 원 공제, 원금 1억 원)

공통 가정은 validate_buffer.py 와 같음 (시가 체결, 편도 0.12%, 현금에 미국 단기국채 이자, 샤프는 초과수익 기준)
결과: results/summary_spy_vs_qqq.txt
"""

import os

import numpy as np
import pandas as pd

import backtest_stock_ma as bs
import validate_buffer as vb

RULES = {"보유": None, "주1회 기본": (200, 0, 0, "W"), "매일 ±4%": (200, 0.04, 0.04, "D")}
PICK = {"SPY": "매일 ±4%", "QQQ": "주1회 기본"}
STARTS = ["2000-01-03", "2001-01-02", "2010-01-04", "2022-01-03"]


def series(a, rule, start, end=None):
    """규칙 하나의 자산 곡선 (원금 1, 일별 종가 기준)과 그 기간의 일별 무위험 이자율"""
    i0, i1 = a.span(start, end)
    pos = np.ones(len(a.idx), bool) if RULES[rule] is None else a.pos(*RULES[rule])
    eq, n = vb.run(a, pos, i0, i1)
    return pd.Series(eq, a.idx[i0:i1]), pd.Series(a.rf[i0:i1], a.idx[i0:i1]), n, pos[i0:i1].mean()


def stats(s, rf):
    m = bs.metrics(s.to_numpy(), s.index)
    years = (s.index[-1] - s.index[0]).days / 365.25
    m["cagr"], m["mdd"], m["sharpe"] = vb.fast_metrics(s.to_numpy(), rf.reindex(s.index).fillna(0).to_numpy(), years)
    m["calmar"] = m["cagr"] / abs(m["mdd"]) if m["mdd"] < 0 else np.nan
    r = np.diff(np.concatenate([[1.0], s.to_numpy()])) / np.concatenate([[1.0], s.to_numpy()[:-1]])
    m["vol"] = r.std() * np.sqrt(bs.ANN)
    m["recovery"] = f"{m['recovery_days']}" + ("+" if m["recovery_ongoing"] else "")
    # 최악의 12개월 (월말 기준)
    me = pd.concat([pd.Series([1.0], [s.index[0] - pd.Timedelta(days=1)]), s]).resample("ME").last()
    m["worst_12m"] = (me / me.shift(12) - 1).min()
    return m


def combo(s1, s2, w=0.5):
    """매년 첫 거래일에 w : 1-w 로 재조정한 혼합 곡선"""
    s1, s2 = s1.align(s2, join="inner")
    g1 = s1 / s1.shift(1).fillna(1.0)
    g2 = s2 / s2.shift(1).fillna(1.0)
    out, v = [], 1.0
    for _, idx in s1.groupby(s1.index.year).groups.items():
        seg = v * w * g1[idx].cumprod() + v * (1 - w) * g2[idx].cumprod()
        out.append(seg)
        v = seg.iloc[-1]
    return pd.concat(out)


def rolling_win(sa, sb, rf, years):
    """월초 시작 years년 구간에서 a가 b를 이긴 비율"""
    sa, sb = sa.align(sb, join="inner")
    dates = sa.index
    starts = pd.Series(np.arange(len(dates)), index=dates).groupby(dates.to_period("M")).first().to_numpy()
    rows = []
    for s in starts:
        e = dates.searchsorted(dates[s] + pd.DateOffset(years=years))
        if e >= len(dates):
            break
        yrs = (dates[e - 1] - dates[s]).days / 365.25
        r = rf.reindex(dates[s:e]).fillna(0).to_numpy()
        res = []
        for x in (sa, sb):
            base = x.iloc[s - 1] if s > 0 else 1.0
            res.append(vb.fast_metrics(x.iloc[s:e].to_numpy() / base, r, yrs))
        rows.append(dict(start=dates[s], a_cagr=res[0][0], b_cagr=res[1][0], a_mdd=res[0][1], b_mdd=res[1][1],
                         a_sh=res[0][2], b_sh=res[1][2]))
    d = pd.DataFrame(rows)
    return dict(n=len(d), cagr_win=(d.a_cagr > d.b_cagr).mean(), cagr_diff=(d.a_cagr - d.b_cagr).median(),
                sh_win=(d.a_sh > d.b_sh).mean(), mdd_win=(d.a_mdd > d.b_mdd).mean(),
                a_worst=d.a_cagr.min(), b_worst=d.b_cagr.min())


COLS = [("name", "전략", None), ("cagr", "CAGR", vb.pct), ("vol", "변동성", vb.pct), ("mdd", "MDD", vb.pct),
        ("sharpe", "샤프", vb.num), ("calmar", "칼마", vb.num), ("worst_month", "최악월", vb.pct),
        ("worst_12m", "최악 12개월", vb.pct), ("loss_month_ratio", "손실월", vb.pct), ("recovery", "회복일", None),
        ("trades", "매매", None)]


def main():
    rf_all = (vb.load_long("^IRX")["close"] / 100 / bs.ANN).clip(lower=0)
    A = {"SPY": vb.Asset("SPY", rf_all), "QQQ": vb.Asset("QQQ", rf_all),
         "S&P500지수": vb.Asset("^GSPC", rf_all), "나스닥100지수": vb.Asset("^NDX", rf_all)}
    L = ["SPY vs QQQ: 200일선 추세추종 적용 대상 비교",
         f"선택 규칙: SPY {PICK['SPY']}(200일선, 매일 판단), QQQ {PICK['QQQ']}(200일선 주 1회, 버퍼 없음)",
         "혼합: 두 선택 규칙을 반반, 매년 첫 거래일 재조정. 공통: 시가 체결, 편도 0.12%, 현금에 미국 단기국채 이자, 샤프는 초과수익 기준",
         "회복일: 전고점 회복까지 걸린 최장 달력 일수 (+는 아직 회복 중)", ""]

    # 1. 같은 기간 성과
    L.append("=" * 110 + "\n1. 같은 기간 성과\n" + "=" * 110)
    for start in STARTS:
        rows, picked, weekly = [], {}, {}
        for name in ["SPY", "QQQ"]:
            for rule in RULES:
                s, rf, n, _ = series(A[name], rule, start)
                rows.append(dict(name=f"{name} {rule}", trades=n, **stats(s, rf)))
                if rule == PICK[name]:
                    picked[name] = (s, rf)
                if rule == "주1회 기본":
                    weekly[name] = s
        mix = combo(picked["SPY"][0], picked["QQQ"][0])
        rows.append(dict(name="반반 혼합", trades="", **stats(mix, picked["SPY"][1])))
        rows.append(dict(name="반반(둘 다 주1회)", trades="", **stats(combo(weekly["SPY"], weekly["QQQ"]), picked["SPY"][1])))
        L.append(f"\n[{start[:4]}년 시작, {picked['SPY'][0].index[0].date()}부터 {picked['SPY'][0].index[-1].date()}까지]")
        L.append(vb.table(rows, COLS))

    # 2. 시작 연도별
    L.append("\n" + "=" * 110 + "\n2. 시작 연도별 CAGR / MDD (해당 연도 첫 거래일부터 2026-10까지)\n" + "=" * 110)
    full = {}
    for name in ["SPY", "QQQ"]:
        for rule in RULES:
            full[(name, rule)] = series(A[name], rule, "2000-01-03")[:2]
    mix_full = combo(full[("SPY", PICK["SPY"])][0], full[("QQQ", PICK["QQQ"])][0])
    mix_weekly = combo(full[("SPY", "주1회 기본")][0], full[("QQQ", "주1회 기본")][0])
    rf = full[("SPY", "보유")][1]
    keys = [("SPY", "보유"), ("QQQ", "보유"), ("SPY", PICK["SPY"]), ("QQQ", PICK["QQQ"])]
    rows = []
    for y in range(2000, 2023):
        r = dict(year=y)
        for k in keys + ["혼합"]:
            s = mix_full if k == "혼합" else full[k][0]
            i = s.index.searchsorted(pd.Timestamp(f"{y}-01-01"))
            sub = s.iloc[i:] / (s.iloc[i - 1] if i > 0 else 1.0)
            c, m, _ = vb.fast_metrics(sub.to_numpy(), rf.reindex(sub.index).fillna(0).to_numpy(),
                                      (sub.index[-1] - sub.index[0]).days / 365.25)
            lab = "혼합" if k == "혼합" else f"{k[0]} {k[1]}"
            r[lab] = f"{vb.pct(c)} / {vb.pct(m, 0)}"
            r[lab + "_c"] = c
        r["전략 승자"] = "QQQ" if r[f"QQQ {PICK['QQQ']}_c"] > r[f"SPY {PICK['SPY']}_c"] else "SPY"
        rows.append(r)
    d = pd.DataFrame(rows)
    L.append(d[[c for c in d.columns if not c.endswith("_c")]].to_string(index=False))
    L.append(f"\n  전략 기준 QQQ가 이긴 시작 연도: {(d['전략 승자'] == 'QQQ').sum()} / {len(d)}")

    # 3. 롤링
    L.append("\n" + "=" * 110 + "\n3. 롤링 구간 승률 (월초 시작, 2000-01부터 데이터). A가 B를 이긴 비율\n" + "=" * 110)
    pairs = [("QQQ 주1회 기본", "SPY 매일 ±4%", full[("QQQ", "주1회 기본")][0], full[("SPY", "매일 ±4%")][0]),
             ("QQQ 주1회 기본", "SPY 주1회 기본", full[("QQQ", "주1회 기본")][0], full[("SPY", "주1회 기본")][0]),
             ("QQQ 보유", "SPY 보유", full[("QQQ", "보유")][0], full[("SPY", "보유")][0]),
             ("반반 혼합", "SPY 매일 ±4%", mix_full, full[("SPY", "매일 ±4%")][0]),
             ("반반 혼합", "QQQ 주1회 기본", mix_full, full[("QQQ", "주1회 기본")][0]),
             ("반반(둘 다 주1회)", "QQQ 주1회 기본", mix_weekly, full[("QQQ", "주1회 기본")][0])]
    rows = []
    for yrs in (5, 10):
        for an, bn, sa, sb in pairs:
            r = rolling_win(sa, sb, rf, yrs)
            rows.append(dict(win=f"{yrs}년", a=an, b=bn, **r))
    L.append(vb.table(rows, [("win", "구간", None), ("a", "A", None), ("b", "B", None), ("n", "구간 수", None),
                             ("cagr_win", "CAGR 승률", vb.pct), ("cagr_diff", "CAGR차(중앙)", lambda x: vb.pct(x, 2)),
                             ("sh_win", "샤프 승률", vb.pct), ("mdd_win", "MDD 승률", vb.pct),
                             ("a_worst", "A 최악 CAGR", vb.pct), ("b_worst", "B 최악 CAGR", vb.pct)]))

    # 4. 상장 전 지수
    L.append("\n" + "=" * 110 + "\n4. 상장 전 지수 비교 (가격 지수, 배당 미반영: S&P500이 연 2%p 안팎 불리)\n" + "=" * 110)
    for start, end in [("1987-01-02", "1999-12-31"), ("1987-01-02", None)]:
        rows = []
        for name, pick in [("S&P500지수", "매일 ±4%"), ("나스닥100지수", "주1회 기본")]:
            for rule in ["보유", pick]:
                s, rf_i, n, _ = series(A[name], rule, start, end)
                rows.append(dict(name=f"{name} {rule}", trades=n, **stats(s, rf_i)))
        L.append(f"\n[{start[:4]}년부터 {(end or '2026')[:4]}년까지]")
        L.append(vb.table(rows, COLS))

    # 5. 신호 겹침과 상관
    L.append("\n" + "=" * 110 + "\n5. 신호 겹침과 상관 (2000년부터)\n" + "=" * 110)
    i0s, _ = A["SPY"].span("2000-01-03", None)
    i0q, _ = A["QQQ"].span("2000-01-03", None)
    ps = pd.Series(A["SPY"].pos(*RULES[PICK["SPY"]])[i0s:], A["SPY"].idx[i0s:])
    pq = pd.Series(A["QQQ"].pos(*RULES[PICK["QQQ"]])[i0q:], A["QQQ"].idx[i0q:])
    ps, pq = ps.align(pq, join="inner")
    L.append(f"  둘 다 보유 {(ps & pq).mean():.1%}, SPY만 {(ps & ~pq).mean():.1%}, QQQ만 {(~ps & pq).mean():.1%}, "
             f"둘 다 현금 {(~ps & ~pq).mean():.1%}")
    hs, hq = full[("SPY", "보유")][0], full[("QQQ", "보유")][0]
    ss, sq = full[("SPY", PICK["SPY"])][0], full[("QQQ", PICK["QQQ"])][0]
    L.append(f"  일간 수익률 상관: 보유끼리 {hs.pct_change().corr(hq.pct_change()):.2f}, "
             f"전략끼리 {ss.pct_change().corr(sq.pct_change()):.2f}")
    me = lambda s: s.resample("ME").last().pct_change()
    L.append(f"  월간 수익률 상관: 보유끼리 {me(hs).corr(me(hq)):.2f}, 전략끼리 {me(ss).corr(me(sq)):.2f}")

    # 6. 세후
    L.append("\n" + "=" * 110 + "\n6. 세후 비교 (해외주식 직접 투자, 양도세 22%, 연 250만 원 공제, 원금 1억 원, 마지막 날 전량 매도)\n" + "=" * 110)
    for start in STARTS:
        rows = []
        for name in ["SPY", "QQQ"]:
            a = A[name]
            i0, i1 = a.span(start, None)
            for rule in ["보유", PICK[name]]:
                pos = np.ones(len(a.idx), bool) if RULES[rule] is None else a.pos(*RULES[rule])
                eq, _ = vb.run(a, pos, i0, i1)
                t = vb.after_tax(a, pos, i0, i1)
                rows.append(dict(name=f"{name} {rule}", pre=vb.metrics(a, eq, i0, i1)["cagr"],
                                 post=t["after_tax_cagr"], final=t["after_tax_final"] * 1e4))
        L.append(f"\n[{start[:4]}년 시작]")
        L.append(vb.table(rows, [("name", "전략", None), ("pre", "세전 CAGR", vb.pct), ("post", "세후 CAGR", vb.pct),
                                 ("final", "세후 최종(만원)", lambda x: f"{x:,.0f}")]))

    text = "\n".join(L)
    with open(os.path.join(bs.RES_DIR, "summary_spy_vs_qqq.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
