import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams["font.family"] = "Apple SD Gothic Neo"
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "output", "scoring")
FIG_DIR = os.path.join(ROOT, "output", "figures", "scoring")
os.makedirs(FIG_DIR, exist_ok=True)

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
GREEN = "#1baf7a"
RED = "#e34948"

res = pd.read_csv(os.path.join(OUT, "cv_all_industries.csv"), encoding="utf-8-sig")
weights = pd.read_csv(os.path.join(OUT, "weights_all_industries.csv"), encoding="utf-8-sig")

PLAN_A = "A안(이미 잘되는 상권)"
PLAN_B = "B안(앞으로 유망한 상권)"
SET_ORDER = ["전체피처", "과거매출제외", "과거매출단독"]
SET_COLORS = {"전체피처": BLUE, "과거매출제외": GREEN, "과거매출단독": ORANGE}

# ---------- (1) 업종별 F1: A안 vs B안, 피처조합 3종 ----------
ind_order = (res[(res["정답기준"] == PLAN_B) & (res["피처조합"] == "전체피처")]
             .sort_values("F1_평균", ascending=False)["업종"].tolist())

fig, axes = plt.subplots(1, 2, figsize=(15, 6.5), facecolor=SURFACE, sharey=True)
for ax, plan in zip(axes, [PLAN_A, PLAN_B]):
    ax.set_facecolor(SURFACE)
    sub = res[res["정답기준"] == plan]
    x = np.arange(len(ind_order))
    width = 0.26
    for j, s in enumerate(SET_ORDER):
        vals = [sub[(sub["업종"] == g) & (sub["피처조합"] == s)]["F1_평균"].values[0] for g in ind_order]
        errs = [sub[(sub["업종"] == g) & (sub["피처조합"] == s)]["F1_표준편차"].values[0] for g in ind_order]
        ax.bar(x + (j - 1) * width, vals, width, yerr=errs, capsize=2, color=SET_COLORS[s],
               label=s, zorder=3, error_kw={"ecolor": INK_MUTED, "elinewidth": 0.8})
    ax.axhline(0.2, color=RED, linewidth=1.3, linestyle="--", zorder=4)
    # 라벨은 축 바깥 오른쪽에 둔다(막대와 겹치지 않도록)
    ax.text(1.005, 0.2, "랜덤\n0.20", color=RED, fontsize=8, ha="left", va="center",
            transform=ax.get_yaxis_transform())
    ax.set_xticks(x)
    ax.set_xticklabels(ind_order, rotation=40, ha="right", fontsize=8.5, color=INK_SECONDARY)
    ax.tick_params(axis="y", length=0, labelcolor=INK_MUTED)
    ax.tick_params(axis="x", length=0)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color(GRIDLINE)
    ax.spines["bottom"].set_color(GRIDLINE)
    ax.grid(axis="y", color=GRIDLINE, linewidth=0.6, zorder=0)
    ax.set_title(plan, color=INK_PRIMARY, fontsize=12.5, fontweight="bold")
axes[0].set_ylabel("F1 (k=폴드 정답수, 5-fold 평균±표준편차)", color=INK_SECONDARY, fontsize=9.5)
axes[0].legend(frameon=False, fontsize=9, labelcolor=INK_SECONDARY, loc="lower left")

fig.text(0.02, 0.99, "스코어링 검증: 업종별 F1 (정답기준 2종 x 피처조합 3종)",
          color=INK_PRIMARY, fontsize=14, fontweight="bold", ha="left", va="top")
fig.text(0.02, 0.955, "A안은 '과거매출단독'만으로 F1 0.93 -- 구조 피처가 기여하지 않는 사실상 동어반복 과제. "
                       "B안은 전 업종에서 F1이 0.4 미만으로 예측이 훨씬 어렵다.",
          color=INK_MUTED, fontsize=9, ha="left", va="top")
fig.tight_layout(rect=[0, 0, 1, 0.93])
out1 = os.path.join(FIG_DIR, "scoring_f1_by_industry.png")
fig.savefig(out1, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out1)

