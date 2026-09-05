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

summary = pd.read_csv(os.path.join(OUT, "gu_worker_sales_summary.csv"), encoding="utf-8-sig")
summary = summary.sort_values("추정매출_변화율%", ascending=False)
gu_order = summary["자치구"].tolist()
y = np.arange(len(gu_order))

worker = pd.read_csv(os.path.join(OUT, "gu_trend_직장인구.csv"), encoding="utf-8-sig", index_col=0)[gu_order]
sales = pd.read_csv(os.path.join(OUT, "gu_trend_추정매출_직장인구비교용.csv"), encoding="utf-8-sig", index_col=0)[gu_order]
trans = pd.read_csv(os.path.join(OUT, "gu_worker_sales_transition_points.csv"), encoding="utf-8-sig")
trans_corr = np.corrcoef(trans["직장인구_변화%"], trans["매출_변화%"])[0, 1]


# ---------- (1) 히트맵: 직장인구 vs 추정매출, 25개 자치구 x 21개 분기 (첫분기=100) ----------
def index100(df):
    return df.div(df.iloc[0]) * 100


fig, axes = plt.subplots(1, 2, figsize=(14, 9), facecolor=SURFACE)
for ax, name, df in zip(axes, ["직장인구", "추정매출"], [worker, sales]):
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

fig.text(0.02, 0.99, "25개 자치구: 직장인구 vs 추정매출 분기별 추세 (첫 분기=100 지수, 매출 성장률 순 정렬)",
          color=INK_PRIMARY, fontsize=13.5, fontweight="bold", ha="left", va="top")
fig.text(0.02, 0.965, "직장인구는 계단식 데이터(매년 4분기에 한 번만 갱신, 3개 분기는 직전 갱신값 유지) — 20244 이후 20261까지 5개 분기 그대로 고정",
          color=INK_MUTED, fontsize=9, ha="left", va="top")
fig.tight_layout(rect=[0, 0, 1, 0.93])
out1 = os.path.join(FIG_DIR, "gu_worker_sales_heatmap.png")
fig.savefig(out1, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out1)

# ---------- (2) 자치구별 변화율: 직장인구 vs 매출 ----------
fig, ax = plt.subplots(figsize=(10, 9), facecolor=SURFACE)
ax.set_facecolor(SURFACE)
ax.barh(y, summary["추정매출_변화율%"], color=BLUE, height=0.6, zorder=3, label="추정매출 변화율%")
ax.scatter(summary["직장인구_변화율%"], y, color=ORANGE, s=28, zorder=4, label="직장인구 변화율%")
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
fig.text(0.06, 0.985, "25개 자치구: 추정매출 변화율(막대) vs 직장인구 변화율(점) · 2021Q1~2026Q1 전체 기간",
          color=INK_PRIMARY, fontsize=13.5, fontweight="bold", ha="left", va="top")
fig.text(0.06, 0.965, "상주인구(21장)와 달리 직장인구는 자치구별로 증감이 갈린다(용산·송파·성동·영등포는 +19~23%, 동작·강북은 -9~-10%)",
          color=INK_MUTED, fontsize=8.8, ha="left", va="top")
ax.legend(frameon=False, fontsize=9, labelcolor=INK_SECONDARY, loc="lower right")
fig.tight_layout(rect=[0, 0, 1, 0.94])
out2 = os.path.join(FIG_DIR, "gu_worker_sales_change.png")
fig.savefig(out2, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out2)

# ---------- (3) pooled 레벨상관(진짜, 규모효과) vs 실제 전환점 상관(변화 동행성 없음) ----------
fig, axes = plt.subplots(1, 2, figsize=(13, 7), facecolor=SURFACE)

ax = axes[0]
ax.set_facecolor(SURFACE)
worker_gu_flat = worker.reset_index().melt(id_vars="기준_년분기_코드", var_name="자치구", value_name="직장인구")
sales_gu_flat = sales.reset_index().melt(id_vars="기준_년분기_코드", var_name="자치구", value_name="매출")
panel = worker_gu_flat.merge(sales_gu_flat, on=["기준_년분기_코드", "자치구"])
pooled_corr = np.corrcoef(np.log1p(panel["직장인구"]), np.log1p(panel["매출"]))[0, 1]
ax.scatter(panel["직장인구"], panel["매출"], color=BLUE, s=14, alpha=0.35, zorder=3)
ax.set_xscale("log")
ax.set_yscale("log")
ax.tick_params(length=0, labelcolor=INK_MUTED, labelsize=8)
for spine in ["top", "right"]:
    ax.spines[spine].set_visible(False)
ax.spines["left"].set_color(GRIDLINE)
ax.spines["bottom"].set_color(GRIDLINE)
ax.grid(color=GRIDLINE, linewidth=0.5, zorder=0)
ax.set_xlabel("직장인구(로그축)", color=INK_SECONDARY, fontsize=9)
ax.set_ylabel("매출(로그축)", color=INK_SECONDARY, fontsize=9)
ax.set_title(f"(A) pooled 규모 상관 — {pooled_corr:+.3f} (자치구x분기 {len(panel)}개)",
             color=INK_PRIMARY, fontsize=11.2, fontweight="bold")

ax = axes[1]
ax.set_facecolor(SURFACE)
ax.scatter(trans["직장인구_변화%"], trans["매출_변화%"], color=ORANGE, s=28, alpha=0.6, zorder=3)
ax.axhline(0, color=GRIDLINE, linewidth=1)
ax.axvline(0, color=GRIDLINE, linewidth=1)
ax.tick_params(length=0, labelcolor=INK_MUTED)
for spine in ["top", "right"]:
    ax.spines[spine].set_visible(False)
ax.spines["left"].set_color(GRIDLINE)
ax.spines["bottom"].set_color(GRIDLINE)
ax.grid(color=GRIDLINE, linewidth=0.5, zorder=0)
ax.set_xlabel("직장인구 변화%(실제 값이 바뀐 분기만, 매년 4분기)", color=INK_SECONDARY, fontsize=8.5)
ax.set_ylabel("같은 분기 매출 변화%", color=INK_SECONDARY, fontsize=9)
ax.set_title(f"(B) 실제 전환점 {len(trans)}개(25개 자치구 x 4시점) — 상관 {trans_corr:+.3f}",
             color=INK_PRIMARY, fontsize=11.2, fontweight="bold")

fig.text(0.03, 0.99, "직장인구 x 매출: 규모(pooled)로는 강하게 같이 움직이지만, 변화(전환점)로는 무관하다",
          color=INK_PRIMARY, fontsize=13.2, fontweight="bold", ha="left", va="top")
fig.text(0.03, 0.955, "(A) 직장인구가 많은 자치구일수록 매출도 큰 건 사실(둘 다 '상권 규모'의 대리지표) — (B) 하지만 직장인구가 느는 자치구라고 매출이 더 느는 건 아님",
          color=INK_MUTED, fontsize=8.6, ha="left", va="top")
fig.tight_layout(rect=[0, 0, 1, 0.90])
out3 = os.path.join(FIG_DIR, "gu_worker_sales_corr_comparison.png")
fig.savefig(out3, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out3)
