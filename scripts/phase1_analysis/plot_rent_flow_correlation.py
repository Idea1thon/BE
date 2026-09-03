import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams["font.family"] = "Apple SD Gothic Neo"
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "output")
FIG_DIR = os.path.join(ROOT, "output", "figures", "rent_flow")
os.makedirs(FIG_DIR, exist_ok=True)

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
CAT_BLUE = "#2a78d6"
CAT_ORANGE = "#eb6834"
CAT_AQUA = "#1baf7a"


def draw_unit_comparison(df, title, subtitle, path):
    metrics = df.index.tolist()
    units = df.columns.tolist()
    colors = [CAT_BLUE, CAT_ORANGE, CAT_AQUA]

    fig, ax = plt.subplots(figsize=(8.2, 4.8), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)

    n_metrics = len(metrics)
    n_units = len(units)
    bar_w = 0.24
    x = np.arange(n_metrics)

    for i, (unit, color) in enumerate(zip(units, colors)):
        offset = (i - (n_units - 1) / 2) * bar_w
        vals = df[unit].values
        bars = ax.bar(x + offset, vals, width=bar_w * 0.9, color=color, label=unit, zorder=3)
        for rect, v in zip(bars, vals):
            ax.text(rect.get_x() + rect.get_width() / 2, v + 0.01 if v >= 0 else v - 0.018,
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
    ax.set_ylabel("Pearson r (log1p)", color=INK_MUTED, fontsize=8.5)

    fig.text(0.06, 0.97, title, color=INK_PRIMARY, fontsize=13, fontweight="bold", ha="left", va="top")
    fig.text(0.06, 0.90, subtitle, color=INK_MUTED, fontsize=9.5, ha="left", va="top")

    ax.legend(frameon=False, fontsize=9, ncol=3, labelcolor=INK_SECONDARY, loc="lower center",
              bbox_to_anchor=(0.5, -0.22))

    fig.tight_layout(rect=[0, 0.06, 1, 0.86])
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print("saved:", path)


summary = pd.read_csv(os.path.join(OUT, "rent_flow_unit_comparison.csv"), index_col=0)
# 컬럼명에 n이 붙어있어 범례용으로 그대로 쓰되, 표 자체는 그대로 사용
draw_unit_comparison(
    summary,
    "임대료·공실률·수익률 x 총_유동인구_수, 공간 단위별 비교",
    "2021Q1~2025Q4(20개 분기) · log1p Pearson r · 지역x분기 단위(업종 구분 없음)",
    os.path.join(FIG_DIR, "rent_flow_unit_comparison.png"),
)
