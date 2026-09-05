"""
서울시 상권분석서비스: 잠실 vs 여의도 행정동별 연령대별 직장인구 vs 연령대별 추정매출
(호프/음식/카페(음료) 업종) 상관관계 산점도
호프, 음식, 카페(음료)를 각각 별도로 비교하고, 매출도 연령대별로 분리해서 비교

업종 매핑 (서비스_업종_코드_명 기준, 필요 시 SALES_CATEGORIES 수정):
    호프          -> 호프-간이주점
    음식          -> 한식음식점, 중식음식점, 일식음식점, 양식음식점
    카페(음료)     -> 커피-음료

x축: 연령대별 직장인구 수
y축: 해당 연령대가 쓴 것으로 집계된 연령대별 추정매출 (연령대_XX_매출_금액)
     (예: 20대 직장인구 수 vs 20대가 쓴 호프 매출)

지역 필터 (REGIONS: 행정동_코드_명에 해당 문자열이 포함된 행정동만 사용):
    잠실  -> "잠실"  (잠실본동, 잠실2·3·4·6동 등)
    여의도 -> "여의"  (여의동 — 여의도는 행정동 통합으로 '여의동' 하나로 표기됨)

점 색상 = 지역, 점 옆 라벨 = 행정동명

실행:
    .venv/bin/python3 scatter_age_food_sales.py
"""
import math

import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams["font.family"] = "AppleGothic"  # macOS 한글 폰트 (한글 깨짐 방지)
plt.rcParams["axes.unicode_minus"] = False

QUARTER = "20261"  # 기준_년분기_코드 (두 파일에 공통으로 존재하는 최신 분기)

# 행정동_코드_명에 해당 문자열이 포함되면 그 지역으로 분류 (순서대로 매칭)
REGIONS = {
    "잠실": "잠실",
    "여의도": "여의",
}
REGION_COLORS = {
    "잠실": "#2a78d6",
    "여의도": "#eb6834",
}

POP_FILE = "data/직장인구/서울시 상권분석서비스(직장인구-행정동).csv"
SALES_FILE = "data/추정매출/서울시 상권분석서비스(추정매출-행정동).csv"

SALES_CATEGORIES = {
    "호프": ["호프-간이주점"],
    "음식": ["한식음식점", "중식음식점", "일식음식점", "양식음식점"],
    "카페(음료)": ["커피-음료"],
}

AGE_COLUMNS = {
    "10대": "연령대_10_직장_인구_수",
    "20대": "연령대_20_직장_인구_수",
    "30대": "연령대_30_직장_인구_수",
    "40대": "연령대_40_직장_인구_수",
    "50대": "연령대_50_직장_인구_수",
    "60대 이상": "연령대_60_이상_직장_인구_수",
}

AGE_SALES_COLUMNS = {
    "10대": "연령대_10_매출_금액",
    "20대": "연령대_20_매출_금액",
    "30대": "연령대_30_매출_금액",
    "40대": "연령대_40_매출_금액",
    "50대": "연령대_50_매출_금액",
    "60대 이상": "연령대_60_이상_매출_금액",
}


def match_region(dong_name: str):
    for label, pattern in REGIONS.items():
        if pattern in dong_name:
            return label
    return None


def load_population(quarter: str) -> pd.DataFrame:
    df = pd.read_csv(POP_FILE, encoding="cp949", dtype={"기준_년분기_코드": str, "행정동_코드": str})
    df = df[df["기준_년분기_코드"] == quarter].copy()
    df["지역"] = df["행정동_코드_명"].apply(match_region)
    df = df[df["지역"].notna()]
    cols = ["행정동_코드", "행정동_코드_명", "지역", *AGE_COLUMNS.values()]
    return df[cols]


def load_sales(quarter: str, dong_codes: list) -> pd.DataFrame:
    all_cats = [c for group in SALES_CATEGORIES.values() for c in group]
    df = pd.read_csv(SALES_FILE, encoding="cp949", dtype={"기준_년분기_코드": str, "행정동_코드": str})
    df = df[
        (df["기준_년분기_코드"] == quarter)
        & (df["행정동_코드"].isin(dong_codes))
        & (df["서비스_업종_코드_명"].isin(all_cats))
    ]

    result = pd.DataFrame({"행정동_코드": dong_codes})
    for cat_label, cats in SALES_CATEGORIES.items():
        sub = df[df["서비스_업종_코드_명"].isin(cats)]
        for age_label, age_col in AGE_SALES_COLUMNS.items():
            g = sub.groupby("행정동_코드")[age_col].sum().rename(f"{cat_label}__{age_label}")
            result = result.merge(g, on="행정동_코드", how="left")
    return result


def plot_category(merged: pd.DataFrame, category_label: str):
    fig, axes = plt.subplots(2, 3, figsize=(15, 9))
    axes = axes.flatten()
    colors = merged["지역"].map(REGION_COLORS)

    for ax, (age_label, pop_col) in zip(axes, AGE_COLUMNS.items()):
        x = merged[pop_col]
        y = merged[f"{category_label}__{age_label}"]

        r = x.corr(y)
        valid = (x > 0) & (y > 0)
        log_r = x[valid].apply(math.log10).corr(y[valid].apply(math.log10)) if valid.sum() >= 2 else float("nan")

        ax.scatter(x, y, alpha=0.85, s=50, c=colors, edgecolors="white", linewidths=0.6, zorder=3)
        for name, xi, yi in zip(merged["행정동_코드_명"], x, y):
            if pd.notna(xi) and pd.notna(yi):
                ax.annotate(name, (xi, yi), fontsize=7, color="#52514e", xytext=(4, 4), textcoords="offset points")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_title(f"{age_label} (r={r:.2f}, log-log r={log_r:.2f})", fontsize=11)
        ax.set_xlabel(f"{age_label} 직장인구 (명, 로그축)")
        ax.set_ylabel(f"{age_label} 추정매출: {category_label} (원, 로그축)")
        ax.grid(True, which="both", linestyle="-", linewidth=0.5, alpha=0.3)

        print(f"  {age_label}: r={r:.3f}, log-log r={log_r:.3f}")

    handles = [
        plt.Line2D([0], [0], marker="o", linestyle="", color=color, markeredgecolor="white", markersize=8, label=label)
        for label, color in REGION_COLORS.items()
    ]
    fig.legend(handles=handles, loc="upper right", ncol=len(REGION_COLORS), frameon=False)
    fig.suptitle(
        f"[잠실 vs 여의도] 행정동별 연령대별 직장인구 × 연령대별 {category_label} 추정매출 ({QUARTER}, n={len(merged)})",
        fontsize=13,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.95])


def main():
    print("사용된 업종:")
    for label, cats in SALES_CATEGORIES.items():
        print(f"  {label}: {', '.join(cats)}")

    pop = load_population(QUARTER)
    sales = load_sales(QUARTER, pop["행정동_코드"].tolist())
    merged = pop.merge(sales, on="행정동_코드", how="left")
    print(f"\n매칭된 행정동 수: {len(merged)}")
    for region_label in REGIONS:
        names = merged.loc[merged["지역"] == region_label, "행정동_코드_명"]
        print(f"  {region_label}: {', '.join(names)}")

    for category_label in SALES_CATEGORIES:
        print(f"\n[{category_label}]")
        plot_category(merged, category_label)

    plt.show()


if __name__ == "__main__":
    main()
