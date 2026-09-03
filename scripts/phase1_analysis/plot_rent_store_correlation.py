import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

plt.rcParams["font.family"] = "Apple SD Gothic Neo"
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "output")
FIG_DIR = os.path.join(ROOT, "output", "figures", "rent_store")
os.makedirs(FIG_DIR, exist_ok=True)

# dataviz 스킬 참조 팔레트 — 진단(diverging) 쌍: blue #2a78d6 <-> gray 중립 <-> red #e34948
SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
DIVERGING = LinearSegmentedColormap.from_list(
    "diverging_blue_red", ["#e34948", "#f0efec", "#2a78d6"]
)


def draw_heatmap(df, title, subtitle, path, vmax=0.3):
    header_in = 1.1  # 제목+부제 영역 고정 높이(인치) — 행 개수와 무관하게 유지
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

    # 제목/부제는 그림 전체 기준 고정 위치(fig.text)로 배치 — 행 수와 무관하게 겹치지 않음
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


pooled = pd.read_csv(os.path.join(OUT, "rent_store_correlation_pooled.csv"), index_col=0)
by_biz = pd.read_csv(os.path.join(OUT, "rent_store_correlation_by_industry.csv"), index_col=0)

draw_heatmap(
    pooled,
    "임대료·공실률·수익률 x 점포 지표 상관관계",
    "겹치는 63개 지역, 2021Q1~2025Q4(20개 분기), 10개 외식업종 통합 (n=12,600) · log1p Pearson r",
    os.path.join(FIG_DIR, "rent_store_correlation_pooled.png"),
    vmax=0.3,
)

draw_heatmap(
    by_biz,
    "업종별 임대료 x 점포 지표 상관관계",
    "겹치는 63개 지역, 2021Q1~2025Q4(20개 분기) · log1p Pearson r (임대료 기준)",
    os.path.join(FIG_DIR, "rent_store_correlation_by_industry.png"),
    vmax=0.3,
)
