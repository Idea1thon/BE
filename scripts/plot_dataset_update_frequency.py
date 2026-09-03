"""
23장 시각화 - v2. 단일 막대(전체 동일값 비율 하나로 압축)는 축 방향을 뒤집어도 "숫자 하나로는
판정이 안 된다"는 23-2의 핵심을 제대로 못 보여준다는 걸 확인했다(상권변화지표 라벨이 실제
갱신 비율 기준으로도 가장 짧은 막대가 되어 계단식들 사이에 섞여 보이는 문제).
그래서 데이터셋마다 "분기별 동일값 비율"을 작은 다중 차트(small multiples)로 그려
판정에 실제로 쓰인 패턴(특정 소수 분기만 뚝 떨어지고 나머지는 쭉 붙어있는 계단식 vs
전 분기 고르게 낮거나 고르게 sticky한 시계열)을 눈으로 바로 확인할 수 있게 한다.
"""
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams["font.family"] = "Apple SD Gothic Neo"
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output")
FIG_DIR = os.path.join(ROOT, "output", "figures")
os.makedirs(FIG_DIR, exist_ok=True)

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BLUE = "#2a78d6"
ORANGE = "#eb6834"

summary = pd.read_csv(os.path.join(OUT, "dataset_update_frequency_check.csv"), encoding="utf-8-sig")
by_q = pd.read_csv(os.path.join(OUT, "dataset_update_frequency_by_quarter.csv"), encoding="utf-8-sig")

# 정렬: 계단식 먼저(전체 동일값 비율 높은 순), 그다음 시계열(전체 동일값 비율 높은 순) --
# 그룹으로 딱 나눠서 보여주는 게 "숫자 하나로 줄세우기"보다 오해가 없다.
summary = summary.sort_values(["계단식_의심", "전체_동일값_비율"], ascending=[False, False])
order = summary["데이터셋"].tolist()

ncols = 3
nrows = -(-len(order) // ncols)
fig, axes = plt.subplots(nrows, ncols, figsize=(4.6 * ncols, 3.1 * nrows), facecolor=SURFACE)
axes = np.array(axes).reshape(nrows, ncols)

for i, name in enumerate(order):
    ax = axes[i // ncols, i % ncols]
    ax.set_facecolor(SURFACE)
    row = summary[summary["데이터셋"] == name].iloc[0]
    sub = by_q[by_q["데이터셋"] == name].sort_values("기준_년분기_코드")
    is_step = bool(row["계단식_의심"])
    color = ORANGE if is_step else BLUE
    x = np.arange(len(sub))
    ax.bar(x, sub["동일값_비율"] * 100, color=color, width=0.75, zorder=3)
    ax.axhline(10, color=GRIDLINE, linewidth=0.8, linestyle="--", zorder=1)
    ax.axhline(90, color=GRIDLINE, linewidth=0.8, linestyle="--", zorder=1)
    ax.set_ylim(0, 105)
    ax.set_xticks([])
    ax.tick_params(axis="y", labelsize=7, length=0, labelcolor=INK_MUTED)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color(GRIDLINE)
    ax.spines["bottom"].set_color(GRIDLINE)
    label = "계단식" if is_step else "시계열"
    ax.set_title(f"{name}\n[{label}] 전체 동일값 비율 {row['전체_동일값_비율'] * 100:.1f}%",
                 fontsize=8.8, color=INK_PRIMARY, fontweight="bold", pad=4)
    if row["전체_동일값_비율"] < 0.01:  # 막대가 전부 0이라 비어 보이므로 "빈 차트 아님"을 명시
        ax.text(0.5, 0.5, "막대 없음 = 동일값 0건\n(매분기 100% 새 값)", transform=ax.transAxes,
                ha="center", va="center", fontsize=8, color=INK_MUTED)

for j in range(len(order), nrows * ncols):
    axes[j // ncols, j % ncols].axis("off")

fig.text(0.02, 0.995, "데이터셋별 분기간 동일값 비율(막대 1개=분기 1개) — 계단식은 소수 분기만 뚝 떨어지고 나머지는 90%+ 고정",
          color=INK_PRIMARY, fontsize=13, fontweight="bold", ha="left", va="top")
fig.text(0.02, 0.975, "주황=계단식(관리상 갱신 정지, 트렌드 피처 금지) · 파랑=시계열(매분기 갱신, 트렌드 피처로 안전) · 점선=10%/90% 판정 기준선",
          color=INK_MUTED, fontsize=9, ha="left", va="top")
fig.tight_layout(rect=[0, 0, 1, 0.93])
out = os.path.join(FIG_DIR, "dataset_update_frequency_check.png")
fig.savefig(out, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out)
