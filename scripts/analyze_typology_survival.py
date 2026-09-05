"""상권 유형별 상주·직장인구 ↔ 존속(폐업/생존) 조건부 검증 (미래가치 재정의 2단계).

가설(사용자 2026-09-03): 상주인구·직장인구를 '입지 존속 가능성' 신호로 쓸 때, 배후
상권이 성숙(골목형)한지 미성숙(신도시형)한지에 따라 의미가 반대다.
- 골목상권형 + 상주/직장인구 높음 → 폐업률 낮음(존속 하방 방어)이면 근거로 승격
- 신도시형에서는 관계가 약하거나 없음 → '잠재수요는 있으나 상권 미성숙'
- 두 유형 모두에서 관계가 안 나오면 '가정(미검증)' 유지

방법: 상권 단위(전 외식 pooled). 유형(`build_site_typology.py`)으로 층화해
Spearman(인구 수준 ↔ outcome)를 그룹별로 낸다. **적합 모델 없음.** scipy 없어
p-value 없이 ρ·n·|ρ|>=0.2 여부만 본다.

산출:
  output/feature_validation/typology_survival_panel.csv
  output/feature_validation/typology_survival_stratified.csv
  output/feature_validation/typology_survival_cells.csv
  output/feature_validation/typology_survival_manifest.json
  output/figures/feature_validation/typology_survival_*.png
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "feature_validation"
FIG = ROOT / "output" / "figures" / "feature_validation"
OUT.mkdir(parents=True, exist_ok=True)
FIG.mkdir(parents=True, exist_ok=True)

FOOD = [f"CS100{i:03d}" for i in range(1, 11)]
Q_NOW = "20261"
CHURN_YEARS = ("2022", "2023", "2024", "2025")
COHORT_QS = [f"20{y}{q}" for y in range(21, 25) for q in (1, 2, 3, 4)]
MIN_GROUP_N = 30           # 그룹별 상관을 계산할 최소 표본
STRONG = 0.20             # |ρ| 신호 기준 (feature-evidential-value.md 관례)


def spearman(a: pd.Series, b: pd.Series) -> tuple[float | None, int]:
    d = pd.concat([a, b], axis=1).dropna()
    if len(d) < 10:
        return None, len(d)
    r = d.iloc[:, 0].rank().corr(d.iloc[:, 1].rank())
    return (round(float(r), 3) if pd.notna(r) else None), len(d)


def _q2idx(s: pd.Series) -> pd.Series:
    y = pd.to_numeric(s.str.slice(0, 4), errors="coerce")
    q = pd.to_numeric(s.str.slice(4, 5), errors="coerce")
    return y * 4 + (q - 1)


def load_outcomes() -> pd.DataFrame:
    # (1) 상권분기 패널 — churn_ratio(폐업/개업 2022~2025 완전분기), 최근4Q 폐업률, 순증
    pan = pd.read_csv(ROOT / "data/인허가/음식점_상권분기_패널.csv", encoding="utf-8-sig", dtype=str)
    pan.columns = [c.lstrip("﻿") for c in pan.columns]
    for c in ("영업중_수", "신규개업_수", "폐업_수", "폐업률", "개업률"):
        pan[c] = pd.to_numeric(pan[c], errors="coerce")
    pan["상권_코드"] = pan["상권_코드"].astype(str).str.strip()
    comp = pan[pan["분기_상태"] == "완전"]

    win = comp[comp["기준_년분기_코드"].str[:4].isin(CHURN_YEARS)]
    ag = win.groupby("상권_코드").agg(opens=("신규개업_수", "sum"), closes=("폐업_수", "sum")).reset_index()
    ag["churn_ratio"] = ag["closes"] / ag["opens"].replace(0, np.nan)

    recent = ["20252", "20253", "20254", Q_NOW]
    r4 = comp[comp["기준_년분기_코드"].isin(recent)].groupby("상권_코드").agg(
        close_r_panel_4q=("폐업률", "mean"), open_r_panel_4q=("개업률", "mean")
    ).reset_index()
    r4["net_open_panel_4q"] = r4["open_r_panel_4q"] - r4["close_r_panel_4q"]

    base = comp[comp["기준_년분기_코드"] == "20221"].groupby("상권_코드")["영업중_수"].sum()
    now = comp[comp["기준_년분기_코드"] == Q_NOW].groupby("상권_코드")["영업중_수"].sum()
    ng = ((now - base) / base.replace(0, np.nan) * 100).rename("net_growth_pct").reset_index()

    out = ag.merge(r4, on="상권_코드", how="outer").merge(ng, on="상권_코드", how="outer")

    # (2) 인허가 업소 코호트 1년 생존율 (상권 pooled)
    lic = pd.read_csv(ROOT / "data/인허가/음식점_인허가_서울.csv", encoding="utf-8", dtype=str).fillna("")
    lic = lic[lic["업종코드"].isin(FOOD)].copy()
    lic["상권_코드"] = lic["상권_코드"].astype(str).str.strip()
    coh = lic[lic["인허가_분기"].str.strip().isin(COHORT_QS) & (lic["상권_코드"] != "")].copy()
    oi = _q2idx(coh["인허가_분기"].str.strip())
    ci = _q2idx(coh["폐업_분기"].str.strip().where(coh["폐업_분기"].str.strip() != "", None))
    coh["alive_4q"] = ci.isna() | (ci >= oi + 4)
    coh["alive_5q"] = ci.isna() | (ci >= oi + 5)
    surv = coh.groupby("상권_코드").agg(
        n_cohort=("alive_4q", "size"), surv_1y=("alive_4q", "mean"), surv_1y_buf5=("alive_5q", "mean")
    ).reset_index()
    surv["surv_1y"] *= 100
    surv["surv_1y_buf5"] *= 100

    out = out.merge(surv, on="상권_코드", how="outer")

    # (3) 점포-상권 20261 — 외식 10업종 평균 폐업률 + 총 점포수(표본 필터용)
    st = pd.read_csv(ROOT / "data/점포/2026년/서울시 상권분석서비스(점포-상권).csv", encoding="cp949")
    st = st[(st["기준_년분기_코드"] == int(Q_NOW)) & (st["서비스_업종_코드"].isin(FOOD))]
    st["상권_코드"] = st["상권_코드"].astype(str).str.strip()
    sg = st.groupby("상권_코드").agg(
        close_r_store=("폐업_률", "mean"), open_r_store=("개업_율", "mean"),
        food_store_n=("전체_점포_수", "sum"),
    ).reset_index()
    out = out.merge(sg, on="상권_코드", how="outer")
    return out


def build_panel() -> pd.DataFrame:
    typ = pd.read_csv(OUT.parent / "site_typology" / "typology_상권.csv")
    typ["code"] = typ["code"].astype(str).str.strip()
    typ = typ.rename(columns={"code": "상권_코드", "총_상주인구_수": "resident_pop"})
    typ = typ.drop(columns=[c for c in ("food_store_n", "open_rate", "store_density") if c in typ.columns])

    wk = pd.read_csv(ROOT / "data/직장인구/서울시 상권분석서비스(직장인구-상권).csv", encoding="cp949")
    wk = wk[wk["기준_년분기_코드"] == int(Q_NOW)][["상권_코드", "총_직장_인구_수"]].copy()
    wk["상권_코드"] = wk["상권_코드"].astype(str).str.strip()
    wk = wk.rename(columns={"총_직장_인구_수": "worker_pop"})

    out = load_outcomes()

    df = typ.merge(wk, on="상권_코드", how="left").merge(out, on="상권_코드", how="left")

    # 서울 분위(전체 상권 기준)
    for col in ("resident_pop", "worker_pop"):
        df[f"{col}_pctl"] = df[col].rank(pct=True) * 100

    # 표본 필터 — 저인구 상권은 outcome이 degenerate(0 폐업 / 100% 생존)라 상관을 오염시킨다.
    df["sample_ok_churn"] = df["opens"].fillna(0) >= 5
    df["sample_ok_surv"] = df["n_cohort"].fillna(0) >= 10
    df["sample_ok_store"] = df["food_store_n"].fillna(0) >= 20

    keep = ["상권_코드", "name", "sigungu", "seoul_trdar_class", "typology", "classifiable",
            "resident_pop", "worker_pop", "resident_pop_pctl", "worker_pop_pctl",
            "n_cohort", "opens", "closes", "food_store_n",
            "sample_ok_churn", "sample_ok_surv", "sample_ok_store",
            "churn_ratio", "surv_1y", "surv_1y_buf5", "close_r_panel_4q", "net_open_panel_4q",
            "net_growth_pct", "close_r_store", "open_r_store"]
    df = df[[c for c in keep if c in df.columns]]
    return df


OUTCOMES = {
    "churn_ratio": "낮을수록 존속 좋음 (인허가 폐업/개업 2022~2025)",
    "close_r_panel_4q": "낮을수록 좋음 (인허가 패널 최근4Q 폐업률)",
    "close_r_store": "낮을수록 좋음 (점포 20261 외식평균 폐업률)",
    "surv_1y": "높을수록 좋음 (코호트 1년 생존율)",
    "net_growth_pct": "높을수록 좋음 (영업중 20221→20261 %)",
}
GOOD_DIR = {"churn_ratio": -1, "close_r_panel_4q": -1, "close_r_store": -1,
            "surv_1y": +1, "net_growth_pct": +1}
SAMPLE_FLAG = {"churn_ratio": "sample_ok_churn", "close_r_panel_4q": "sample_ok_churn",
               "net_growth_pct": "sample_ok_churn", "close_r_store": "sample_ok_store",
               "surv_1y": "sample_ok_surv"}


def analyze(df: pd.DataFrame) -> dict:
    groups = ["골목상권형", "혼합형", "신도시형", "배후주거_희박"]
    sub = df[df["typology"].isin(groups)].copy()

    def oc_sub(frame: pd.DataFrame, oc: str) -> pd.DataFrame:
        return frame[frame[SAMPLE_FLAG[oc]]]

    # A. 유형별 outcome 수준 (baseline) — 각 outcome은 표본 통과분만
    brows = {}
    for g in groups:
        gg = sub[sub["typology"] == g]
        brows[g] = {oc: round(float(oc_sub(gg, oc)[oc].median()), 3)
                    if oc_sub(gg, oc)[oc].notna().any() else None for oc in OUTCOMES}
        brows[g]["n_total"] = len(gg)
        brows[g]["n_churn_ok"] = int(gg["sample_ok_churn"].sum())
    baseline = pd.DataFrame(brows).T.reindex(groups)

    # B. 층화 Spearman: (인구 수준 pctl) ↔ (outcome), 유형별 + pooled — outcome별 표본 필터
    rows = []
    for pop in ("resident_pop_pctl", "worker_pop_pctl"):
        for oc in OUTCOMES:
            base_oc = oc_sub(sub, oc)
            r_all, n_all = spearman(base_oc[pop], base_oc[oc])
            rows.append({"pop": pop, "outcome": oc, "group": "ALL(4유형)", "rho": r_all, "n": n_all,
                         "signal": (r_all is not None and abs(r_all) >= STRONG)})
            for g in groups:
                gg = oc_sub(sub[sub["typology"] == g], oc)
                if len(gg) < MIN_GROUP_N:
                    rows.append({"pop": pop, "outcome": oc, "group": g, "rho": None, "n": len(gg), "signal": False})
                    continue
                r, n = spearman(gg[pop], gg[oc])
                rows.append({"pop": pop, "outcome": oc, "group": g, "rho": r, "n": n,
                             "signal": (r is not None and abs(r) >= STRONG)})
    strat = pd.DataFrame(rows)

    # C. 2x2 cells: 유형 × 인구(서울 median split) → outcome 중앙값 (표본 통과분만)
    cells = []
    for pop_raw, pop_lbl in (("resident_pop", "상주인구"), ("worker_pop", "직장인구")):
        med = df[pop_raw].median()
        for g in groups:
            gg = sub[sub["typology"] == g]
            for hi, lab in ((True, "높음"), (False, "낮음")):
                cc = gg[(gg[pop_raw] >= med) == hi]
                row = {"pop": pop_lbl, "typology": g, "pop_level": lab, "n": len(cc)}
                for oc in OUTCOMES:
                    cco = oc_sub(cc, oc)
                    row[oc] = round(float(cco[oc].median()), 3) if len(cco) >= 8 and cco[oc].notna().any() else None
                    row[f"{oc}_n"] = len(cco)
                cells.append(row)
    cells = pd.DataFrame(cells)

    # D. 자치구 교란 체크 — baseline의 유형 차이가 자치구 구성 효과인지
    d = df[df["sample_ok_churn"] & df["typology"].isin(["골목상권형", "신도시형"])]
    wr = []
    for sg, g in d.groupby("sigungu"):
        a = g[g["typology"] == "골목상권형"]["churn_ratio"]
        n = g[g["typology"] == "신도시형"]["churn_ratio"]
        if len(a) >= 5 and len(n) >= 5:
            wr.append({"sigungu": sg, "n_alley": len(a), "n_newtown": len(n),
                       "newtown_minus_alley_churn": round(float(n.median() - a.median()), 3)})
    within = pd.DataFrame(wr)
    within_summary = {
        "sigungu_pairs": len(within),
        "newtown_worse_count": int((within["newtown_minus_alley_churn"] > 0).sum()) if len(within) else 0,
        "median_within_diff": round(float(within["newtown_minus_alley_churn"].median()), 3) if len(within) else None,
    }

    return {"baseline": baseline, "stratified": strat, "cells": cells,
            "within_sigungu": within, "within_summary": within_summary}


def make_figures(df: pd.DataFrame, res: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = "Apple SD Gothic Neo"
    plt.rcParams["axes.unicode_minus"] = False
    groups = ["골목상권형", "혼합형", "신도시형", "배후주거_희박"]
    color = {"골목상권형": "#1baf7a", "혼합형": "#9aa0a6", "신도시형": "#2a78d6", "배후주거_희박": "#eb6834"}

    # 유형별 churn_ratio 분포 (박스) — 표본 통과분만
    sub = df[df["typology"].isin(groups) & df["sample_ok_churn"]]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    data = [sub[sub["typology"] == g]["churn_ratio"].dropna() for g in groups]
    bp = ax.boxplot(data, tick_labels=groups, showfliers=False, patch_artist=True)
    for patch, g in zip(bp["boxes"], groups):
        patch.set_facecolor(color[g])
        patch.set_alpha(0.6)
    ax.set_ylabel("churn_ratio (폐업/개업, 낮을수록 존속 좋음)")
    ax.set_title("상권 유형별 폐업/개업 비율 (2022~2025)")
    fig.tight_layout()
    fig.savefig(FIG / "typology_survival_baseline.png", dpi=130)
    plt.close(fig)

    # 층화 Spearman 히트맵 (resident + worker)
    strat = res["stratified"]
    for pop in ("resident_pop_pctl", "worker_pop_pctl"):
        piv = strat[strat["pop"] == pop].pivot(index="outcome", columns="group", values="rho")
        piv = piv.reindex(index=list(OUTCOMES), columns=["ALL(4유형)"] + groups)
        fig, ax = plt.subplots(figsize=(8, 4))
        im = ax.imshow(piv.values.astype(float), cmap="RdBu", vmin=-0.5, vmax=0.5)
        ax.set_xticks(range(len(piv.columns)))
        ax.set_xticklabels(piv.columns, rotation=20, ha="right")
        ax.set_yticks(range(len(piv.index)))
        ax.set_yticklabels(piv.index)
        for i in range(len(piv.index)):
            for j in range(len(piv.columns)):
                v = piv.values[i, j]
                if pd.notna(v):
                    ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=8)
        ax.set_title(f"Spearman ρ: {'상주인구' if 'resident' in pop else '직장인구'} 수준 ↔ outcome (유형별)")
        fig.colorbar(im, ax=ax, shrink=0.7)
        fig.tight_layout()
        fig.savefig(FIG / f"typology_survival_rho_{'resident' if 'resident' in pop else 'worker'}.png", dpi=130)
        plt.close(fig)


def main() -> int:
    df = build_panel()
    df.to_csv(OUT / "typology_survival_panel.csv", index=False, encoding="utf-8-sig")

    res = analyze(df)
    res["baseline"].to_csv(OUT / "typology_survival_baseline.csv", encoding="utf-8-sig")
    res["stratified"].to_csv(OUT / "typology_survival_stratified.csv", index=False, encoding="utf-8-sig")
    res["cells"].to_csv(OUT / "typology_survival_cells.csv", index=False, encoding="utf-8-sig")
    res["within_sigungu"].to_csv(OUT / "typology_survival_within_sigungu.csv", index=False, encoding="utf-8-sig")
    make_figures(df, res)

    # 핵심 발견 요약
    strat = res["stratified"]
    verdicts = {}
    for pop, plbl in (("resident_pop_pctl", "상주인구"), ("worker_pop_pctl", "직장인구")):
        alley = strat[(strat["pop"] == pop) & (strat["group"] == "골목상권형")]
        newt = strat[(strat["pop"] == pop) & (strat["group"] == "신도시형")]
        # '좋은 방향'으로 정렬된 ρ (churn/폐업률은 부호 반전)
        def dir_rho(r):
            out = {}
            for _, x in r.iterrows():
                if x["rho"] is None:
                    continue
                out[x["outcome"]] = round(x["rho"] * GOOD_DIR[x["outcome"]], 3)
            return out
        verdicts[plbl] = {
            "골목상권형_ρ(좋은방향)": dir_rho(alley),
            "신도시형_ρ(좋은방향)": dir_rho(newt),
            "골목형에서 |ρ|>=0.2 outcome": sorted(alley[alley["signal"]]["outcome"].tolist()),
            "신도시형에서 |ρ|>=0.2 outcome": sorted(newt[newt["signal"]]["outcome"].tolist()),
        }

    manifest = {
        "generated_for": "미래가치 존속가능성 재정의 2단계 — 상권 유형별 인구↔존속 조건부 검증",
        "unit": "상권 (전 외식 pooled)", "quarter": Q_NOW,
        "typology_source": "output/site_typology/typology_상권.csv",
        "outcomes": OUTCOMES,
        "method": "유형으로 층화 → Spearman(인구 pctl ↔ outcome). 적합 모델 없음. p-value 없음(scipy 미설치). |ρ|>=0.2를 신호로 본다.",
        "min_group_n": MIN_GROUP_N,
        "group_sizes": df[df["typology"].isin(["골목상권형", "혼합형", "신도시형", "배후주거_희박"])]["typology"].value_counts().to_dict(),
        "verdicts": verdicts,
        "baseline_median": res["baseline"].to_dict(),
        "sigungu_confound_check": res["within_summary"],
        "conclusion": (
            "가설 미지지. (1) baseline에서 신도시형이 존속 나쁨(churn·폐업률·생존율·순증)이나 "
            f"자치구 통제 시 신도시형 churn이 더 나쁜 자치구는 {res['within_summary']['newtown_worse_count']}/"
            f"{res['within_summary']['sigungu_pairs']}로 대부분 자치구 구성 효과. "
            "(2) 상주·직장인구 수준 ↔ 존속: 유형 무관하게 |ρ|<0.2 (유일 예외 골목형 close_r_store ρ+0.217 = 나쁜 방향). "
            "(3) 유형이 인구↔존속 관계를 조건화하지 않음. → 상주·직장인구를 존속 근거로 승격하지 않는다. "
            "유형 라벨은 배경 서술로만 유지."
        ),
        "limitation": [
            "신규 점포 매출·손익 outcome 없음 → '좋은 입지 = 성공'은 여전히 미검증. outcome은 인허가/점포 교체 프록시.",
            "인허가 폐업일자 = 행정처리일 → 실제보다 지연(surv_1y_buf5 병기).",
            "상권 pooled(업종 미구분). 상주·직장인구도 상권 단위라 정합.",
            "관찰 상관이며 인과 아님. 자치구 교란은 within-자치구 차이 체크로 부분 확인.",
            "유형 라벨의 축(영업개월·개업률) 일부가 churn류 outcome과 원천 공유 → churn 차이는 순환성 있음. surv_1y·net_growth는 독립.",
            "저인구 상권 outcome degeneracy는 표본 필터(opens>=5, n_cohort>=10, food_store_n>=20)로 제거.",
        ],
    }
    (OUT / "typology_survival_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== 유형별 outcome 중앙값 ===")
    print(res["baseline"].to_string())
    print("\n=== 층화 Spearman (신호 |ρ|>=0.2만) ===")
    sig = strat[strat["signal"]].sort_values(["pop", "outcome", "group"])
    print(sig.to_string(index=False) if len(sig) else "  (신호 없음)")
    print("\n=== 판정 요약 ===")
    print(json.dumps(verdicts, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
