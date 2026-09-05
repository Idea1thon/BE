"""아파트(소득 대리지표) x 업종별 매출 상관관계 — 서울 전체 상권, 전체 공통분기 패널.

scripts/songpa_apartment_sales_panel.py를 송파구 한정 없이 서울 전체로 일반화한 버전
(원본은 output/songpa/에 그대로 남겨 송파구 검증 결과와 비교 가능하게 유지).

추정매출은 20251~20261(5개 분기)만 있고 아파트는 20211~20261(21개 분기)까지 있어,
두 데이터셋의 공통 분기(20251, 20252, 20253, 20254, 20261)만 사용한다.
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
SEOUL_DIR = os.path.join(ROOT, "output", "seoul")
FIG_DIR = os.path.join(ROOT, "output", "figures", "seoul")
os.makedirs(SEOUL_DIR, exist_ok=True)
os.makedirs(FIG_DIR, exist_ok=True)

INDUSTRY_CODES = {
    "CS100001": "한식음식점", "CS100002": "중식음식점", "CS100003": "일식음식점",
    "CS100004": "양식음식점", "CS100005": "제과점", "CS100006": "패스트푸드점",
    "CS100007": "치킨전문점", "CS100008": "분식전문점", "CS100009": "호프-간이주점",
    "CS100010": "커피-음료",
}
HIGHLIGHT = "호프-간이주점"

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


def common_quarters():
    sales_q = set(pd.read_csv(os.path.join(DATA, "추정매출", "2026", "서울시 상권분석서비스(추정매출-상권).csv"),
                               encoding="cp949", usecols=["기준_년분기_코드"])["기준_년분기_코드"].unique())
    apt_q = set(pd.read_csv(os.path.join(DATA, "아파트", "서울시 상권분석서비스(아파트-상권).csv"),
                             encoding="cp949", usecols=["기준_년분기_코드"])["기준_년분기_코드"].unique())
    return sorted(sales_q & apt_q)


def load_apartment_panel(quarters):
    path = os.path.join(DATA, "아파트", "서울시 상권분석서비스(아파트-상권).csv")
    df = pd.read_csv(path, encoding="cp949")
    df = df[df["기준_년분기_코드"].isin(quarters)]
    return df[["기준_년분기_코드", "상권_코드", "상권_코드_명", "아파트_평균_면적", "아파트_평균_시가"]]


def load_sales_panel(quarters):
    path = os.path.join(DATA, "추정매출", "2026", "서울시 상권분석서비스(추정매출-상권).csv")
    df = pd.read_csv(path, encoding="cp949")
    df = df[df["기준_년분기_코드"].isin(quarters) & df["서비스_업종_코드"].isin(INDUSTRY_CODES)]
    pivot = df.pivot_table(index=["기준_년분기_코드", "상권_코드"], columns="서비스_업종_코드",
                            values="당월_매출_금액", aggfunc="sum")
    pivot = pivot.reindex(columns=list(INDUSTRY_CODES.keys())).rename(columns=INDUSTRY_CODES)
    return pivot.reset_index()


def main():
    quarters = common_quarters()
    print(f"공통 분기: {quarters}\n")

    apt = load_apartment_panel(quarters)
    sales = load_sales_panel(quarters)
    panel = apt.merge(sales, on=["기준_년분기_코드", "상권_코드"], how="inner").dropna(subset=["아파트_평균_시가"])
    print(f"패널 관측치 수: {len(panel)}행 (상권-분기 쌍, 분기당 평균 {len(panel)/len(quarters):.0f}개 상권)\n")

    out_csv = os.path.join(SEOUL_DIR, "seoul_apartment_sales_panel.csv")
    panel.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"{out_csv} 저장\n")

    log_panel = panel.copy()
    for col in ["아파트_평균_시가", "아파트_평균_면적"] + list(INDUSTRY_CODES.values()):
        log_panel[col] = np.log1p(log_panel[col].fillna(0))

    # === (1) 전체 분기 pooled 상관계수: 단일분기(20261) 대비 비교 ===
    pooled_corr = pd.DataFrame({
        biz: {"아파트_평균_시가": log_panel["아파트_평균_시가"].corr(log_panel[biz]),
              "아파트_평균_면적": log_panel["아파트_평균_면적"].corr(log_panel[biz])}
        for biz in INDUSTRY_CODES.values()
    }).T
    single_q = log_panel[log_panel["기준_년분기_코드"] == 20261]
    single_corr = pd.Series({biz: single_q["아파트_평균_시가"].corr(single_q[biz]) for biz in INDUSTRY_CODES.values()},
                             name="20261_단일분기")
    compare = pooled_corr[["아파트_평균_시가"]].rename(columns={"아파트_평균_시가": "전체분기_pooled"}).join(single_corr)
    compare_csv = os.path.join(SEOUL_DIR, "seoul_apartment_sales_pooled_vs_single.csv")
    compare.to_csv(compare_csv, encoding="utf-8-sig")
    print("=== 전체분기 pooled 상관계수 vs 20261 단일분기 비교 ===")
    print(compare.round(2).to_string())
    print(f"{compare_csv} 저장\n")

    # === (2) 분기별 상관계수 추이 (특히 호프-간이주점) ===
    trend_rows = []
    for q in quarters:
        sub = log_panel[log_panel["기준_년분기_코드"] == q]
        row = {"분기": q}
        for biz in INDUSTRY_CODES.values():
            row[biz] = sub["아파트_평균_시가"].corr(sub[biz])
        trend_rows.append(row)
    trend = pd.DataFrame(trend_rows).set_index("분기")
    trend_csv = os.path.join(SEOUL_DIR, "seoul_apartment_sales_quarterly_corr_trend.csv")
    trend.to_csv(trend_csv, encoding="utf-8-sig")
    print("=== 분기별 상관계수 추이 ===")
    print(trend.round(2).to_string())
    print(f"{trend_csv} 저장\n")

    # === 시각화 1: pooled 히트맵 ===
    fig, ax = plt.subplots(figsize=(6, 6))
    im = ax.imshow(pooled_corr.values, cmap=DIVERGING_CMAP, vmin=-0.6, vmax=0.6, aspect="auto")
    ax.set_xticks(range(len(pooled_corr.columns)))
    ax.set_xticklabels(pooled_corr.columns, color=INK_SECONDARY, fontsize=10)
    ax.set_yticks(range(len(pooled_corr.index)))
    ax.set_yticklabels(pooled_corr.index, color=INK_SECONDARY, fontsize=10)
    for i in range(pooled_corr.shape[0]):
        for j in range(pooled_corr.shape[1]):
            v = pooled_corr.values[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                     color="white" if abs(v) > 0.35 else INK_PRIMARY, fontsize=9)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title(f"서울 전체: 아파트 x 업종별 매출 상관계수 (전체분기 pooled, n={len(panel)})",
                 color=INK_PRIMARY, fontsize=11, pad=12)
    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.ax.tick_params(colors=INK_MUTED)
    cbar.outline.set_visible(False)
    fig.tight_layout()
    out1 = os.path.join(FIG_DIR, "seoul_apartment_sales_corr_heatmap_pooled.png")
    fig.savefig(out1, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out1} 저장")

    # === 시각화 2: 분기별 상관계수 추이 라인 (전체 업종 + 호프-간이주점 강조) ===
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.set_facecolor(SURFACE)
    x = range(len(quarters))
    for biz in INDUSTRY_CODES.values():
        if biz == HIGHLIGHT:
            continue
        ax.plot(x, trend[biz].values, color=INK_MUTED, linewidth=1, alpha=0.5, zorder=1)
    ax.plot(x, trend[HIGHLIGHT].values, color=ORANGE, linewidth=2.5, marker="o", markersize=6,
             label=HIGHLIGHT, zorder=3)
    ax.axhline(0, color=GRIDLINE, linewidth=1.2, zorder=0)
    ax.set_xticks(x)
    ax.set_xticklabels(quarters, color=INK_SECONDARY)
    ax.set_ylabel("Pearson r (log1p, vs 아파트 평균시가)", color=INK_SECONDARY, fontsize=10)
    ax.set_title("서울 전체: 분기별 아파트-매출 상관계수 추이 (회색=나머지 9개 업종, 주황=호프-간이주점)",
                 color=INK_PRIMARY, fontsize=11)
    ax.tick_params(colors=INK_MUTED)
    ax.legend(frameon=False, labelcolor=INK_SECONDARY)
    ax.grid(color=GRIDLINE, linewidth=1)
    ax.set_axisbelow(True)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color(GRIDLINE)
    fig.tight_layout()
    out2 = os.path.join(FIG_DIR, "seoul_apartment_sales_quarterly_trend.png")
    fig.savefig(out2, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out2} 저장")


if __name__ == "__main__":
    main()
