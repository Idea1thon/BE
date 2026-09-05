"""
16장까지 개별적으로 계산한 임대료 x {점포, 매출, 유동인구, 상권변화지표} 상관관계를
전부 하나의 패널로 엮어, 겹치는 63개 임대료 지역·공통분기 2021Q1~2025Q4(20개 분기)에서
5개 데이터셋이 전반적으로 어떻게 함께 움직이는지(시계열 추세 + 통합 다변량 관계)를 본다.

기존에 이미 만들어둔 상권 단위 패널(output/rent_store_panel.csv, rent_sales_panel.csv,
rent_flow_sg_panel.csv, rent_change_sg_panel.csv)을 재사용해 병합한다 — 전부 같은 63개
지역·같은 20개 공통분기·같은 임대료 소스에서 나온 값이라 그대로 머지 가능.

두 갈래로 분석한다:
1. 분기별 추세: 63개 지역 평균을 분기별로 집계 -> 서로 스케일이 다른 5개 지표를
   같은 기준(첫 분기=100 인덱스)으로 정규화해 한 그래프에 겹쳐서 "전체적으로 함께
   움직이는지"를 본다. 정규화 전/후 분기별 평균 자체도 CSV로 남긴다.
2. 통합 다변량 회귀: seoul_multivariate_analysis.py와 같은 표준화(z-score) 회귀 방식을
   재사용해, 이번엔 임대료를 새 피처로 추가한 상태에서 업종별 매출을 설명 -- 임대료가
   기존에 확인된 피처(점포수·유동인구·상권변화지표) 대비 상대적으로 얼마나 중요한지 확인.
"""
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

plt.rcParams["font.family"] = "Apple SD Gothic Neo"
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "output")
FIG_DIR = os.path.join(ROOT, "output", "figures", "rent_combined")
os.makedirs(FIG_DIR, exist_ok=True)

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
DIVERGING = LinearSegmentedColormap.from_list("diverging_blue_red", ["#e34948", "#f0efec", "#2a78d6"])
CAT5 = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#8a5fd6"]  # 임대료/점포/매출/유동인구/gap_survive

QUADRANT_ORDER = ["LL", "LH", "HL", "HH"]
QUADRANT_LABEL = {"LL": "다이나믹(LL)", "LH": "상권확장(LH)", "HL": "상권축소(HL)", "HH": "정체(HH)"}
CAT4 = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]

# ---------- 1. 5개 기존 패널 병합 ----------
store = pd.read_csv(os.path.join(OUT, "rent_store_panel.csv"))
sales = pd.read_csv(os.path.join(OUT, "rent_sales_panel.csv"))
flow = pd.read_csv(os.path.join(OUT, "rent_flow_sg_panel.csv"))
change = pd.read_csv(os.path.join(OUT, "rent_change_sg_panel.csv"))

key_biz = ["임대료_지역", "기준_년분기_코드", "서비스_업종_코드", "서비스_업종_코드_명"]
key_region_q = ["임대료_지역", "기준_년분기_코드"]
rent_cols = ["임대료", "공실률", "투자수익률", "소득수익률", "자본수익률"]

panel = store.merge(
    sales[key_biz + ["당월_매출_금액", "당월_매출_건수", "건당_매출액"]], on=key_biz, how="inner"
)
panel = panel.merge(flow[key_region_q + ["총_유동인구_수"]], on=key_region_q, how="left")
panel = panel.merge(change[key_region_q + ["gap_survive", "gap_close", "사분면"]], on=key_region_q, how="left")
panel = panel.dropna(subset=["총_유동인구_수", "gap_survive"])

panel_csv = os.path.join(OUT, "rent_combined_panel.csv")
panel.to_csv(panel_csv, index=False, encoding="utf-8-sig")
print(f"[1] 통합 패널: {panel.shape[0]}행 (지역{panel['임대료_지역'].nunique()}x분기{panel['기준_년분기_코드'].nunique()}x업종{panel['서비스_업종_코드'].nunique()}) -> {panel_csv}")

# ---------- 2. 분기별 추세 (63개 지역 평균, 정규화) ----------
quarterly = panel.groupby("기준_년분기_코드").agg(
    임대료=("임대료", "mean"),
    전체_점포_수=("전체_점포_수", "mean"),
    당월_매출_금액=("당월_매출_금액", "mean"),
    총_유동인구_수=("총_유동인구_수", "mean"),
    gap_survive=("gap_survive", "mean"),
).reset_index().sort_values("기준_년분기_코드")

