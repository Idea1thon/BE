import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

plt.rcParams["font.family"] = "Apple SD Gothic Neo"
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "output")
FIG_DIR = os.path.join(ROOT, "output", "figures", "rent_sales")
os.makedirs(FIG_DIR, exist_ok=True)

# dataviz 스킬 참조 팔레트
SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
DIVERGING = LinearSegmentedColormap.from_list(
    "diverging_blue_red", ["#e34948", "#f0efec", "#2a78d6"]
)
# 카테고리컬 팔레트 1~3번 슬롯(blue/orange/aqua) — 3계열 all-pairs 검증 통과 조합
CAT_BLUE = "#2a78d6"
CAT_ORANGE = "#eb6834"
CAT_AQUA = "#1baf7a"


def draw_heatmap(df, title, subtitle, path, vmax=0.3):
    header_in = 1.1
    fig_h = 0.55 * len(df) + header_in + 0.6
    fig, ax = plt.subplots(figsize=(7.5, fig_h), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    im = ax.imshow(df.values, cmap=DIVERGING, vmin=-vmax, vmax=vmax, aspect="auto")

    ax.set_xticks(range(len(df.columns)))
    ax.set_xticklabels(df.columns, color=INK_SECONDARY, fontsize=10)
    ax.set_yticks(range(len(df.index)))
    ax.set_yticklabels(df.index, color=INK_SECONDARY, fontsize=10)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    for i in range(df.shape[0]):
        for j in range(df.shape[1]):
            v = df.values[i, j]
            txt_color = INK_PRIMARY if abs(v) < vmax * 0.7 else SURFACE
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                     color=txt_color, fontsize=9.5)

    fig.text(0.06, 1 - 0.4 / fig_h, title, color=INK_PRIMARY, fontsize=13,
              fontweight="bold", ha="left", va="top")
    fig.text(0.06, 1 - 0.75 / fig_h, subtitle, color=INK_MUTED, fontsize=9.5,
              ha="left", va="top")

    cbar = fig.colorbar(im, ax=ax, fraction=0.05, pad=0.03)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(length=0, labelsize=8, labelcolor=INK_MUTED)
    cbar.set_label("Pearson r", color=INK_MUTED, fontsize=8.5)

    fig.tight_layout(rect=[0, 0, 1, 1 - header_in / fig_h])
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print("saved:", path)


def draw_unit_comparison(df, title, subtitle, path, n_labels):
    """공간 단위(상권/행정동/상권배후지) 3계열 그룹 막대 — 카테고리컬 정체성 비교."""
    metrics = df.index.tolist()
    units = df.columns.tolist()
    colors = [CAT_BLUE, CAT_ORANGE, CAT_AQUA]

    fig, ax = plt.subplots(figsize=(7.5, 4.6), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)

    n_metrics = len(metrics)
    n_units = len(units)
    bar_w = 0.24
    x = np.arange(n_metrics)

    for i, (unit, color) in enumerate(zip(units, colors)):
        offset = (i - (n_units - 1) / 2) * bar_w
        vals = df[unit].values
        bars = ax.bar(x + offset, vals, width=bar_w * 0.9, color=color,
                       label=f"{unit} ({n_labels[unit]})", zorder=3)
        for rect, v in zip(bars, vals):
            ax.text(rect.get_x() + rect.get_width() / 2, v + 0.008 if v >= 0 else v - 0.02,
                     f"{v:.2f}", ha="center",
                     va="bottom" if v >= 0 else "top", fontsize=8.5, color=INK_SECONDARY)

    ax.axhline(0, color=GRIDLINE, linewidth=1, zorder=1)
    ax.set_xticks(x)
    ax.set_xticklabels(metrics, color=INK_SECONDARY, fontsize=10)
    ax.tick_params(axis="y", length=0, labelsize=8.5, labelcolor=INK_MUTED)
    ax.tick_params(axis="x", length=0)
    for spine in ["top", "right", "left"]:
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(GRIDLINE)
    ax.grid(axis="y", color=GRIDLINE, linewidth=0.7, zorder=0)
    ax.set_ylabel("Pearson r (log1p, 임대료 기준)", color=INK_MUTED, fontsize=8.5)

    fig.text(0.06, 0.97, title, color=INK_PRIMARY, fontsize=13, fontweight="bold", ha="left", va="top")
    fig.text(0.06, 0.90, subtitle, color=INK_MUTED, fontsize=9.5, ha="left", va="top")

    ax.legend(frameon=False, fontsize=9, ncol=3, labelcolor=INK_SECONDARY, loc="lower center",
              bbox_to_anchor=(0.5, -0.24))

    fig.tight_layout(rect=[0, 0.06, 1, 0.86])
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print("saved:", path)


