"""송파구 상권: 아파트 데이터(소득 수준 대리지표) x 업종별 추정매출 상관관계.

방법(가정):
- 아파트-상권 CSV의 `아파트_평균_시가`(단지 평균 시가, 원)를 그 상권 배후 거주지의
  소득 수준 대리지표로 사용한다. 실제 소득 데이터가 없으므로 부동산 자산 가치를
  소득의 근사치로 쓰는 것 — 자산과 소득이 항상 비례하진 않는다는 한계는 있음.
- 상권마다 이 값으로 송파구 내 분위(quartile)를 매겨 "소득분위"를 구성한다(송파구
  71개 상권 표본 내 상대적 순위이며, 서울 전체 기준 분위가 아님에 주의).
- 매출/아파트값 모두 우측으로 치우친 분포라 log1p 변환 후 Pearson 상관계수 사용
  (앞선 인구지표 분석과 동일한 방법론).
"""
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
    df = pd.read_csv(os.path.join(SONGPA_DIR, "songpa_trdar_membership.csv"), encoding="utf-8-sig")
    return set(df["TRDAR_CD"].astype(int))


def load_apartment(trdar_codes):
    path = os.path.join(DATA, "아파트", "서울시 상권분석서비스(아파트-상권).csv")
    df = pd.read_csv(path, encoding="cp949")
    df = df[(df["기준_년분기_코드"] == QUARTER) & df["상권_코드"].isin(trdar_codes)].copy()
    return df.set_index("상권_코드")[["상권_코드_명", "아파트_단지_수", "아파트_평균_면적", "아파트_평균_시가"]]


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
    pivot = pivot.reindex(columns=list(INDUSTRY_CODES.keys())).rename(columns=INDUSTRY_CODES)
    return pivot


def sanity_check_avg_price(apt):
    """아파트_평균_시가 필드가 가격 구간별 세대수와 대략 일치하는지 정성적으로 확인."""
    print("아파트_평균_시가 기술통계 (원):")
    print(apt["아파트_평균_시가"].describe().apply(lambda v: f"{v:,.0f}"))
    print()


