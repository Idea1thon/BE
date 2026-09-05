"""
서울시 상권분석서비스: 행정동별 총 직장인구 vs 추정매출 상관관계 산점도

실행:
    .venv/bin/python3 scatter_employment_sales.py
"""
import math

import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams["font.family"] = "AppleGothic"  # macOS 한글 폰트 (한글 깨짐 방지)
plt.rcParams["axes.unicode_minus"] = False

QUARTER = "20261"  # 기준_년분기_코드 (두 파일에 공통으로 존재하는 최신 분기)

POP_FILE = "서울시 상권분석서비스(직장인구-행정동).csv"
SALES_FILE = "서울시 상권분석서비스(추정매출-행정동).csv"


def load_population(quarter: str) -> pd.DataFrame:
    df = pd.read_csv(POP_FILE, encoding="cp949", dtype={"기준_년분기_코드": str, "행정동_코드": str})
    df = df[df["기준_년분기_코드"] == quarter]
    return df[["행정동_코드", "행정동_코드_명", "총_직장_인구_수"]]


def load_sales(quarter: str) -> pd.DataFrame:
    df = pd.read_csv(SALES_FILE, encoding="cp949", dtype={"기준_년분기_코드": str, "행정동_코드": str})
    df = df[df["기준_년분기_코드"] == quarter]
    return df.groupby("행정동_코드", as_index=False)["당월_매출_금액"].sum()


def main():
    pop = load_population(QUARTER)
    sales = load_sales(QUARTER)

    merged = pop.merge(sales, on="행정동_코드", how="inner")
    merged = merged.rename(columns={"총_직장_인구_수": "직장인구", "당월_매출_금액": "추정매출"})

    r = merged["추정매출"].corr(merged["직장인구"])
    log_r = merged["추정매출"].apply(math.log10).corr(merged["직장인구"].apply(math.log10))

    print(f"매칭된 행정동 수: {len(merged)}")
    print(f"피어슨 상관계수 (원값): {r:.3f}")
    print(f"피어슨 상관계수 (로그-로그): {log_r:.3f}")

    fig, ax = plt.subplots(figsize=(9, 6.5))
    ax.scatter(merged["추정매출"], merged["직장인구"], alpha=0.6, s=25, color="#2a78d6", edgecolors="white", linewidths=0.5)

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("추정매출 (원, 로그축)")
    ax.set_ylabel("직장인구 (명, 로그축)")
    ax.set_title(f"행정동별 추정매출 × 직장인구 상관관계 ({QUARTER}, n={len(merged)}, r={r:.2f}, log-log r={log_r:.2f})")
    ax.grid(True, which="both", linestyle="-", linewidth=0.5, alpha=0.3)

    # 상위 3개 행정동 라벨 표시 (매출, 인구 기준 각각 최고점)
    top_sales = merged.nlargest(1, "추정매출")
    top_pop = merged.nlargest(1, "직장인구")
    for _, row in pd.concat([top_sales, top_pop]).drop_duplicates(subset="행정동_코드").iterrows():
        ax.annotate(row["행정동_코드_명"], (row["추정매출"], row["직장인구"]),
                    textcoords="offset points", xytext=(6, 6), fontsize=9, color="#52514e")

    fig.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