# ---------- 상권(기존 12장) 재사용 히트맵 ----------
pooled_sg = pd.read_csv(os.path.join(OUT, "rent_sales_correlation_pooled.csv"), index_col=0)
by_biz_sg = pd.read_csv(os.path.join(OUT, "rent_sales_correlation_by_industry.csv"), index_col=0)

# ---------- 행정동 ----------
pooled_dong = pd.read_csv(os.path.join(OUT, "rent_sales_dong_correlation_pooled.csv"), index_col=0)
by_biz_dong = pd.read_csv(os.path.join(OUT, "rent_sales_dong_correlation_by_industry.csv"), index_col=0)

draw_heatmap(
    pooled_dong,
    "임대료·공실률·수익률 x 추정매출 지표 상관관계 (행정동 단위)",
    "63개 지역의 소속 행정동, 2021Q1~2025Q4(20개 분기), 10개 외식업종 통합 (n=12,600) · log1p Pearson r",
    os.path.join(FIG_DIR, "rent_sales_dong_correlation_pooled.png"),
    vmax=0.3,
)
draw_heatmap(
    by_biz_dong,
    "업종별 임대료 x 추정매출 지표 상관관계 (행정동 단위)",
    "63개 지역의 소속 행정동, 2021Q1~2025Q4(20개 분기) · log1p Pearson r (임대료 기준)",
    os.path.join(FIG_DIR, "rent_sales_dong_correlation_by_industry.png"),
    vmax=0.4,
)

# ---------- 상권배후지 ----------
pooled_bg = pd.read_csv(os.path.join(OUT, "rent_sales_bg_correlation_pooled.csv"), index_col=0)
by_biz_bg = pd.read_csv(os.path.join(OUT, "rent_sales_bg_correlation_by_industry.csv"), index_col=0)

draw_heatmap(
    pooled_bg,
    "임대료·공실률·수익률 x 추정매출 지표 상관관계 (상권배후지 단위)",
    "63개 중 60개 지역의 소속 상권배후지(면적가중 다중소속), 2021Q1~2025Q4(20개 분기), 10개 외식업종 통합 (n=12,000) · log1p Pearson r",
    os.path.join(FIG_DIR, "rent_sales_bg_correlation_pooled.png"),
    vmax=0.3,
)
draw_heatmap(
    by_biz_bg,
    "업종별 임대료 x 추정매출 지표 상관관계 (상권배후지 단위)",
    "63개 중 60개 지역의 소속 상권배후지(면적가중 다중소속), 2021Q1~2025Q4(20개 분기) · log1p Pearson r (임대료 기준)",
    os.path.join(FIG_DIR, "rent_sales_bg_correlation_by_industry.png"),
    vmax=0.4,
)

# ---------- 3단위 비교 (임대료 x 매출, pooled / 지역x분기 독립표본) ----------
pooled_cmp = pd.read_csv(os.path.join(OUT, "rent_sales_unit_comparison_pooled.csv"), index_col=0)
rq_cmp = pd.read_csv(os.path.join(OUT, "rent_sales_unit_comparison_region_quarter.csv"), index_col=0)

draw_unit_comparison(
    pooled_cmp,
    "임대료 x 매출 상관계수, 공간 단위별 비교 (풀링)",
    "지역×분기×업종 패널 · log1p Pearson r · 상권=12,600행/행정동=12,600행/상권배후지=12,000행",
    os.path.join(FIG_DIR, "rent_sales_unit_comparison_pooled.png"),
    n_labels={"상권(11-2/12-2)": "n≈11,460", "행정동": "n=11,460", "상권배후지": "n=11,000"},
)
draw_unit_comparison(
    rq_cmp,
    "임대료 x 매출 상관계수, 공간 단위별 비교 (지역x분기 독립표본)",
    "업종 10개 합산 후 지역×분기 단위 · log1p Pearson r",
    os.path.join(FIG_DIR, "rent_sales_unit_comparison_region_quarter.png"),
    n_labels={"상권(11-2/12-2)": "n=1,260", "행정동": "n=1,260", "상권배후지": "n=1,200"},
)
