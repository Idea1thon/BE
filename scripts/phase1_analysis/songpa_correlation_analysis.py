"""송파구 상권 대상: 유동인구/상주인구/직장인구 vs 업종별(10개 독립 코드) 매출 상관관계 분석.

방법 메모(가정):
- 상권 멤버십은 라벨이 아니라 output/songpa/songpa_trdar_membership.csv(겹침 크로스워크 기반)를 사용.
- 매출/인구 모두 우측으로 크게 치우친 분포(소수 대형 상권이 극단값)라, 원자료 그대로 피어슨 상관계수를
  계산하면 그 소수 상권에 좌우된다. log1p 변환 후 상관계수를 계산해 전반적 경향을 본다.
- 업종에 매출이 아예 없는(=그 업종 점포가 없는) 상권은 0으로 채운다 (결측이 아니라 "그 업종 수요가
  0으로 관측됨"으로 취급).
"""
import csv
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

plt.rcParams["font.family"] = "Apple SD Gothic Neo"
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
SONGPA_DIR = os.path.join(ROOT, "output", "songpa")
FIG_DIR = os.path.join(ROOT, "output", "figures", "songpa")
os.makedirs(FIG_DIR, exist_ok=True)

QUARTER = 20261

INDUSTRY_CODES = {
    "CS100001": "한식음식점", "CS100002": "중식음식점", "CS100003": "일식음식점",
    "CS100004": "양식음식점", "CS100005": "제과점", "CS100006": "패스트푸드점",
    "CS100007": "치킨전문점", "CS100008": "분식전문점", "CS100009": "호프-간이주점",
    "CS100010": "커피-음료",
}
POP_COLS = ["유동인구", "상주인구", "직장인구"]

# dataviz 스킬 참조 팔레트
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
SURFACE = "#fcfcfb"
GRIDLINE = "#e1e0d9"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
RED = "#e34948"
GRAY_MID = "#f0efec"
DIVERGING_CMAP = LinearSegmentedColormap.from_list("diverging_blue_red", [RED, GRAY_MID, BLUE])


def load_songpa_trdar_codes():
    path = os.path.join(SONGPA_DIR, "songpa_trdar_membership.csv")
    with open(path, encoding="utf-8-sig") as f:
        return {int(row["TRDAR_CD"]) for row in csv.DictReader(f)}


def load_sales(trdar_codes):
    # data/추정매출/이 연도별 폴더(2021~2026)로 재구성됨 — QUARTER(20261)는 '2026' 폴더 파일에 있음
    path = os.path.join(DATA, "추정매출", "2026", "서울시 상권분석서비스(추정매출-상권).csv")
    df = pd.read_csv(path, encoding="cp949")
    df = df[
        (df["기준_년분기_코드"] == QUARTER)
        & df["상권_코드"].isin(trdar_codes)
        & df["서비스_업종_코드"].isin(INDUSTRY_CODES)
    ]
    pivot = df.pivot_table(index="상권_코드", columns="서비스_업종_코드", values="당월_매출_금액", aggfunc="sum")
    pivot = pivot.reindex(columns=list(INDUSTRY_CODES.keys()))
    pivot = pivot.rename(columns=INDUSTRY_CODES)
    return pivot


def load_population(trdar_codes):
    def load_one(folder, fname, col):
        path = os.path.join(DATA, folder, fname)
        df = pd.read_csv(path, encoding="cp949")
        df = df[(df["기준_년분기_코드"] == QUARTER) & df["상권_코드"].isin(trdar_codes)]
        return df.set_index("상권_코드")[col]

    flow = load_one("길단위인구", "서울시 상권분석서비스(길단위인구-상권).csv", "총_유동인구_수")
    resident = load_one("상주인구", "서울시 상권분석서비스(상주인구-상권).csv", "총_상주인구_수")
    worker = load_one("직장인구", "서울시 상권분석서비스(직장인구-상권).csv", "총_직장_인구_수")
    return pd.DataFrame({"유동인구": flow, "상주인구": resident, "직장인구": worker})


