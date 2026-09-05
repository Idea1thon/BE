"""송파구 상권 대상: 유동인구 시간대 분포 vs 업종별 매출 시간대 분포 비교.

유동인구는 업종과 무관하게 송파구 전체 상권 합계로 하나의 프로파일을 만들고,
매출은 업종(10개 독립 코드)별로 각각 프로파일을 만들어 같은 좌표에 겹쳐 그린다.
두 선의 모양이 얼마나 닮았는지(피크 시간대 일치 여부)가 "그 업종이 유동인구를
그대로 따라가는 업종인지, 아니면 특정 시간대(직장인구의 점심/퇴근 등)에 쏠리는
업종인지"를 보여주는 근거가 된다.
"""
import os

import matplotlib.pyplot as plt
import pandas as pd

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

TIME_BINS = ["00~06", "06~11", "11~14", "14~17", "17~21", "21~24"]
FLOW_COLS = [f"시간대_{b.replace('~', '_')}_유동인구_수" for b in TIME_BINS]
SALES_COLS = [f"시간대_{b}_매출_금액" for b in TIME_BINS]

INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
SURFACE = "#fcfcfb"
GRIDLINE = "#e1e0d9"
BLUE = "#2a78d6"
ORANGE = "#eb6834"


def load_songpa_trdar_codes():
    df = pd.read_csv(os.path.join(SONGPA_DIR, "songpa_trdar_membership.csv"), encoding="utf-8-sig")
    return set(df["TRDAR_CD"].astype(int))


def load_flow_profile(trdar_codes):
    path = os.path.join(DATA, "길단위인구", "서울시 상권분석서비스(길단위인구-상권).csv")
    df = pd.read_csv(path, encoding="cp949")
    df = df[(df["기준_년분기_코드"] == QUARTER) & df["상권_코드"].isin(trdar_codes)]
    totals = df[FLOW_COLS].sum()
    profile = totals / totals.sum()
    return pd.Series(profile.values, index=TIME_BINS)


def load_sales_profiles(trdar_codes):
    # data/추정매출/이 연도별 폴더(2021~2026)로 재구성됨 — QUARTER(20261)는 '2026' 폴더 파일에 있음
    path = os.path.join(DATA, "추정매출", "2026", "서울시 상권분석서비스(추정매출-상권).csv")
    df = pd.read_csv(path, encoding="cp949")
    df = df[
        (df["기준_년분기_코드"] == QUARTER)
        & df["상권_코드"].isin(trdar_codes)
        & df["서비스_업종_코드"].isin(INDUSTRY_CODES)
    ]
    grouped = df.groupby("서비스_업종_코드")[SALES_COLS].sum()
    profiles = grouped.div(grouped.sum(axis=1), axis=0)
    profiles = profiles.rename(index=INDUSTRY_CODES)
    profiles.columns = TIME_BINS
    return profiles


def main():
    trdar_codes = load_songpa_trdar_codes()
    flow_profile = load_flow_profile(trdar_codes)
    sales_profiles = load_sales_profiles(trdar_codes)

    out_csv = os.path.join(SONGPA_DIR, "songpa_time_profiles.csv")
    combined = sales_profiles.copy()
    combined.loc["유동인구(전체)"] = flow_profile
    combined.to_csv(out_csv, encoding="utf-8-sig")
    print(f"{out_csv} 저장")
    print(combined.round(3).to_string())

    fig, axes = plt.subplots(2, 5, figsize=(18, 7), sharey=True)
    x = range(len(TIME_BINS))
    for ax, biz in zip(axes.flat, INDUSTRY_CODES.values()):
        ax.set_facecolor(SURFACE)
        ax.plot(x, flow_profile.values, color=BLUE, linewidth=2, marker="o", markersize=4, label="유동인구(송파구 전체)")
        ax.plot(x, sales_profiles.loc[biz].values, color=ORANGE, linewidth=2, marker="o", markersize=4, label="해당 업종 매출 (패널 제목 참고)")
        ax.set_xticks(x)
        ax.set_xticklabels(TIME_BINS, rotation=45, ha="right", fontsize=8, color=INK_MUTED)
        ax.set_title(biz, color=INK_PRIMARY, fontsize=11)
        ax.tick_params(colors=INK_MUTED, labelsize=8)
        ax.grid(color=GRIDLINE, linewidth=1)
        ax.set_axisbelow(True)
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)
        for spine in ["left", "bottom"]:
            ax.spines[spine].set_color(GRIDLINE)

    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, bbox_to_anchor=(0.5, 1.04), frameon=False,
               labelcolor=INK_SECONDARY, fontsize=10)
    fig.suptitle("송파구: 유동인구 시간대 비중 vs 업종별 매출 시간대 비중", color=INK_PRIMARY, fontsize=14, y=1.1)
    fig.tight_layout()
    out = os.path.join(FIG_DIR, "songpa_time_profile_small_multiples.png")
    fig.savefig(out, dpi=150, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    print(f"{out} 저장")


if __name__ == "__main__":
    main()