# ---------- (2) 요약: 정답기준 x 피처조합 평균 F1 + lift ----------
summ = res.groupby(["정답기준", "피처조합"])[["F1_평균", "lift"]].mean().reset_index()
fig, axes = plt.subplots(1, 2, figsize=(13, 5.2), facecolor=SURFACE)
for ax, (metric, title, ref, reflabel) in zip(
        axes, [("F1_평균", "평균 F1 (10개 업종)", 0.2, "랜덤 0.20"),
                ("lift", "평균 lift (랜덤 대비 몇 배)", 1.0, "랜덤 1.0배")]):
    ax.set_facecolor(SURFACE)
    x = np.arange(2)
    width = 0.26
    for j, s in enumerate(SET_ORDER):
        vals = [summ[(summ["정답기준"] == p) & (summ["피처조합"] == s)][metric].values[0]
                for p in [PLAN_A, PLAN_B]]
        bars = ax.bar(x + (j - 1) * width, vals, width, color=SET_COLORS[s], label=s, zorder=3)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + (0.015 if metric == "F1_평균" else 0.07),
                    f"{v:.3f}" if metric == "F1_평균" else f"{v:.2f}x",
                    ha="center", fontsize=8, color=INK_SECONDARY)
    ax.axhline(ref, color=RED, linewidth=1.3, linestyle="--", zorder=4)
    ax.text(1.005, ref, reflabel.replace(" ", "\n"), color=RED, fontsize=8.5,
            ha="left", va="center", transform=ax.get_yaxis_transform())
    ax.set_xticks(x)
    ax.set_xticklabels(["A안\n(이미 잘되는 상권)", "B안\n(앞으로 유망한 상권)"],
                        fontsize=10, color=INK_SECONDARY)
    ax.tick_params(length=0, labelcolor=INK_MUTED)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color(GRIDLINE)
    ax.spines["bottom"].set_color(GRIDLINE)
    ax.grid(axis="y", color=GRIDLINE, linewidth=0.6, zorder=0)
    ax.set_title(title, color=INK_PRIMARY, fontsize=12, fontweight="bold")
axes[0].legend(frameon=False, fontsize=9, labelcolor=INK_SECONDARY, loc="upper right")

fig.text(0.03, 0.99, "스코어링 최종 결론: 무엇을 맞히려 하느냐에 따라 결과가 완전히 갈린다",
          color=INK_PRIMARY, fontsize=13.5, fontweight="bold", ha="left", va="top")
fig.text(0.03, 0.945, "A안=이미 매출 큰 곳 맞히기(쉽지만 정보 없음) · B안=앞으로 더 클 곳 맞히기(어렵고 lift 1.5배에 그침)",
          color=INK_MUTED, fontsize=9, ha="left", va="top")
fig.tight_layout(rect=[0, 0, 1, 0.90])
out2 = os.path.join(FIG_DIR, "scoring_summary.png")
fig.savefig(out2, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out2)

# ---------- (3) B안 피처 가중치의 업종 간 불안정성 ----------
wb = weights[weights["정답기준"] == PLAN_B]
agg = wb.groupby("피처")["표준화계수"].agg(["mean", "std"]).sort_values("mean", key=abs, ascending=False)
fig, ax = plt.subplots(figsize=(9.5, 5.5), facecolor=SURFACE)
ax.set_facecolor(SURFACE)
y = np.arange(len(agg))
ax.barh(y, agg["mean"], xerr=agg["std"], color=BLUE, height=0.6, zorder=3,
        error_kw={"ecolor": ORANGE, "elinewidth": 1.4, "capsize": 3})
ax.axvline(0, color=GRIDLINE, linewidth=1)
ax.set_yticks(y)
ax.set_yticklabels(agg.index, fontsize=9.5, color=INK_SECONDARY)
ax.invert_yaxis()
ax.tick_params(axis="x", length=0, labelcolor=INK_MUTED)
for spine in ["top", "right"]:
    ax.spines[spine].set_visible(False)
ax.spines["left"].set_color(GRIDLINE)
ax.spines["bottom"].set_color(GRIDLINE)
ax.grid(axis="x", color=GRIDLINE, linewidth=0.6, zorder=0)
ax.set_xlabel("B안 표준화 회귀계수 — 막대=10개 업종 평균, 주황 오차막대=업종 간 표준편차",
              color=INK_SECONDARY, fontsize=9)
fig.text(0.04, 0.985, "B안 가중치는 업종마다 부호까지 뒤집힐 만큼 불안정하다",
          color=INK_PRIMARY, fontsize=13.5, fontweight="bold", ha="left", va="top")
fig.text(0.04, 0.945, "표준편차가 평균의 2배 이상 — 과거매출·전체_점포_수의 다중공선성 + 초과변화율 분모에 과거매출이 들어가는 구조 탓. "
                       "고정 가중치 공식을 그대로 쓰면 안 된다는 근거.",
          color=INK_MUTED, fontsize=8.5, ha="left", va="top")
fig.tight_layout(rect=[0, 0, 1, 0.90])
out3 = os.path.join(FIG_DIR, "scoring_weights_instability.png")
fig.savefig(out3, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out3)
