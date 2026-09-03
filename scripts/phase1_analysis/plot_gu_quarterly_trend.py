import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

plt.rcParams["font.family"] = "Apple SD Gothic Neo"
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "output", "gu")
FIG_DIR = os.path.join(ROOT, "output", "figures", "gu")
os.makedirs(FIG_DIR, exist_ok=True)

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
GREEN = "#1baf7a"
DIVERGING = LinearSegmentedColormap.from_list("diverging_blue_red", ["#e34948", "#f0efec", "#2a78d6"])
CAT4_QUAD = {"LL": "#2a78d6", "LH": "#1baf7a", "HL": "#eda100", "HH": "#e34948"}  # 다이나믹/상권확장/상권축소/정체

summary = pd.read_csv(os.path.join(OUT, "gu_trend_summary.csv"), encoding="utf-8-sig")
summary = summary.sort_values("추정매출_변화율%", ascending=False)
gu_order = summary["자치구"].tolist()


# ---------- (1) 3개 지표 히트맵: 25개 자치구 x 21개 분기, 지수화(첫분기=100) ----------
def load_trend(name):
    df = pd.read_csv(os.path.join(OUT, f"gu_trend_{name}.csv"), encoding="utf-8-sig", index_col=0)
    idx = df.div(df.iloc[0]) * 100
    return idx[gu_order]  # 매출 성장률 순으로 자치구 정렬


fig, axes = plt.subplots(1, 3, figsize=(19, 9), facecolor=SURFACE)
for ax, name in zip(axes, ["유동인구", "추정매출", "점포"]):
    ax.set_facecolor(SURFACE)
    idx = load_trend(name)
    vmax = np.nanmax(np.abs(idx.values - 100))
    im = ax.imshow(idx.values.T, cmap=DIVERGING, vmin=100 - vmax, vmax=100 + vmax, aspect="auto")
    ax.set_yticks(range(len(gu_order)))
    ax.set_yticklabels(gu_order, fontsize=7.3, color=INK_SECONDARY)
    ax.set_xticks(range(len(idx.index))[::4])
    ax.set_xticklabels([str(q) for q in idx.index][::4], rotation=45, ha="right", fontsize=7, color=INK_MUTED)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title(name, color=INK_PRIMARY, fontsize=12.5, fontweight="bold")
    cbar = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(length=0, labelsize=7, labelcolor=INK_MUTED)

fig.text(0.02, 0.99, "25개 자치구 x 분기별 추세 (첫 분기=100 지수, 자치구는 추정매출 성장률 순 정렬)",
          color=INK_PRIMARY, fontsize=14, fontweight="bold", ha="left", va="top")
fig.text(0.02, 0.965, "2021Q1~2026Q1 · 파랑=증가, 빨강=감소(첫 분기 대비) · 상단 자치구일수록 매출 성장률 높음",
          color=INK_MUTED, fontsize=9.5, ha="left", va="top")
fig.tight_layout(rect=[0, 0, 1, 0.94])
out1 = os.path.join(FIG_DIR, "gu_trend_heatmap.png")
fig.savefig(out1, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out1)

# ---------- (2) 요약 막대: 자치구별 매출 변화율 vs 유동인구 변화율 (다이버전스 확인) ----------
fig, ax = plt.subplots(figsize=(10, 9), facecolor=SURFACE)
ax.set_facecolor(SURFACE)
y = np.arange(len(gu_order))
ax.barh(y, summary["추정매출_변화율%"], color=BLUE, height=0.6, zorder=3, label="추정매출 변화율%")
ax.scatter(summary["유동인구_변화율%"], y, color=ORANGE, s=28, zorder=4, label="유동인구 변화율%")
ax.axvline(0, color=GRIDLINE, linewidth=1)
ax.set_yticks(y)
ax.set_yticklabels(gu_order, fontsize=9, color=INK_SECONDARY)
ax.invert_yaxis()
ax.tick_params(axis="x", length=0, labelcolor=INK_MUTED)
for spine in ["top", "right"]:
    ax.spines[spine].set_visible(False)
ax.spines["left"].set_color(GRIDLINE)
ax.spines["bottom"].set_color(GRIDLINE)
ax.grid(axis="x", color=GRIDLINE, linewidth=0.6, zorder=0)
ax.set_xlabel("변화율% (2021Q1 -> 2026Q1)", color=INK_SECONDARY, fontsize=9.5)
fig.text(0.06, 0.985, "25개 자치구: 추정매출 변화율(막대) vs 유동인구 변화율(점)",
          color=INK_PRIMARY, fontsize=13.5, fontweight="bold", ha="left", va="top")
fig.text(0.06, 0.965, "거의 모든 자치구에서 매출은 늘고 유동인구는 정체·감소 — 서울 전체(18장)와 같은 패턴이 자치구 단위에서도 재현",
          color=INK_MUTED, fontsize=9, ha="left", va="top")
ax.legend(frameon=False, fontsize=9, labelcolor=INK_SECONDARY, loc="lower right")
fig.tight_layout(rect=[0, 0, 1, 0.94])
out2 = os.path.join(FIG_DIR, "gu_trend_sales_vs_flow.png")
fig.savefig(out2, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out2)

# ---------- (3) 상권변화지표 최신분기 구성비 스택바 (25개 자치구) ----------
fig, ax = plt.subplots(figsize=(10, 9), facecolor=SURFACE)
ax.set_facecolor(SURFACE)
left = np.zeros(len(gu_order))
for code, label in [("LL", "LL·다이나믹"), ("LH", "LH·상권확장"), ("HL", "HL·상권축소"), ("HH", "HH·정체")]:
    vals = summary.set_index("자치구").loc[gu_order, f"{code}_최신비중%"].values
    ax.barh(y, vals, left=left, color=CAT4_QUAD[code], height=0.65, zorder=3, label=label)
    left += vals
ax.set_yticks(y)
ax.set_yticklabels(gu_order, fontsize=9, color=INK_SECONDARY)
ax.invert_yaxis()
ax.set_xlim(0, 100)
ax.tick_params(axis="x", length=0, labelcolor=INK_MUTED)
for spine in ["top", "right"]:
    ax.spines[spine].set_visible(False)
ax.spines["left"].set_color(GRIDLINE)
ax.spines["bottom"].set_color(GRIDLINE)
ax.grid(axis="x", color=GRIDLINE, linewidth=0.6, zorder=0)
ax.set_xlabel("행정동 구성비% (2026Q1 기준)", color=INK_SECONDARY, fontsize=9.5, labelpad=10)
fig.text(0.06, 0.985, "25개 자치구: 상권변화지표(LL/LH/HL/HH) 최신분기 행정동 구성비",
          color=INK_PRIMARY, fontsize=13.5, fontweight="bold", ha="left", va="top")
fig.text(0.06, 0.965, "자치구 정렬은 (1)(2)와 동일하게 추정매출 성장률 순 · 중구·종로구처럼 매출 성장률 1·2위인 곳이 HH(정체) 비중도 최상위",
          color=INK_MUTED, fontsize=9, ha="left", va="top")
ax.legend(frameon=False, fontsize=9.5, labelcolor=INK_SECONDARY, ncol=4,
          loc="lower center", bbox_to_anchor=(0.5, -0.12))
fig.tight_layout(rect=[0, 0.05, 1, 0.94])
out3 = os.path.join(FIG_DIR, "gu_trend_change_indicator_composition.png")
fig.savefig(out3, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out3)
