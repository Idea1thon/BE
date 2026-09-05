"""FC 피처 설명력 분석 — 단변량 상관 · 중복도 · 7차원 분리도 · 증분 신호 · 재현성.

입력: output/feature_validation/panel_trdar_industry.csv, panel_trdar.csv
산출: output/feature_validation/*.csv

방법: Spearman(pandas 네이티브), 편상관 = 통제변수 rank-잔차 간 Spearman (numpy).
     적합 모델(로지스틱 등) 만들지 않음 — 스코어링 엔진 오인 방지.
한계: outcome 은 인허가 폐업/개업·생존 프록시. 매출·손익 없음 → "좋은 입지=성공" 검증 불가.

실행: .venv/bin/python3 scripts/analyze_feature_evidential_value.py
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output", "feature_validation")

# 방향: sign_hint = +1 이면 "값↑ → churn_ratio↑ (나쁨)" 이 자연스러운 가설
FC_META = {
    "FC01_flow_density":       ("현재 수요|수요 구성", "유동밀도"),
    "FC02_flow_trend":         ("현재 수요|미래 신호", "유동 4Q 기울기"),
    "FC03_resident":           ("수요 구성", "상주인구 수준"),
    "FC04_worker":             ("수요 구성", "직장인구 수준"),
    "FC05_activity_spread":    ("수요 구성", "활동유형 편차"),
    "FC06a_foreign_resident":  ("수요 구성", "외국인 거주 근사비율"),
    "FC06b_foreign_visit":     ("수요 구성", "외국인 방문 근사비율"),
    "FC07_station_n":          ("현재 수요", "상권내 역 수"),
    "FC07_station_dist":       ("현재 수요", "최근접 역 거리"),
    "FC07_station_250m":       ("현재 수요", "역세권 250m"),
    "FC07_bus_n":              ("현재 수요", "250m 버스정류소 수"),
    "FC07_bus_trunk_n":        ("현재 수요", "간선 정류소 수"),
    "FC08_apt_hh_250m":        ("수요 구성", "250m 아파트 세대수"),
    "FC08_apt_hh_in":          ("수요 구성", "상권내 아파트 세대수"),
    "FC10_entry_health_risk":  ("진입 건전성", "entry_health risk"),
    "FC11_label_risk":         ("진입 건전성", "상권변화 라벨 리스크"),
    "FC12_close_trend":        ("진입 건전성|미래 신호", "폐업률 4Q 기울기"),
    "FC20_rent_index":         ("비용 부담", "R-ONE 임대가격지수"),
    "FC30_store_density":      ("경쟁·시장수용", "동종 점포밀도"),
    "FC31_rev_per_store":      ("현재 수요|경쟁·시장수용", "점포당 매출"),
    "FC32_franchise_ratio":    ("경쟁·시장수용", "프랜차이즈 비율"),
    "FC40_flow_surge":         ("현재 수요|미래 신호", "유동 최근 급증률"),
    "FC41_gu_search":          ("현재 수요|미래 신호", "자치구 검색 관심도"),
    "FC42_industry_search":    ("미래 신호", "업종 검색 관심도"),
    "FC51_redev_overlap":      ("미래 신호", "정비/개발사업 겹침"),
    "FC52_plan_rail_n":        ("미래 신호", "자치구 계획철도 수"),
    "FC53_employ_rate":        ("미래 신호", "자치구 고용률"),
}
# 두 야드스틱: 인허가(행정) universe vs 점포분석(상권분석서비스) universe. 둘은 r≈0.11로 별개.
OUTCOMES = ["churn_ratio", "net_growth_pct", "surv_1y", "close_r_4q", "net_open_4q", "delta_store"]
PRIMARY = "churn_ratio"
CONTROLS = ["FC30_store_density_pctl", "rev_pp_pctl"]   # 편상관 통제 (경쟁·매출)
IND_NM = {"CS100001": "한식", "CS100002": "중식", "CS100003": "일식", "CS100004": "양식",
          "CS100005": "제과점", "CS100006": "패스트푸드", "CS100007": "치킨", "CS100008": "분식",
          "CS100009": "호프-간이주점", "CS100010": "커피-음료"}


def spearman(a: pd.Series, b: pd.Series):
    """Spearman = rank 후 Pearson (scipy 불필요)."""
    m = a.notna() & b.notna()
    if m.sum() < 30:
        return np.nan, int(m.sum())
    r = a[m].rank().corr(b[m].rank())   # pearson on ranks
    return (round(r, 3) if pd.notna(r) else np.nan), int(m.sum())


def spearman_matrix(df: pd.DataFrame) -> pd.DataFrame:
    d = df.loc[:, df.nunique() > 1]     # 상수 열 제외 (자치구/업종 상수 FC)
    return d.rank().corr()


def quartile_spread(fc: pd.Series, y: pd.Series):
    """FC 4분위별 outcome 평균. (Q4평균 − Q1평균) / outcome std. 비단조·임계 효과 포착."""
    m = fc.notna() & y.notna()
    if m.sum() < 80:
        return np.nan, np.nan
    q = pd.qcut(fc[m].rank(method="first"), 4, labels=[1, 2, 3, 4])
    g = y[m].groupby(q, observed=True).mean()
    if len(g) < 4 or y[m].std() == 0:
        return np.nan, np.nan
    return round((g[4] - g[1]) / y[m].std(), 3), round(float(g.max() - g.min()) / y[m].std(), 3)


def partial_spearman(df, y, x, controls):
    """통제변수 rank-잔차 간 Spearman."""
    cols = [y, x] + controls
    d = df[cols].dropna()
    if len(d) < 40:
        return np.nan, len(d)
    R = d.rank()
    C = np.column_stack([np.ones(len(d))] + [R[c].values for c in controls])
    def resid(v):
        beta, *_ = np.linalg.lstsq(C, v, rcond=None)
        return v - C @ beta
    ry, rx = resid(R[y].values), resid(R[x].values)
    r = np.corrcoef(ry, rx)[0, 1]
    return round(float(r), 3), len(d)


def main():
    ti = pd.read_csv(os.path.join(OUT, "panel_trdar_industry.csv"), encoding="utf-8-sig",
                     dtype={"상권_코드": str})
    ti["업종명"] = ti["업종코드"].map(IND_NM)
    # outcome 을 업종 내 분위로 (교차업종 비교용)
    for oc in OUTCOMES:
        ti[oc + "_pctl"] = ti.groupby("업종코드")[oc].rank(pct=True) * 100

    fcs = [c for c in FC_META if c in ti.columns]
    ch = ti[ti["sample_churn_ok"] == 1].copy()          # 인허가 churn/net_growth
    sv = ti[ti["sample_surv_ok"] == 1].copy()           # 코호트 생존율
    st = ti[ti["전체_점포_수"].fillna(0) >= 5].copy()    # 점포분석 universe (폐업률·순개업·증감)
    OC_SAMPLE = {"churn_ratio": ch, "net_growth_pct": ch, "surv_1y": sv, "surv_1y_buf5": sv,
                 "close_r_4q": st, "net_open_4q": st, "delta_store": st}

    # 상권 단위(전 외식 pooled) — 자치구 상수 FC·저노이즈 확인용
    tf = pd.read_csv(os.path.join(OUT, "panel_trdar.csv"), encoding="utf-8-sig", dtype={"상권_코드": str})
    tf_ok = tf[tf["opens_all"].fillna(0) >= 20]

    # ── 1. 단변량 설명력 ──
    # pooled 는 업종 내 분위(_pctl)끼리 비교 — 업종 기저율(치킨·커피 프랜차이즈 등) 제거,
    # "같은 업종 안에서 FC 높은 입지가 결과가 나은가" 만 남긴다.
    rows = []
    for fc in fcs:
        fp = fc + "_pctl"
        rec = {"fc": fc, "dimension": FC_META[fc][0], "항목": FC_META[fc][1],
               "coverage_pct": round(100 * ch[fc].notna().mean(), 1)}
        for oc in OUTCOMES:
            samp = OC_SAMPLE[oc]
            r, n = spearman(samp[fp], samp[oc + "_pctl"])
            rec[f"rho_{oc}"] = r
            rec[f"n_{oc}"] = n
        rec["q4_minus_q1_std"], rec["quartile_range_std"] = quartile_spread(ch[fp], ch[PRIMARY])
        if fc in tf.columns:                    # 상권 단위 (전 외식 pooled — 업종 무관)
            r_t, n_t = spearman(tf_ok[fc], tf_ok["churn_ratio_all"])
            rec["rho_churn_trdar"], rec["n_trdar"] = r_t, n_t
        rows.append(rec)
    corr = pd.DataFrame(rows)
    corr.to_csv(os.path.join(OUT, "fc_outcome_correlation.csv"), index=False, encoding="utf-8-sig")

    # 두 판정: "입지 품질"(폐업 낮음·생존) vs "시장 모멘텀"(점포 성장).
    # 하네스 목표는 '좋은 입지=실패 낮음'이므로 품질 신호가 본질. 모멘텀은 신규 진입엔 양날.
    QUAL = ["rho_churn_ratio", "rho_churn_trdar", "rho_close_r_4q", "rho_surv_1y"]
    MOM = ["rho_delta_store", "rho_net_growth_pct"]

    def _best(r, keys):
        return round(max(abs(r.get(k) or 0) for k in keys), 3)

    def grade(v):
        return "신호" if v >= 0.25 else "약함" if v >= 0.15 else "없음"

    corr["quality_abs_rho"] = corr.apply(lambda r: _best(r, QUAL), axis=1)
    corr["momentum_abs_rho"] = corr.apply(lambda r: _best(r, MOM), axis=1)
    corr["quartile_abs"] = corr["quartile_range_std"].abs().fillna(0)
    corr["verdict_quality"] = corr.apply(
        lambda r: "결측많음" if r["coverage_pct"] < 70
        else "신호(비단조)" if (grade(r["quality_abs_rho"]) == "없음" and r["quartile_abs"] >= 0.45)
        else grade(r["quality_abs_rho"]), axis=1)
    corr["verdict_momentum"] = corr.apply(
        lambda r: "결측많음" if r["coverage_pct"] < 70 else grade(r["momentum_abs_rho"]), axis=1)

    corr[["fc", "항목", "dimension", "coverage_pct",
          "quality_abs_rho", "verdict_quality", "momentum_abs_rho", "verdict_momentum",
          "rho_churn_ratio", "rho_churn_trdar", "rho_close_r_4q", "rho_surv_1y",
          "rho_delta_store", "rho_net_growth_pct", "quartile_range_std"]].to_csv(
        os.path.join(OUT, "fc_verdict.csv"), index=False, encoding="utf-8-sig")

    # ── 2. 중복도 ──
    pctl_cols = [fc + "_pctl" for fc in fcs]
    M = spearman_matrix(ch[pctl_cols]).round(3)
    M.index = [c[:-5] for c in M.index]
    M.columns = [c[:-5] for c in M.columns]
    M.to_csv(os.path.join(OUT, "fc_redundancy_matrix.csv"), encoding="utf-8-sig")
    pairs = []
    for i, a in enumerate(M.index):
        for b in M.index[i + 1:]:
            if abs(M.loc[a, b]) >= 0.7:
                pairs.append({"fc_a": a, "fc_b": b, "rho": M.loc[a, b]})
    pd.DataFrame(pairs).to_csv(os.path.join(OUT, "fc_redundant_pairs.csv"),
                               index=False, encoding="utf-8-sig")

    # ── 3. 7차원 분리도 ──
    dim2fc = {}
    for fc in fcs:
        for dim in FC_META[fc][0].split("|"):
            dim2fc.setdefault(dim, []).append(fc)
    Mabs = M.abs()
    dsep = []
    for dim, dfcs in dim2fc.items():
        if len(dfcs) < 2:
            continue
        within = [Mabs.loc[a, b] for i, a in enumerate(dfcs) for b in dfcs[i + 1:]]
        others = [f for f in fcs if f not in dfcs]
        between = [Mabs.loc[a, b] for a in dfcs for b in others]
        dsep.append({"dimension": dim, "n_fc": len(dfcs),
                     "within_mean_abs_rho": round(np.mean(within), 3),
                     "between_mean_abs_rho": round(np.mean(between), 3),
                     "ratio_within_over_between": round(np.mean(within) / max(np.mean(between), 1e-6), 2),
                     "separable": "예" if np.mean(within) > np.mean(between) * 1.3 else "약함"})
    pd.DataFrame(dsep).to_csv(os.path.join(OUT, "dimension_separability.csv"),
                              index=False, encoding="utf-8-sig")

    # ── 4. 증분 신호 (편상관, 통제=경쟁+매출) ──
    inc = []
    for fc in fcs:
        if fc + "_pctl" in CONTROLS:
            continue
        raw_r, _ = spearman(ch[fc + "_pctl"], ch[PRIMARY + "_pctl"])
        par_r, n = partial_spearman(ch, PRIMARY + "_pctl", fc + "_pctl", CONTROLS)
        inc.append({"fc": fc, "항목": FC_META[fc][1],
                    "rho_raw": raw_r, "rho_partial": par_r,
                    "attenuation": round((raw_r or 0) - (par_r or 0), 3), "n": n})
    incdf = pd.DataFrame(inc).sort_values("rho_partial", key=lambda s: s.abs(), ascending=False)
    incdf.to_csv(os.path.join(OUT, "incremental_partial_corr.csv"), index=False, encoding="utf-8-sig")
    # 기저 vs 전체 설명력 (rank R², 서술용)
    base_r2, full_r2 = _rank_r2(ch, PRIMARY + "_pctl", CONTROLS,
                                [f + "_pctl" for f in fcs if f + "_pctl" not in CONTROLS])

    # ── 4b. 알려진-정답 앵커: 변화지표 라벨(범주) → outcome (change_indicator_store_correlation 재현) ──
    lab = st.dropna(subset=["chg_label"]).groupby("chg_label").agg(
        n=("close_r_4q", "size"),
        close_r_4q_mean=("close_r_4q", "mean"),
        net_open_4q_mean=("net_open_4q", "mean")).reset_index()
    labc = ch.dropna(subset=["chg_label"]).groupby("chg_label")["churn_ratio"].agg(
        churn_ratio_mean="mean", churn_ratio_median="median", n="size").reset_index()
    lab = lab.merge(labc, on="chg_label", how="outer").round(3)
    lab.to_csv(os.path.join(OUT, "label_category_check.csv"), index=False, encoding="utf-8-sig")

    # ── 5. 재현성 (업종별) ──
    stab = []
    for fc in fcs:
        rec = {"fc": fc, "항목": FC_META[fc][1], "rho_pooled": corr.loc[corr.fc == fc, "rho_churn_ratio"].iloc[0]}
        signs = []
        for ind, nm in IND_NM.items():
            sub = ch[ch["업종코드"] == ind]
            r, n = spearman(sub[fc], sub[PRIMARY])
            rec[nm] = r
            if pd.notna(r) and abs(r) >= 0.1:
                signs.append(np.sign(r))
        rec["sign_flips"] = int(len(set(signs)) > 1)
        rec["n_industry_signal"] = len(signs)
        stab.append(rec)
    pd.DataFrame(stab).to_csv(os.path.join(OUT, "by_industry_stability.csv"),
                              index=False, encoding="utf-8-sig")

    def _rec(df, vcol):
        return df.loc[df[vcol].str.startswith("신호"), ["fc", "항목", "quality_abs_rho", "momentum_abs_rho"]] \
            .to_dict("records")

    summary = {
        "primary_outcome": PRIMARY,
        "outcomes": {"quality": QUAL, "momentum": MOM},
        "n_cells": {"churn_sample": int(ch.shape[0]), "surv_sample": int(sv.shape[0]),
                    "store_sample": int(st.shape[0]), "trdar_pooled": int(tf_ok.shape[0])},
        "verdict_quality_counts": corr["verdict_quality"].value_counts().to_dict(),
        "verdict_momentum_counts": corr["verdict_momentum"].value_counts().to_dict(),
        "quality_signal_fcs": _rec(corr, "verdict_quality"),
        "momentum_signal_fcs": _rec(corr, "verdict_momentum"),
        "redundant_pairs": pairs,
        "dimension_separability": dsep,
        "incremental_rank_r2": {"baseline_controls_only": round(base_r2, 3),
                                "controls_plus_all_fc": round(full_r2, 3),
                                "gain": round(full_r2 - base_r2, 3)},
        "sign_flip_fcs": [s["fc"] for s in stab if s["sign_flips"]],
        "label_category_check": lab.to_dict("records"),
        "caveats": [
            "야드스틱 2개: 인허가(행정)·점포분석(상권분석서비스) — 둘은 r≈0.11로 별개 universe",
            "매출·손익 outcome 없음 → '좋은 입지=성공' 검증 불가. 폐업/생존·성장 연관까지만",
            "surv_1y median~90% 포화 → 보조",
            "pooled 상관은 업종 내 분위(_pctl)끼리 — 업종 기저율(치킨·커피 프랜차이즈 등) 제거",
            "FC-41·42·52·53 자치구/업종 상수 → within 분위 제한적, pooled(trdar)·모멘텀 위주 해석",
            "편상관은 통제 후 잔차 Spearman (적합 모델 아님)",
        ],
    }
    json.dump(summary, open(os.path.join(OUT, "analysis_summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    print(f"품질 판정: {summary['verdict_quality_counts']}")
    print(f"  품질 신호 FC: {[(r['fc'], r['quality_abs_rho']) for r in summary['quality_signal_fcs']]}")
    print(f"모멘텀 판정: {summary['verdict_momentum_counts']}")
    print(f"  모멘텀 신호 FC: {[(r['fc'], r['momentum_abs_rho']) for r in summary['momentum_signal_fcs']]}")
    print(f"중복 쌍(|ρ|≥0.7): {[(p['fc_a'], p['fc_b'], p['rho']) for p in pairs]}")
    print(f"증분 rank R²(churn): 기저 {base_r2:.3f} → 전체 {full_r2:.3f} (+{full_r2-base_r2:.3f})")
    print(f"부호 뒤집힘 FC: {summary['sign_flip_fcs']}")
    print("변화지표 라벨(범주) 앵커 — churn_ratio 평균:")
    for r in lab.to_dict("records"):
        print(f"  {r['chg_label']}: churn {r.get('churn_ratio_mean')}  close_r_4q {r.get('close_r_4q_mean')}")
    print("차원 분리도:")
    for d in dsep:
        print(f"  {d['dimension']:14} within {d['within_mean_abs_rho']} vs between {d['between_mean_abs_rho']} → {d['separable']}")


def _rank_r2(df, y, controls, extra):
    d = df[[y] + controls + extra].dropna()
    if len(d) < 50:
        return 0.0, 0.0
    R = d.rank()
    yv = R[y].values
    yv = yv - yv.mean()

    def r2(cols):
        X = np.column_stack([np.ones(len(d))] + [R[c].values for c in cols])
        beta, *_ = np.linalg.lstsq(X, R[y].values, rcond=None)
        pred = X @ beta
        ss_res = ((R[y].values - pred) ** 2).sum()
        ss_tot = (yv ** 2).sum()
        return 1 - ss_res / ss_tot
    return r2(controls), r2(controls + extra)


if __name__ == "__main__":
    main()
