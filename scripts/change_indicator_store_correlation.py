"""상권변화지표(LL/LH/HL/HH) x 점포 데이터 상관관계 분석.

상권변화지표는 업종 구분 없이 상권/행정동 단위로만 존재하고(.claude/CLAUDE.md 참고),
점포 데이터는 업종별로 상권/행정동 단위 개업률·폐업률·점포수를 제공한다. 두 데이터를
상권_코드 / 행정동_코드 기준으로 결합(broadcast join: 상권변화지표 값 1개가 그 상권의
업종 10개 행 모두에 복제됨)해 "그 지역이 다이나믹/정체/확장/축소 중 무엇이냐가 실제
업종별 개업·폐업 동향과 관련이 있는가"를 확인한다.

지표 정의:
- gap_survive = 지역 운영(생존)영업개월평균 - 서울 운영영업개월평균  (양수면 H, 음수면 L → 코드 1번째 글자)
- gap_close   = 지역 폐업영업개월평균 - 서울 폐업영업개월평균        (양수면 H, 음수면 L → 코드 2번째 글자)
"""
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

plt.rcParams["font.family"] = "Apple SD Gothic Neo"
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "output", "change_indicator")
FIG_DIR = os.path.join(ROOT, "output", "figures", "change_indicator")
os.makedirs(OUT, exist_ok=True)
os.makedirs(FIG_DIR, exist_ok=True)

QUARTER = 20261

INDUSTRY_CODES = {
    "CS100001": "한식음식점", "CS100002": "중식음식점", "CS100003": "일식음식점",
    "CS100004": "양식음식점", "CS100005": "제과점", "CS100006": "패스트푸드점",
    "CS100007": "치킨전문점", "CS100008": "분식전문점", "CS100009": "호프-간이주점",
    "CS100010": "커피-음료",
}
QUADRANT_ORDER = ["LL", "LH", "HL", "HH"]
QUADRANT_LABEL = {"LL": "다이나믹(LL)", "LH": "상권확장(LH)", "HL": "상권축소(HL)", "HH": "정체(HH)"}

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
CAT4 = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]  # LL, LH, HL, HH


def load_change_indicator(unit_key, fname):
    path = os.path.join(DATA, "상권변화지표", fname)
    df = pd.read_csv(path, encoding="cp949")
    df = df[df["기준_년분기_코드"] == QUARTER].copy()
    df["gap_survive"] = df["운영_영업_개월_평균"] - df["서울_운영_영업_개월_평균"]
    df["gap_close"] = df["폐업_영업_개월_평균"] - df["서울_폐업_영업_개월_평균"]
    return df[[unit_key, "상권_변화_지표", "gap_survive", "gap_close"]]


def load_store(unit_key, fname):
    # data/점포/ 밑이 연도별 폴더로 재구성됨 — QUARTER=20261(2026Q1)은 '2026년' 폴더 파일에 들어있음
    path = os.path.join(DATA, "점포", "2026년", fname)
    df = pd.read_csv(path, encoding="cp949")
    df = df[(df["기준_년분기_코드"] == QUARTER) & df["서비스_업종_코드"].isin(INDUSTRY_CODES)].copy()
    df["프랜차이즈_비율"] = df["프랜차이즈_점포_수"] / df["전체_점포_수"].replace(0, np.nan)
    df["업종명"] = df["서비스_업종_코드"].map(INDUSTRY_CODES)
    return df[[unit_key, "업종명", "전체_점포_수", "개업_율", "폐업_률", "프랜차이즈_비율"]]