trend_csv = os.path.join(OUT, "rent_combined_quarterly_trend.csv")
quarterly.to_csv(trend_csv, index=False, encoding="utf-8-sig")
print(f"[2] 분기별 평균(원값): {quarterly.shape[0]}행 -> {trend_csv}")

series_cols = ["임대료", "전체_점포_수", "당월_매출_금액", "총_유동인구_수", "gap_survive"]
indexed = quarterly.copy()
for c in series_cols:
    base = indexed[c].iloc[0]
    if c == "gap_survive":
        # gap_survive는 0 근처를 오가는 차이값이라 index=100 정규화가 아니라 그대로(원값) 둔다 -
        # 대신 별도 축(우측)에 그린다. indexed엔 그대로 보존, 플롯에서 다르게 처리.
        indexed[c] = quarterly[c]
    else:
        indexed[c] = quarterly[c] / base * 100

indexed_csv = os.path.join(OUT, "rent_combined_quarterly_trend_indexed.csv")
indexed.to_csv(indexed_csv, index=False, encoding="utf-8-sig")
print(f"[2] 분기별 지수화(2021Q1=100): -> {indexed_csv}")

# 분기별 집계 시계열 간 상관관계 (n=20, 지역x분기 cross-section이 아니라 분기 단위 aggregate 비교)
agg_corr = quarterly[series_cols].corr()
agg_corr_csv = os.path.join(OUT, "rent_combined_quarterly_correlation.csv")
agg_corr.to_csv(agg_corr_csv, encoding="utf-8-sig")
print(f"[3] 분기별 집계 시계열 간 상관계수(n={len(quarterly)}개 분기):")
print(agg_corr.round(3).to_string())
print(f"-> {agg_corr_csv}")

# ---------- 3. 사분면 비중 추세 (63개 지역 한정) ----------
quad_share = (
    panel.drop_duplicates(subset=key_region_q)
    .groupby("기준_년분기_코드")["사분면"]
    .value_counts(normalize=True)
    .unstack(fill_value=0)
    .reindex(columns=QUADRANT_ORDER, fill_value=0)
    .reset_index()
    .sort_values("기준_년분기_코드")
)
quad_share_csv = os.path.join(OUT, "rent_combined_quadrant_share_trend.csv")
quad_share.to_csv(quad_share_csv, index=False, encoding="utf-8-sig")
print(f"\n[4] 사분면 비중 추세: -> {quad_share_csv}")
print(quad_share.round(3).to_string(index=False))

# ---------- 4. 통합 다변량 회귀 (임대료를 새 피처로 추가) ----------
FEATURES = ["임대료", "전체_점포_수", "총_유동인구_수", "gap_survive", "gap_close"]
LOG_FEATURES = {"임대료", "전체_점포_수", "총_유동인구_수"}
FOOD_CODES = sorted(panel["서비스_업종_코드_명"].unique())

log_panel = panel.copy()
for c in LOG_FEATURES:
    log_panel[c] = np.log1p(log_panel[c].clip(lower=0))
log_panel["당월_매출_금액"] = np.log1p(log_panel["당월_매출_금액"].clip(lower=0))


