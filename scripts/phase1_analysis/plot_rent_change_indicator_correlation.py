import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

plt.rcParams["font.family"] = "Apple SD Gothic Neo"
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "output")
FIG_DIR = os.path.join(ROOT, "output", "figures", "rent_change")
os.makedirs(FIG_DIR, exist_ok=True)

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
DIVERGING = LinearSegmentedColormap.from_list("diverging_blue_red", ["#e34948", "#f0efec", "#2a78d6"])
BLUE = "#2a78d6"
ORANGE = "#eb6834"

QUADRANT_ORDER = ["LL", "LH", "HL", "HH"]
QUADRANT_LABEL = {"LL": "다이나믹(LL)", "LH": "상권확장(LH)", "HL": "상권축소(HL)", "HH": "정체(HH)"}


def draw_heatmap(df, title, subtitle, path, vmax=0.3):
    header_in = 1.1
    fig_h = 0.55 * len(df) + header_in + 0.6
    fig, ax = plt.subplots(figsize=(6.5, fig_h), facecolor=SURFACE)
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
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", color=txt_color, fontsize=9.5)

    fig.text(0.08, 1 - 0.4 / fig_h, title, color=INK_PRIMARY, fontsize=13, fontweight="bold", ha="left", va="top")
    fig.text(0.08, 1 - 0.75 / fig_h, subtitle, color=INK_MUTED, fontsize=9, ha="left", va="top")

    cbar = fig.colorbar(im, ax=ax, fraction=0.05, pad=0.03)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(length=0, labelsize=8, labelcolor=INK_MUTED)
    cbar.set_label("Pearson r", color=INK_MUTED, fontsize=8.5)

    fig.tight_layout(rect=[0, 0, 1, 1 - header_in / fig_h])
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print("saved:", path)


def draw_quadrant_bars(quad_df, title, subtitle, path):
    """사분면별 평균 임대료 vs 공실률 (이중 y축)."""
    fig, ax1 = plt.subplots(figsize=(7.5, 5), facecolor=SURFACE)
    ax1.set_facecolor(SURFACE)
    x = np.arange(len(QUADRANT_ORDER))
    width = 0.35

    rent_vals = quad_df.loc[QUADRANT_ORDER, "임대료"]
    vac_vals = quad_df.loc[QUADRANT_ORDER, "공실률"]

    b1 = ax1.bar(x - width / 2, rent_vals, width, color=BLUE, label="평균 임대료(천원/㎡)", zorder=3)
    ax1.set_ylabel("평균 임대료", color=BLUE, fontsize=9.5)
    ax1.tick_params(axis="y", labelcolor=BLUE, length=0)

    ax2 = ax1.twinx()
    b2 = ax2.bar(x + width / 2, vac_vals, width, color=ORANGE, label="평균 공실률(%)", zorder=3)
    ax2.set_ylabel("평균 공실률(%)", color=ORANGE, fontsize=9.5)
    ax2.tick_params(axis="y", labelcolor=ORANGE, length=0)

    ax1.set_xticks(x)
    ax1.set_xticklabels([QUADRANT_LABEL[q] for q in QUADRANT_ORDER], color=INK_SECONDARY, fontsize=10)
    ax1.tick_params(axis="x", length=0)
    for spine in ["top"]:
        ax1.spines[spine].set_visible(False)
        ax2.spines[spine].set_visible(False)
    ax1.spines["left"].set_color(BLUE)
    ax2.spines["right"].set_color(ORANGE)
    ax1.spines["bottom"].set_color(GRIDLINE)
    ax1.grid(axis="y", color=GRIDLINE, linewidth=0.7, zorder=0)

    fig.text(0.08, 0.97, title, color=INK_PRIMARY, fontsize=13, fontweight="bold", ha="left", va="top")
    fig.text(0.08, 0.90, subtitle, color=INK_MUTED, fontsize=9, ha="left", va="top")

    lines = [b1, b2]
    labels = [l.get_label() for l in lines]
    fig.legend(lines, labels, frameon=False, fontsize=9, ncol=2, labelcolor=INK_SECONDARY,
               loc="lower center", bbox_to_anchor=(0.5, -0.02))

    fig.tight_layout(rect=[0, 0.08, 1, 0.86])
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print("saved:", path)


for unit, prefix in [("상권", "rent_change_sg"), ("행정동", "rent_change_dong")]:
    corr_df = pd.read_csv(os.path.join(OUT, f"{prefix}_correlation.csv"), index_col=0)
    draw_heatmap(
        corr_df,
        f"임대료·공실률·수익률 x 상권변화지표 gap 상관관계 ({unit} 단위)",
        f"63개 지역, 2021Q1~2025Q4(20개 분기), 업종 구분 없음(n=1,146) · Pearson r",
        os.path.join(FIG_DIR, f"{prefix}_correlation.png"),
        vmax=0.3,
    )
    quad_df = pd.read_csv(os.path.join(OUT, f"{prefix}_quadrant_mean.csv"), index_col=0)
    draw_quadrant_bars(
        quad_df,
        f"상권변화지표 사분면별 평균 임대료·공실률 ({unit} 단위)",
        f"63개 지역, 2021Q1~2025Q4 지역x분기 관측치(n=1,260) · 사분면은 면적가중 평균 gap 부호로 재판정",
        os.path.join(FIG_DIR, f"{prefix}_quadrant_bars.png"),
    )

print("done")
