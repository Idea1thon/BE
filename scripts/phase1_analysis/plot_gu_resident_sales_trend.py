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
DIVERGING = LinearSegmentedColormap.from_list("diverging_blue_red", ["#e34948", "#f0efec", "#2a78d6"])

summary = pd.read_csv(os.path.join(OUT, "gu_resident_sales_summary.csv"), encoding="utf-8-sig")
summary = summary.sort_values("추정매출_변화율%", ascending=False)
gu_order = summary["자치구"].tolist()
y = np.arange(len(gu_order))

resident = pd.read_csv(os.path.join(OUT, "gu_trend_상주인구.csv"), encoding="utf-8-sig", index_col=0)[gu_order]
sales = pd.read_csv(os.path.join(OUT, "gu_trend_추정매출_상주인구비교용.csv"), encoding="utf-8-sig", index_col=0)[gu_order]


# ---------- (1) 히트맵: 상주인구 vs 추정매출, 25개 자치구 x 21개 분기 (첫분기=100) ----------
def index100(df):
    return df.div(df.iloc[0]) * 100


fig, axes = plt.subplots(1, 2, figsize=(14, 9), facecolor=SURFACE)
for ax, name, df in zip(axes, ["상주인구", "추정매출"], [resident, sales]):
    ax.set_facecolor(SURFACE)
    idx = index100(df)
    vmax = np.nanmax(np.abs(idx.values - 100))
    im = ax.imshow(idx.values.T, cmap=DIVERGING, vmin=100 - vmax, vmax=100 + vmax, aspect="auto")
    ax.set_yticks(range(len(gu_order)))
    ax.set_yticklabels(gu_order, fontsize=7.6, color=INK_SECONDARY)
    ax.set_xticks(range(len(idx.index))[::4])
    ax.set_xticklabels([str(q) for q in idx.index][::4], rotation=45, ha="right", fontsize=7.3, color=INK_MUTED)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title(name, color=INK_PRIMARY, fontsize=12.5, fontweight="bold")
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(length=0, labelsize=7, labelcolor=INK_MUTED)

fig.text(0.02, 0.99, "25개 자치구: 상주인구 vs 추정매출 분기별 추세 (첫 분기=100 지수, 매출 성장률 순 정렬)",
          color=INK_PRIMARY, fontsize=13.5, fontweight="bold", ha="left", va="top")
fig.text(0.02, 0.965, "상주인구는 계단식 데이터(21개 분기 중 20224·20234 딱 2번만 실제로 바뀌고 나머지는 완전 동일값, 20234 이후 7개 분기 그대로 고정) — 매끄러운 추세로 오독 금지",
          color=INK_MUTED, fontsize=9, ha="left", va="top")
fig.tight_layout(rect=[0, 0, 1, 0.93])
out1 = os.path.join(FIG_DIR, "gu_resident_sales_heatmap.png")
fig.savefig(out1, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out1)

# ---------- (2) 자치구별 변화율: 상주인구 vs 매출 ----------
fig, ax = plt.subplots(figsize=(10, 9), facecolor=SURFACE)
ax.set_facecolor(SURFACE)
ax.barh(y, summary["추정매출_변화율%"], color=BLUE, height=0.6, zorder=3, label="추정매출 변화율%")
ax.scatter(summary["상주인구_변화율%"], y, color=ORANGE, s=28, zorder=4, label="상주인구 변화율%")
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
fig.text(0.06, 0.985, "25개 자치구: 추정매출 변화율(막대) vs 상주인구 변화율(점) · 2021Q1~2026Q1 전체 기간",
          color=INK_PRIMARY, fontsize=13.5, fontweight="bold", ha="left", va="top")
fig.text(0.06, 0.965, "거의 모든 자치구에서 상주인구는 소폭 감소(-8~0%), 매출은 크게 증가(+20~+64%) — 상주인구는 계단식 데이터라 이 변화율도 사실상 20224·20234 두 시점 변화의 합",
          color=INK_MUTED, fontsize=8.6, ha="left", va="top")