def standardized_ols(X, y):
    Xz = (X - X.mean(axis=0)) / X.std(axis=0, ddof=0)
    yz = (y - y.mean()) / y.std(ddof=0)
    design = np.column_stack([np.ones(len(Xz)), Xz])
    coef, _, _, _ = np.linalg.lstsq(design, yz, rcond=None)
    y_pred = design @ coef
    ss_res = np.sum((yz - y_pred) ** 2)
    ss_tot = np.sum((yz - yz.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot
    return coef[1:], r2


coef_rows = {}
r2_rows = []
for biz in FOOD_CODES:
    sub = log_panel[log_panel["서비스_업종_코드_명"] == biz].dropna(subset=FEATURES + ["당월_매출_금액"])
    X = sub[FEATURES].values
    y = sub["당월_매출_금액"].values
    coefs, r2 = standardized_ols(X, y)
    coef_rows[biz] = dict(zip(FEATURES, coefs))
    single_r2 = max(sub[f].corr(sub["당월_매출_금액"]) ** 2 for f in FEATURES)
    r2_rows.append({"업종": biz, "다변량_R2": r2, "최고단일지표_R2": single_r2, "n": len(sub)})

coef_df = pd.DataFrame(coef_rows).T[FEATURES]
coef_csv = os.path.join(OUT, "rent_combined_multivariate_coef.csv")
coef_df.to_csv(coef_csv, encoding="utf-8-sig")
print(f"\n[5] 임대료 포함 표준화 다변량 회귀계수 (겹치는 63개 지역):")
print(coef_df.round(3).to_string())
print(f"-> {coef_csv}")

r2_df = pd.DataFrame(r2_rows).set_index("업종")
r2_csv = os.path.join(OUT, "rent_combined_multivariate_r2.csv")
r2_df.to_csv(r2_csv, encoding="utf-8-sig")
print(f"\n[5] R^2 비교:")
print(r2_df.round(3).to_string())
print(f"-> {r2_csv}")

# ========== 시각화 ==========

# (a) 분기별 정규화 추세선
fig, ax1 = plt.subplots(figsize=(10, 5.5), facecolor=SURFACE)
ax1.set_facecolor(SURFACE)
x = range(len(indexed))
labels = [str(q) for q in indexed["기준_년분기_코드"]]

for c, color in zip(["임대료", "전체_점포_수", "당월_매출_금액", "총_유동인구_수"], CAT5[:4]):
    ax1.plot(x, indexed[c], color=color, linewidth=2, marker="o", markersize=3, label=c)
ax1.axhline(100, color=GRIDLINE, linewidth=1, zorder=0)
ax1.set_ylabel("지수 (2021Q1=100)", color=INK_SECONDARY, fontsize=9.5)
ax1.set_xticks(x[::2])
ax1.set_xticklabels(labels[::2], rotation=45, ha="right", color=INK_MUTED, fontsize=8)
ax1.tick_params(axis="y", length=0, labelcolor=INK_MUTED)
for spine in ["top", "right"]:
    ax1.spines[spine].set_visible(False)
ax1.spines["left"].set_color(GRIDLINE)
ax1.spines["bottom"].set_color(GRIDLINE)
ax1.grid(axis="y", color=GRIDLINE, linewidth=0.6, zorder=0)

ax2 = ax1.twinx()
ax2.plot(x, indexed["gap_survive"], color=CAT5[4], linewidth=2, linestyle="--", marker="s", markersize=3,
          label="gap_survive(우축, 원값)")
ax2.axhline(0, color=CAT5[4], linewidth=0.6, alpha=0.4)
ax2.set_ylabel("gap_survive (개월, 지역-서울)", color=CAT5[4], fontsize=9.5)
ax2.tick_params(axis="y", labelcolor=CAT5[4], length=0)
ax2.spines["right"].set_color(CAT5[4])
ax2.spines["top"].set_visible(False)

fig.text(0.08, 0.97, "임대료·점포·매출·유동인구·상권변화지표 분기별 추세 (겹치는 63개 지역 평균)",
          color=INK_PRIMARY, fontsize=13, fontweight="bold", ha="left", va="top")
fig.text(0.08, 0.925, "2021Q1~2025Q4(20개 분기) · 임대료/점포/매출/유동인구는 2021Q1=100 지수, gap_survive는 원값(우축)",
          color=INK_MUTED, fontsize=9, ha="left", va="top")

lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
fig.legend(lines1 + lines2, labels1 + labels2, frameon=False, fontsize=8.5, ncol=5,
           labelcolor=INK_SECONDARY, loc="lower center", bbox_to_anchor=(0.5, -0.02))

fig.tight_layout(rect=[0, 0.09, 1, 0.88])
out_a = os.path.join(FIG_DIR, "rent_combined_quarterly_trend.png")
fig.savefig(out_a, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out_a)

# (b) 분기별 집계 상관 히트맵
fig, ax = plt.subplots(figsize=(6.5, 5.5), facecolor=SURFACE)
ax.set_facecolor(SURFACE)
im = ax.imshow(agg_corr.values, cmap=DIVERGING, vmin=-1, vmax=1, aspect="auto")
ax.set_xticks(range(len(agg_corr.columns)))
ax.set_xticklabels(agg_corr.columns, rotation=30, ha="right", color=INK_SECONDARY, fontsize=9)
ax.set_yticks(range(len(agg_corr.index)))
ax.set_yticklabels(agg_corr.index, color=INK_SECONDARY, fontsize=9)
ax.tick_params(length=0)
for spine in ax.spines.values():
    spine.set_visible(False)
for i in range(agg_corr.shape[0]):
    for j in range(agg_corr.shape[1]):
        v = agg_corr.values[i, j]
        ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                 color=SURFACE if abs(v) > 0.6 else INK_PRIMARY, fontsize=9.5)
fig.text(0.1, 0.97, "5개 지표의 분기별 집계 시계열 간 상관관계", color=INK_PRIMARY, fontsize=13,
          fontweight="bold", ha="left", va="top")
fig.text(0.1, 0.90, f"63개 지역 평균을 분기별로 집계(n=20개 분기) · Pearson r", color=INK_MUTED, fontsize=9,
          ha="left", va="top")
cbar = fig.colorbar(im, ax=ax, fraction=0.05, pad=0.03)
cbar.outline.set_visible(False)
cbar.ax.tick_params(length=0, labelsize=8, labelcolor=INK_MUTED)
fig.tight_layout(rect=[0, 0, 1, 0.86])
out_b = os.path.join(FIG_DIR, "rent_combined_quarterly_correlation.png")
fig.savefig(out_b, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out_b)

# (c) 사분면 비중 추세 (누적 영역)
fig, ax = plt.subplots(figsize=(10, 5), facecolor=SURFACE)
ax.set_facecolor(SURFACE)
x = range(len(quad_share))
bottom = np.zeros(len(quad_share))
for q, color in zip(QUADRANT_ORDER, CAT4):
    vals = quad_share[q].values
    ax.bar(x, vals, bottom=bottom, color=color, label=QUADRANT_LABEL[q], width=0.75, zorder=3)
    bottom += vals
ax.set_xticks(list(x)[::2])
ax.set_xticklabels([str(q) for q in quad_share["기준_년분기_코드"]][::2], rotation=45, ha="right",
                     color=INK_MUTED, fontsize=8)
ax.set_ylabel("지역 비중", color=INK_SECONDARY, fontsize=9.5)
ax.tick_params(axis="y", length=0, labelcolor=INK_MUTED)
for spine in ["top", "right"]:
    ax.spines[spine].set_visible(False)
ax.spines["left"].set_color(GRIDLINE)
ax.spines["bottom"].set_color(GRIDLINE)
ax.set_ylim(0, 1)
fig.text(0.08, 0.97, "상권변화지표 사분면 비중 추세 (겹치는 63개 지역 한정, 상권 단위)",
          color=INK_PRIMARY, fontsize=13, fontweight="bold", ha="left", va="top")
fig.text(0.08, 0.90, "2021Q1~2025Q4, 지역x분기 관측치 기준 · 사분면은 가중평균 gap 부호로 재판정",
          color=INK_MUTED, fontsize=9, ha="left", va="top")
ax.legend(frameon=False, fontsize=9, ncol=4, labelcolor=INK_SECONDARY, loc="lower center",
           bbox_to_anchor=(0.5, -0.28))
fig.tight_layout(rect=[0, 0.08, 1, 0.86])
out_c = os.path.join(FIG_DIR, "rent_combined_quadrant_share_trend.png")
fig.savefig(out_c, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out_c)

# (d) 통합 다변량 회귀계수 히트맵
fig, ax = plt.subplots(figsize=(8, 6), facecolor=SURFACE)
ax.set_facecolor(SURFACE)
im = ax.imshow(coef_df.values, cmap=DIVERGING, vmin=-0.6, vmax=0.6, aspect="auto")
ax.set_xticks(range(len(coef_df.columns)))
ax.set_xticklabels(coef_df.columns, rotation=30, ha="right", color=INK_SECONDARY, fontsize=9)
ax.set_yticks(range(len(coef_df.index)))
ax.set_yticklabels(coef_df.index, color=INK_SECONDARY, fontsize=9)
ax.tick_params(length=0)
for spine in ax.spines.values():
    spine.set_visible(False)
for i in range(coef_df.shape[0]):
    for j in range(coef_df.shape[1]):
        v = coef_df.values[i, j]
        ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                 color=SURFACE if abs(v) > 0.42 else INK_PRIMARY, fontsize=9)
fig.text(0.08, 0.97, "임대료 포함 업종별 표준화 다변량 회귀계수 (겹치는 63개 지역)",
          color=INK_PRIMARY, fontsize=13, fontweight="bold", ha="left", va="top")
fig.text(0.08, 0.91, "log1p 변환, 2021Q1~2025Q4 패널 · 매출_금액 설명", color=INK_MUTED, fontsize=9,
          ha="left", va="top")
cbar = fig.colorbar(im, ax=ax, fraction=0.05, pad=0.03)
cbar.outline.set_visible(False)
cbar.ax.tick_params(length=0, labelsize=8, labelcolor=INK_MUTED)
fig.tight_layout(rect=[0, 0, 1, 0.87])
out_d = os.path.join(FIG_DIR, "rent_combined_multivariate_coef.png")
fig.savefig(out_d, dpi=150, facecolor=SURFACE)
plt.close(fig)
print("saved:", out_d)

print("\ndone")
