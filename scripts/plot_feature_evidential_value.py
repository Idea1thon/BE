"""FC 피처 설명력 시각화. 입력: output/feature_validation/*.csv → output/figures/feature_validation/*.png"""
from __future__ import annotations

import json
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams["font.family"] = "Apple SD Gothic Neo"
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IN = os.path.join(ROOT, "output", "feature_validation")
FIG = os.path.join(ROOT, "output", "figures", "feature_validation")
os.makedirs(FIG, exist_ok=True)

DIM_COLOR = {"현재 수요": "#4C72B0", "수요 구성": "#55A868", "경쟁·시장수용": "#C44E52",
             "진입 건전성": "#8172B2", "비용 부담": "#CCB974", "미래 신호": "#64B5CD"}


def save(fig, name):
    fig.savefig(os.path.join(FIG, name), dpi=130, bbox_inches="tight")
    plt.close(fig)


def fig_outcome_bars():
    v = pd.read_csv(os.path.join(IN, "fc_verdict.csv"), encoding="utf-8-sig")
    v["dim0"] = v["dimension"].str.split("|").str[0]
    v = v.sort_values("quality_abs_rho")
    fig, axes = plt.subplots(1, 2, figsize=(13, 8), sharey=True)
    for ax, col, ttl in [(axes[0], "quality_abs_rho", "입지 품질 (폐업 낮음·생존)  |ρ| max"),
                         (axes[1], "momentum_abs_rho", "시장 모멘텀 (점포 성장)  |ρ| max")]:
        colors = [DIM_COLOR.get(d, "#999") for d in v["dim0"]]
        ax.barh(v["항목"], v[col], color=colors)
        ax.axvline(0.25, color="#333", lw=0.8, ls="--")
        ax.axvline(0.1, color="#aaa", lw=0.7, ls=":")
        ax.set_xlabel(ttl, fontsize=10)
        ax.set_xlim(0, max(0.35, v[col].max() * 1.1))
    axes[0].tick_params(axis="y", labelsize=8.5)
    fig.suptitle("FC 피처 ↔ outcome 최대 |Spearman ρ|  (0.25=신호, 0.1=약함 기준선)",
                 fontsize=12, fontweight="bold")
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in DIM_COLOR.values()]
    fig.legend(handles, DIM_COLOR.keys(), loc="lower center", ncol=6, fontsize=8.5,
               bbox_to_anchor=(0.5, -0.02))
    save(fig, "fc_outcome_bars.png")


def fig_redundancy():
    M = pd.read_csv(os.path.join(IN, "fc_redundancy_matrix.csv"), encoding="utf-8-sig", index_col=0)
    # 상관 기반 간단 정렬 (첫 행 기준)
    order = M.abs().sum().sort_values(ascending=False).index
    M = M.loc[order, order]
    fig, ax = plt.subplots(figsize=(11, 9.5))
    im = ax.imshow(M.values, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(M)))
    ax.set_yticks(range(len(M)))
    ax.set_xticklabels(M.columns, rotation=90, fontsize=7)
    ax.set_yticklabels(M.index, fontsize=7)
    for i in range(len(M)):
        for j in range(len(M)):
            val = M.values[i, j]
            if i != j and abs(val) >= 0.7:
                ax.text(j, i, f"{val:.2f}", ha="center", va="center", fontsize=6.5,
                        color="white" if abs(val) > 0.5 else "black")
    fig.colorbar(im, ax=ax, shrink=0.7, label="Spearman ρ (업종 내 분위)")
    ax.set_title("FC × FC 중복도  (|ρ|≥0.7 표기)", fontsize=12, fontweight="bold")
    save(fig, "fc_redundancy_heatmap.png")


def fig_dimension():
    d = pd.read_csv(os.path.join(IN, "dimension_separability.csv"), encoding="utf-8-sig")
    x = np.arange(len(d))
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(x - 0.2, d["within_mean_abs_rho"], 0.4, label="차원 내 평균 |ρ|", color="#4C72B0")
    ax.bar(x + 0.2, d["between_mean_abs_rho"], 0.4, label="차원 간 평균 |ρ|", color="#C44E52")
    ax.set_xticks(x)
    ax.set_xticklabels(d["dimension"], fontsize=9)
    ax.set_ylabel("평균 |Spearman ρ|")
    ax.legend(fontsize=9)
    ax.set_title("7 근거 차원 분리도 — 차원 내 응집 vs 차원 간 (절대값 자체가 작음에 유의)",
                 fontsize=11, fontweight="bold")
    save(fig, "dimension_separability.png")


def fig_incremental():
    inc = pd.read_csv(os.path.join(IN, "incremental_partial_corr.csv"), encoding="utf-8-sig")
    inc = inc.sort_values("rho_partial", key=lambda s: s.abs())
    fig, ax = plt.subplots(figsize=(9, 8))
    ax.barh(inc["항목"], inc["rho_raw"], 0.4, label="raw ρ (churn)", color="#bbb")
    ax.barh([i + 0.4 for i in range(len(inc))], inc["rho_partial"], 0.4,
            label="편 ρ (점포밀도·매출 통제 후)", color="#4C72B0")
    ax.set_yticks([i + 0.2 for i in range(len(inc))])
    ax.set_yticklabels(inc["항목"], fontsize=8.5)
    ax.axvline(0, color="#333", lw=0.8)
    ax.axvline(0.15, color="#aaa", lw=0.7, ls=":")
    ax.axvline(-0.15, color="#aaa", lw=0.7, ls=":")
    ax.legend(fontsize=9)
    ax.set_title("churn_ratio 대비 증분 신호 — 편상관 (모델 적합 아님)", fontsize=11, fontweight="bold")
    save(fig, "incremental_partial_corr.png")


def fig_label_anchor():
    lab = pd.read_csv(os.path.join(IN, "label_category_check.csv"), encoding="utf-8-sig")
    order = ["LH", "HH", "LL", "HL"]
    lab = lab.set_index("chg_label").reindex(order).reset_index()
    nm = {"LH": "LH 상권확장", "HH": "HH 정체", "LL": "LL 다이나믹", "HL": "HL 상권축소"}
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    axes[0].bar(lab["chg_label"].map(nm), lab["churn_ratio_mean"], color="#8172B2")
    axes[0].set_title("인허가 churn_ratio 평균 (opens≥5)")
    axes[0].axhline(1.0, color="#999", lw=0.7, ls="--")
    axes[1].bar(lab["chg_label"].map(nm), lab["close_r_4q_mean"], color="#C44E52")
    axes[1].set_title("점포 4Q 평균 폐업률 % (점포≥5)")
    for ax in axes:
        ax.tick_params(axis="x", labelsize=8.5)
    fig.suptitle("알려진-정답 앵커: 상권변화지표 라벨(범주) → outcome  "
                 "(change_indicator_store_correlation 재현 — LL 최고, HH/LH 낮음)",
                 fontsize=10.5, fontweight="bold")
    save(fig, "label_category_anchor.png")


def main():
    fig_outcome_bars()
    fig_redundancy()
    fig_dimension()
    fig_incremental()
    fig_label_anchor()
    print(f"→ {FIG}/  (5 그림)")


if __name__ == "__main__":
    main()