def main():
    trdar_codes = load_songpa_trdar_codes()
    apt = load_apartment(trdar_codes)
    sales = load_sales(trdar_codes)

    merged = apt.join(sales, how="inner").dropna(subset=["아파트_평균_시가"])
    print(f"분석 대상 송파구 상권 수: {len(merged)}개 (아파트 데이터 있는 상권만)\n")
    sanity_check_avg_price(merged)

    merged["소득분위"] = pd.qcut(merged["아파트_평균_시가"], 4, labels=["1분위(하위)", "2분위", "3분위", "4분위(상위)"])

    out_csv = os.path.join(SONGPA_DIR, "songpa_apartment_sales_merged.csv")
    merged.to_csv(out_csv, encoding="utf-8-sig")
    print(f"{out_csv} 저장\n")

    log_df = merged.copy()
    for col in ["아파트_평균_시가", "아파트_평균_면적"] + list(INDUSTRY_CODES.values()):
        log_df[col] = np.log1p(log_df[col].fillna(0))

    corr = pd.DataFrame({
        biz: {
            "아파트_평균_시가": log_df["아파트_평균_시가"].corr(log_df[biz]),
            "아파트_평균_면적": log_df["아파트_평균_면적"].corr(log_df[biz]),
        }
        for biz in INDUSTRY_CODES.values()
    }).T

    corr_csv = os.path.join(SONGPA_DIR, "songpa_apartment_sales_correlation.csv")
    corr.to_csv(corr_csv, encoding="utf-8-sig")
    print("=== 상관계수 (log1p, Pearson) ===")
    print(corr.round(2).to_string())
    print(f"{corr_csv} 저장\n")

    # === 히트맵 ===
    fig, ax = plt.subplots(figsize=(6, 6))
    im = ax.imshow(corr.values, cmap=DIVERGING_CMAP, vmin=-0.6, vmax=0.6, aspect="auto")
    ax.set_xticks(range(len(corr.columns)))
    ax.set_xticklabels(corr.columns, color=INK_SECONDARY, fontsize=10)
    ax.set_yticks(range(len(corr.index)))
    ax.set_yticklabels(corr.index, color=INK_SECONDARY, fontsize=10)
    for i in range(corr.shape[0]):
        for j in range(corr.shape[1]):
            v = corr.values[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                     color="white" if abs(v) > 0.35 else INK_PRIMARY, fontsize=9)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title("송파구: 아파트 시가/면적 x 업종별 매출 상관계수", color=INK_PRIMARY, fontsize=12, pad=12)
    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.ax.tick_params(colors=INK_MUTED)
    cbar.outline.set_visible(False)
    fig.tight_layout()
    out1 = os.path.join(FIG_DIR, "songpa_apartment_sales_corr_heatmap.png")
    fig.savefig(out1, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out1} 저장")

    # === 소득분위별 업종 평균매출 소형멀티플 ===
    quartile_order = ["1분위(하위)", "2분위", "3분위", "4분위(상위)"]
    fig, axes = plt.subplots(2, 5, figsize=(18, 7), sharex=True)
    for ax, biz in zip(axes.flat, INDUSTRY_CODES.values()):
        means = merged.groupby("소득분위", observed=True)[biz].mean().reindex(quartile_order)
        ax.set_facecolor(SURFACE)
        ax.bar(range(len(quartile_order)), means.values, color=BLUE, width=0.6)
        ax.set_xticks(range(len(quartile_order)))
        ax.set_xticklabels(quartile_order, rotation=30, ha="right", fontsize=8, color=INK_MUTED)
        ax.set_title(biz, color=INK_PRIMARY, fontsize=11)
        ax.tick_params(colors=INK_MUTED, labelsize=8)
        ax.grid(axis="y", color=GRIDLINE, linewidth=1)
        ax.set_axisbelow(True)
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)
        for spine in ["left", "bottom"]:
            ax.spines[spine].set_color(GRIDLINE)
    fig.suptitle("송파구: 아파트 시가 소득분위별 업종 평균매출", color=INK_PRIMARY, fontsize=14)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    out2 = os.path.join(FIG_DIR, "songpa_income_quartile_sales.png")
    fig.savefig(out2, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out2} 저장")

    # === 산점도 예시 ===
    pairs = [("한식음식점", "일식음식점"), ("치킨전문점", "커피-음료")]
    fig, axes = plt.subplots(1, 4, figsize=(18, 4.5))
    flat_pairs = [b for p in pairs for b in p]
    for ax, biz in zip(axes, flat_pairs):
        x = log_df["아파트_평균_시가"]
        y = log_df[biz]
        r = x.corr(y)
        ax.set_facecolor(SURFACE)
        ax.scatter(x, y, color=BLUE, alpha=0.7, s=32, edgecolor=SURFACE, linewidth=0.6)
        if x.std() > 0:
            slope, intercept = np.polyfit(x, y, 1)
            xs = np.linspace(x.min(), x.max(), 50)
            ax.plot(xs, slope * xs + intercept, color=ORANGE, linewidth=2)
        ax.set_xlabel("log(1+아파트 평균시가)", color=INK_SECONDARY, fontsize=9)
        ax.set_ylabel(f"log(1+{biz} 매출)", color=INK_SECONDARY, fontsize=9)
        ax.set_title(f"{biz} (r={r:.2f})", color=INK_PRIMARY, fontsize=11)
        ax.tick_params(colors=INK_MUTED, labelsize=8)
        ax.grid(color=GRIDLINE, linewidth=1)
        ax.set_axisbelow(True)
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)
        for spine in ["left", "bottom"]:
            ax.spines[spine].set_color(GRIDLINE)
    fig.suptitle("송파구: 아파트 평균시가 vs 업종별 매출 산점도 예시", color=INK_PRIMARY, fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    out3 = os.path.join(FIG_DIR, "songpa_apartment_sales_scatter_examples.png")
    fig.savefig(out3, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out3} 저장")


if __name__ == "__main__":
    main()
