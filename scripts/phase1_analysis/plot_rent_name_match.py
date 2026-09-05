import os

import matplotlib.pyplot as plt

plt.rcParams["font.family"] = "Apple SD Gothic Neo"
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIG_DIR = os.path.join(ROOT, "output", "figures", "rent_match")
os.makedirs(FIG_DIR, exist_ok=True)

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"

# dataviz 스킬 참조 팔레트 — categorical 슬롯 1~5 (blue/orange/aqua/yellow/magenta)
CAT_BLUE = "#2a78d6"
CAT_ORANGE = "#eb6834"
CAT_AQUA = "#1baf7a"
CAT_YELLOW = "#eda100"
CAT_MAGENTA = "#e87ba4"

# docs/data_result_2.md § 10-2 — 68개 임대료 조사 지역, 1단계 이름 매칭 결과
# (실제 매칭 파이프라인은 상권_코드_명 후보만 쓰는 탐색 단계 기록)
labels = [
    "역세권형 부분 매칭\n(여러 후보로 흩어짐)",
    "상권명과 완전일치",
    "전혀 매칭 안 됨",
    "행정동명과 완전일치",
    "상권배후지명과 완전일치",
]
values = [43, 17, 5, 2, 1]
colors = [CAT_ORANGE, CAT_BLUE, CAT_MAGENTA, CAT_AQUA, CAT_YELLOW]

fig, ax = plt.subplots(figsize=(7.5, 6.5), facecolor=SURFACE)
ax.set_facecolor(SURFACE)

wedges, _, autotexts = ax.pie(
    values,
    colors=colors,
    startangle=90,
    counterclock=False,
    autopct=lambda p: f"{p:.1f}%" if p >= 3 else "",
    pctdistance=0.78,
    wedgeprops=dict(width=0.4, edgecolor=SURFACE, linewidth=2),
    textprops=dict(color="white", fontsize=10, fontweight="bold"),
)
ax.axis("equal")

ax.text(0, 0.06, "68", ha="center", va="center", fontsize=30, fontweight="bold", color=INK_PRIMARY)
ax.text(0, -0.12, "개 지역", ha="center", va="center", fontsize=10, color=INK_MUTED)

legend_labels = [f"{lab.replace(chr(10), ' ')}  —  {v}개 ({v/sum(values)*100:.1f}%)" for lab, v in zip(labels, values)]
ax.legend(
    wedges, legend_labels,
    loc="upper center", bbox_to_anchor=(0.5, 0.02), ncol=1,
    frameon=False, fontsize=9.5, labelcolor=INK_SECONDARY,
)

fig.text(0.5, 0.97, "임대료 외부 데이터 68개 지역 — 1단계 이름 매칭 결과",
          color=INK_PRIMARY, fontsize=13, fontweight="bold", ha="center", va="top")
fig.text(0.5, 0.925, "서울시 상권분석서비스 행정동/상권/상권배후지 명칭과의 텍스트 대조 (탐색 단계, data_result_2.md § 10-2)",
          color=INK_MUTED, fontsize=9, ha="center", va="top")

fig.tight_layout(rect=[0, 0.08, 1, 0.90])
out_path = os.path.join(FIG_DIR, "name_match_step1_pie.png")
fig.savefig(out_path, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out_path)