ax.legend(frameon=False, fontsize=9, labelcolor=INK_SECONDARY, loc="lower right")
fig.tight_layout(rect=[0, 0, 1, 0.94])
out2 = os.path.join(FIG_DIR, "gu_resident_sales_change.png")
fig.savefig(out2, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out2)

# ---------- (3) 레벨 상관(허상) vs 실제 전환점 산점도(진짜 신호 점검) ----------
trans = pd.read_csv(os.path.join(OUT, "gu_resident_sales_transition_points.csv"), encoding="utf-8-sig")
trans_corr = np.corrcoef(trans["상주인구_변화%"], trans["매출_변화%"])[0, 1]

fig, axes = plt.subplots(1, 2, figsize=(13, 7), facecolor=SURFACE)

ax = axes[0]
ax.set_facecolor(SURFACE)
ax.barh(y, summary["시계열상관_log1p"], color=ORANGE, height=0.6, zorder=3)
ax.axvline(0, color=GRIDLINE, linewidth=1)
ax.set_yticks(y)
ax.set_yticklabels(gu_order, fontsize=8, color=INK_SECONDARY)
ax.invert_yaxis()
ax.tick_params(axis="x", length=0, labelcolor=INK_MUTED)
for spine in ["top", "right"]:
    ax.spines[spine].set_visible(False)
ax.spines["left"].set_color(GRIDLINE)
ax.spines["bottom"].set_color(GRIDLINE)
ax.grid(axis="x", color=GRIDLINE, linewidth=0.6, zorder=0)
ax.set_xlabel("21개 분기 레벨(절대값) 상관, log1p", color=INK_SECONDARY, fontsize=9)
ax.set_title("(A) 레벨 상관 — 대부분 음수", color=INK_PRIMARY, fontsize=11.5, fontweight="bold")

ax = axes[1]
ax.set_facecolor(SURFACE)
ax.scatter(trans["상주인구_변화%"], trans["매출_변화%"], color=BLUE, s=32, alpha=0.6, zorder=3)
ax.axhline(0, color=GRIDLINE, linewidth=1)
ax.axvline(0, color=GRIDLINE, linewidth=1)
ax.tick_params(length=0, labelcolor=INK_MUTED)
for spine in ["top", "right"]:
    ax.spines[spine].set_visible(False)
ax.spines["left"].set_color(GRIDLINE)
ax.spines["bottom"].set_color(GRIDLINE)
ax.grid(color=GRIDLINE, linewidth=0.5, zorder=0)
ax.set_xlabel("상주인구 변화%(실제 값이 바뀐 시점만, 20224·20234)", color=INK_SECONDARY, fontsize=8.8)
ax.set_ylabel("같은 분기 매출 변화%", color=INK_SECONDARY, fontsize=9)
ax.set_title(f"(B) 실제 전환점 50개(25개 자치구 x 2시점) — 상관 {trans_corr:+.3f}",
             color=INK_PRIMARY, fontsize=11.5, fontweight="bold")

fig.text(0.03, 0.99, "상주인구 x 매출: 레벨 상관은 강하지만 실제 전환점에서는 상관이 거의 사라진다",
          color=INK_PRIMARY, fontsize=13.5, fontweight="bold", ha="left", va="top")
fig.text(0.03, 0.955, "(A)의 음의 상관은 두 지표가 각자 다른 방향으로 추세를 타서 생기는 허상(spurious) — 상주인구가 실제로 바뀌는 시점만 골라보면(B) 매출과 무관",
          color=INK_MUTED, fontsize=8.8, ha="left", va="top")
fig.tight_layout(rect=[0, 0, 1, 0.90])
out3 = os.path.join(FIG_DIR, "gu_resident_sales_corr_comparison.png")
fig.savefig(out3, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out3)