def analyze_unit(unit_key, change_fname, store_fname, unit_label):
    change = load_change_indicator(unit_key, change_fname)
    store = load_store(unit_key, store_fname)
    merged = store.merge(change, on=unit_key, how="inner")
    print(f"[{unit_label}] 결합 후 행 수: {len(merged)} (점포 {len(store)} x 지표 매칭)")

    # === 업종별 상관계수 히트맵 ===
    rows = []
    for biz in INDUSTRY_CODES.values():
        sub = merged[merged["업종명"] == biz]
        row = {
            "업종": biz,
            "gap_survive x 개업률": sub["gap_survive"].corr(sub["개업_율"]),
            "gap_survive x 폐업률": sub["gap_survive"].corr(sub["폐업_률"]),
            "gap_close x 개업률": sub["gap_close"].corr(sub["개업_율"]),
            "gap_close x 폐업률": sub["gap_close"].corr(sub["폐업_률"]),
        }
        rows.append(row)
    corr_df = pd.DataFrame(rows).set_index("업종")
    corr_csv = os.path.join(OUT, f"corr_{unit_label}.csv")
    corr_df.to_csv(corr_csv, encoding="utf-8-sig")
    print(corr_df.round(2).to_string())
    print(f"{corr_csv} 저장\n")

    fig, ax = plt.subplots(figsize=(8, 5.5))
    im = ax.imshow(corr_df.values, cmap=DIVERGING_CMAP, vmin=-0.5, vmax=0.5, aspect="auto")
    ax.set_xticks(range(len(corr_df.columns)))
    ax.set_xticklabels(corr_df.columns, rotation=30, ha="right", color=INK_SECONDARY, fontsize=9)
    ax.set_yticks(range(len(corr_df.index)))
    ax.set_yticklabels(corr_df.index, color=INK_SECONDARY, fontsize=10)
    for i in range(corr_df.shape[0]):
        for j in range(corr_df.shape[1]):
            v = corr_df.values[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                     color="white" if abs(v) > 0.3 else INK_PRIMARY, fontsize=9)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title(f"{unit_label} 단위: 상권변화지표 격차 x 업종별 점포 동향 상관계수", color=INK_PRIMARY, fontsize=12, pad=12)
    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.ax.tick_params(colors=INK_MUTED)
    cbar.outline.set_visible(False)
    fig.tight_layout()
    out1 = os.path.join(FIG_DIR, f"corr_heatmap_{unit_label}.png")
    fig.savefig(out1, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out1} 저장")

    # === 사분면(LL/LH/HL/HH)별 평균 개업률·폐업률 ===
    quad = merged.groupby("상권_변화_지표")[["개업_율", "폐업_률"]].mean().reindex(QUADRANT_ORDER)
    quad_csv = os.path.join(OUT, f"quadrant_mean_rates_{unit_label}.csv")
    quad.to_csv(quad_csv, encoding="utf-8-sig")
    print(quad.round(2).to_string())
    print(f"{quad_csv} 저장\n")

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.set_facecolor(SURFACE)
    x = np.arange(len(QUADRANT_ORDER))
    width = 0.35
    ax.bar(x - width / 2, quad["개업_율"], width, color=BLUE, label="평균 개업률(%)")
    ax.bar(x + width / 2, quad["폐업_률"], width, color=ORANGE, label="평균 폐업률(%)")
    ax.set_xticks(x)
    ax.set_xticklabels([QUADRANT_LABEL[q] for q in QUADRANT_ORDER], color=INK_SECONDARY, fontsize=10)
    ax.set_ylabel("%", color=INK_SECONDARY, fontsize=10)
    ax.set_title(f"{unit_label} 단위: 상권변화지표 사분면별 평균 개업률·폐업률", color=INK_PRIMARY, fontsize=12)
    ax.tick_params(colors=INK_MUTED)
    ax.legend(frameon=False, labelcolor=INK_SECONDARY)
    ax.grid(axis="y", color=GRIDLINE, linewidth=1)
    ax.set_axisbelow(True)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color(GRIDLINE)
    fig.tight_layout()
    out2 = os.path.join(FIG_DIR, f"quadrant_rates_{unit_label}.png")
    fig.savefig(out2, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out2} 저장\n")

    return merged


def main():
    analyze_unit("상권_코드", "서울시 상권분석서비스(상권변화지표-상권).csv",
                 "서울시 상권분석서비스(점포-상권).csv", "상권")
    analyze_unit("행정동_코드", "서울시 상권분석서비스(상권변화지표-행정동).csv",
                 "서울시 상권분석서비스(점포-행정동).csv", "행정동")


if __name__ == "__main__":
    main()