def plot_heatmap(corr):
    fig, ax = plt.subplots(figsize=(10, 4.2))
    im = ax.imshow(corr.values, cmap=DIVERGING_CMAP, vmin=-1, vmax=1, aspect="auto")

    ax.set_xticks(range(len(corr.columns)))
    ax.set_xticklabels(corr.columns, rotation=40, ha="right", color=INK_SECONDARY, fontsize=10)
    ax.set_yticks(range(len(corr.index)))
    ax.set_yticklabels(corr.index, color=INK_SECONDARY, fontsize=10)

    for i in range(corr.shape[0]):
        for j in range(corr.shape[1]):
            v = corr.values[i, j]
            text_color = "white" if abs(v) > 0.55 else INK_PRIMARY
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", color=text_color, fontsize=9)

    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title("송파구 상권: 인구지표 × 업종별 매출 상관계수 (log1p, Pearson)", color=INK_PRIMARY, fontsize=13, pad=14)
    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.ax.tick_params(colors=INK_MUTED)
    cbar.outline.set_visible(False)

    fig.tight_layout()
    out = os.path.join(FIG_DIR, "songpa_population_sales_corr_heatmap.png")
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out} 저장")


def scatter_panel(ax, x, y, xlabel, ylabel, corr_value):
    ax.set_facecolor(SURFACE)
    ax.scatter(x, y, color=BLUE, alpha=0.7, s=32, edgecolor=SURFACE, linewidth=0.6, zorder=2)

    if len(x) >= 2 and x.std() > 0:
        slope, intercept = np.polyfit(x, y, 1)
        xs = np.linspace(x.min(), x.max(), 50)
        ax.plot(xs, slope * xs + intercept, color=ORANGE, linewidth=2, zorder=1)

    ax.set_xlabel(xlabel, color=INK_SECONDARY, fontsize=9)
    ax.set_ylabel(ylabel, color=INK_SECONDARY, fontsize=9)
    ax.set_title(f"r = {corr_value:.2f}", color=INK_PRIMARY, fontsize=11)
    ax.tick_params(colors=INK_MUTED, labelsize=8)
    ax.grid(color=GRIDLINE, linewidth=1)
    ax.set_axisbelow(True)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color(GRIDLINE)


def plot_scatter_examples(log_df, corr):
    pairs = [
        ("직장인구", "커피-음료"),
        ("직장인구", "호프-간이주점"),
        ("상주인구", "한식음식점"),
        ("유동인구", "치킨전문점"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(10, 9))
    for ax, (pop_col, biz_col) in zip(axes.flat, pairs):
        scatter_panel(
            ax, log_df[pop_col], log_df[biz_col],
            xlabel=f"log(1+{pop_col})", ylabel=f"log(1+{biz_col} 매출)",
            corr_value=corr.loc[pop_col, biz_col],
        )
    fig.suptitle("송파구 상권: 인구지표 vs 업종별 매출 산점도 (예시 4쌍)", color=INK_PRIMARY, fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    out = os.path.join(FIG_DIR, "songpa_population_sales_scatter_examples.png")
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out} 저장")


def main():
    trdar_codes = load_songpa_trdar_codes()
    sales = load_sales(trdar_codes)
    pop = load_population(trdar_codes)

    merged = pop.join(sales, how="outer").fillna(0)
    print(f"분석 대상 송파구 상권 수: {len(merged)}개 (인구 또는 매출 데이터가 1개 이상 존재)")

    log_df = np.log1p(merged)
    corr_full = log_df.corr(method="pearson")
    corr = corr_full.loc[POP_COLS, list(INDUSTRY_CODES.values())]

    corr_path = os.path.join(SONGPA_DIR, "songpa_population_sales_correlation.csv")
    corr.to_csv(corr_path, encoding="utf-8-sig")
    print(f"{corr_path} 저장")
    print("\n=== 상관계수 (log1p, Pearson) ===")
    print(corr.round(2).to_string())

    plot_heatmap(corr)
    plot_scatter_examples(log_df, corr)


if __name__ == "__main__":
    main()
