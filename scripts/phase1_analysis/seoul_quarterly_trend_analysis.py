"""
임대료를 제외하고 유동인구·아파트·추정매출·점포 4개 데이터셋의 분기별 추세를
서울 전체(임대료 조사 63개 지역 제한 없음) 기준으로 분석한다.
(배경: 17-6에서 임대료를 다변량 회귀에 추가해도 매출에 대한 고유 기여가 거의 없다는 게
확인돼, 이후 분석에서는 임대료를 빼고 나머지 4개 데이터셋에 집중하기로 함.)

두 갈래:
(A) 상권/행정동/상권배후지 3단위 각각 독립적으로 서울 전체 평균의 분기별 추세를 본다.
(B) crosswalk 기반 겹침결합(scripts/seoul_apartment_sales_multilevel.py의 겹침결합 로직을
    4개 데이터셋 전부로 일반화) — 상권 기준 "행정동결합값"·"배후지결합값"을 만들어 상권 자체
    값과 비교하는 통합 분기별 추세를 본다. 이게 crosswalk를 활용한 "전체지역" 결과.

공통분기: 유동인구/아파트(단일 파일, 21개 분기 native) x 추정매출/점포(연도별 5개 파일 통합,
21개 분기)가 전부 2021Q1~2026Q1(21개 분기)로 일치 — 임대료 데이터 범위(~2025Q4)에 더 이상
묶이지 않아 2026Q1까지 포함한다.

점포 데이터는 11-1d에서 발견한 컬럼 스키마 버그(2021~2024년 파일의 '점포_수'는 프랜차이즈
제외값, '유사_업종_점포_수'가 신 스키마 '전체_점포_수'에 대응)를 동일하게 반영한다.
"""
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams["font.family"] = "Apple SD Gothic Neo"
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
CROSSWALKS = os.path.join(ROOT, "output", "crosswalks")
OUT = os.path.join(ROOT, "output", "seoul")
FIG_DIR = os.path.join(ROOT, "output", "figures", "seoul")
os.makedirs(OUT, exist_ok=True)
os.makedirs(FIG_DIR, exist_ok=True)

UNITS = ["상권", "행정동", "상권배후지"]
INDUSTRY_CODES = {
    "CS100001": "한식음식점", "CS100002": "중식음식점", "CS100003": "일식음식점",
    "CS100004": "양식음식점", "CS100005": "제과점", "CS100006": "패스트푸드점",
    "CS100007": "치킨전문점", "CS100008": "분식전문점", "CS100009": "호프-간이주점",
    "CS100010": "커피-음료",
}

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
CAT4 = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]  # 유동인구/아파트/매출/점포
CAT3 = ["#2a78d6", "#eb6834", "#1baf7a"]  # 상권자체/행정동결합/배후지결합

# ---------- 점포: 연도별 파일(11-1d 스키마 수정 반영) ----------
STORE_YEAR_FILES = {
    "상권": [
        "2021년/서울시_상권분석서비스(점포-상권)_2021년.csv",
        "2022년/서울시_상권분석서비스(점포-상권)_2022년.csv",
        "2023년/서울시_상권분석서비스(점포-상권)_2023년.csv",
        "2024년/서울시 상권분석서비스(점포-상권)_2024년.csv",
        "2026년/서울시 상권분석서비스(점포-상권).csv",
    ],
    "행정동": [
        "2021년/서울시_상권분석서비스(점포-행정동)_2021년.csv",
        "2022년/서울시_상권분석서비스(점포-행정동)_2022년.csv",
        "2023년/서울시 상권분석서비스(점포-행정동)_2023년.csv",
        "2024년/서울시 상권분석서비스(점포-행정동)_2024년.csv",
        "2026년/서울시 상권분석서비스(점포-행정동).csv",
    ],
    "상권배후지": [
        "2021년/서울시_상권분석서비스(점포-상권배후지)_2021년.csv",
        "2022년/서울시_상권분석서비스(점포-상권배후지)_2022년.csv",
        "2023년/서울시_상권분석서비스(점포-상권배후지)_2023년.csv",
        "2024년/서울시 상권분석서비스(점포-상권배후지)_2024년.csv",
        "2026년/서울시 상권분석서비스(점포-상권배후지).csv",
    ],
}
# 2026-08-09 발견(11-1d): '점포_수'는 프랜차이즈 제외값이라 신 스키마 '전체_점포_수'와
# 개념이 다름 -- '유사_업종_점포_수'가 진짜 대응 컬럼(4개 연도 전수 검증, 100% 일치).
STORE_COLUMN_ALIASES = {"유사_업종_점포_수": "전체_점포_수"}
# 점포-상권배후지.csv만 코드 컬럼명이 예외적으로 '상권_코드'(다른 3개 데이터셋은 '상권배후지_코드')
STORE_CODE_COL = {"상권": "상권_코드", "행정동": "행정동_코드", "상권배후지": "상권_코드"}

# ---------- 추정매출: 연도별 파일(코드 컬럼명은 전부 정상) ----------
SALES_YEAR_FILES = {
    unit: [
        f"2021/서울시_상권분석서비스(추정매출-{unit})_2021년.csv",
        f"2022/서울시_상권분석서비스(추정매출-{unit})_2022년.csv",
        f"2023/서울시_상권분석서비스(추정매출-{unit})_2023년.csv",
        f"2024/서울시 상권분석서비스(추정매출-{unit})_2024년.csv",
        f"2026/서울시 상권분석서비스(추정매출-{unit}).csv",
    ]
    for unit in UNITS
}
SALES_CODE_COL = {"상권": "상권_코드", "행정동": "행정동_코드", "상권배후지": "상권배후지_코드"}

# ---------- 유동인구·아파트: 단일 파일(21개 분기 이미 다 포함) ----------
FLOW_FILE = {u: f"서울시 상권분석서비스(길단위인구-{u}).csv" for u in UNITS}
APT_FILE = {u: f"서울시 상권분석서비스(아파트-{u}).csv" for u in UNITS}
FLOW_CODE_COL = {"상권": "상권_코드", "행정동": "행정동_코드", "상권배후지": "상권배후지_코드"}
APT_CODE_COL = FLOW_CODE_COL


def _read(path):
    try:
        return pd.read_csv(path, encoding="cp949")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="utf-8")


def load_store(unit):
    code_col = STORE_CODE_COL[unit]
    parts = []
    for rel in STORE_YEAR_FILES[unit]:
        df = _read(os.path.join(DATA, "점포", rel))
        df = df.rename(columns=STORE_COLUMN_ALIASES)
        df = df[df["서비스_업종_코드"].isin(INDUSTRY_CODES)]
        parts.append(df[["기준_년분기_코드", code_col, "서비스_업종_코드", "전체_점포_수"]])
    store = pd.concat(parts, ignore_index=True).drop_duplicates(
        subset=["기준_년분기_코드", code_col, "서비스_업종_코드"])
    agg = store.groupby(["기준_년분기_코드", code_col])["전체_점포_수"].sum().reset_index()
    return agg.rename(columns={code_col: "code", "전체_점포_수": "value"})


def load_sales(unit):
    code_col = SALES_CODE_COL[unit]
    parts = []
    for rel in SALES_YEAR_FILES[unit]:
        df = _read(os.path.join(DATA, "추정매출", rel))
        df = df[df["서비스_업종_코드"].isin(INDUSTRY_CODES)]
        parts.append(df[["기준_년분기_코드", code_col, "서비스_업종_코드", "당월_매출_금액"]])
    sales = pd.concat(parts, ignore_index=True).drop_duplicates(
        subset=["기준_년분기_코드", code_col, "서비스_업종_코드"])
    agg = sales.groupby(["기준_년분기_코드", code_col])["당월_매출_금액"].sum().reset_index()
    return agg.rename(columns={code_col: "code", "당월_매출_금액": "value"})


def load_flow(unit):
    code_col = FLOW_CODE_COL[unit]
    df = _read(os.path.join(DATA, "길단위인구", FLOW_FILE[unit]))
    return df[["기준_년분기_코드", code_col, "총_유동인구_수"]].rename(
        columns={code_col: "code", "총_유동인구_수": "value"})


def load_apt(unit):
    code_col = APT_CODE_COL[unit]
    df = _read(os.path.join(DATA, "아파트", APT_FILE[unit]))
    return df[["기준_년분기_코드", code_col, "아파트_평균_시가"]].rename(
        columns={code_col: "code", "아파트_평균_시가": "value"})


LOADERS = {"유동인구": load_flow, "아파트": load_apt, "추정매출": load_sales, "점포": load_store}
DATASET_ORDER = ["유동인구", "아파트", "추정매출", "점포"]


def common_quarters(per_unit_data):
    sets = [set(df["기준_년분기_코드"].unique()) for df in per_unit_data.values()]
    return sorted(set.intersection(*sets))


def quarterly_mean(df, quarters):
    df = df[df["기준_년분기_코드"].isin(quarters)]
    return df.groupby("기준_년분기_코드")["value"].mean()


def index100(series):
    base = series.iloc[0]
    return series / base * 100


# ========== (A) 3단위 개별 분기별 추세 ==========
def part_a():
    print("=" * 60, "\n(A) 3단위 개별 분기별 추세\n", "=" * 60)
    all_trend = {}
    for unit in UNITS:
        per_dataset = {name: LOADERS[name](unit) for name in DATASET_ORDER}
        quarters = common_quarters(per_dataset)
        print(f"\n[{unit}] 공통분기: {quarters[0]}~{quarters[-1]} ({len(quarters)}개)")
        trend = pd.DataFrame({name: quarterly_mean(df, quarters) for name, df in per_dataset.items()})
        trend = trend.sort_index()
        all_trend[unit] = trend
        trend.to_csv(os.path.join(OUT, f"seoul_trend_{unit}.csv"), encoding="utf-8-sig")
        print(trend.round(1).to_string())
    return all_trend


def plot_part_a(all_trend):
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), facecolor=SURFACE, sharey=True)
    for ax, unit in zip(axes, UNITS):
        ax.set_facecolor(SURFACE)
        trend = all_trend[unit]
        x = range(len(trend))
        for name, color in zip(DATASET_ORDER, CAT4):
            ax.plot(x, index100(trend[name]), color=color, linewidth=2, marker="o", markersize=2.5, label=name)
        ax.axhline(100, color=GRIDLINE, linewidth=1, zorder=0)
        ax.set_xticks(list(x)[::4])
        ax.set_xticklabels([str(q) for q in trend.index][::4], rotation=45, ha="right",
                             color=INK_MUTED, fontsize=7.5)
        ax.set_title(unit, color=INK_PRIMARY, fontsize=12, fontweight="bold")
        ax.tick_params(axis="y", length=0, labelcolor=INK_MUTED)
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)
        ax.spines["left"].set_color(GRIDLINE)
        ax.spines["bottom"].set_color(GRIDLINE)
        ax.grid(axis="y", color=GRIDLINE, linewidth=0.6, zorder=0)
    axes[0].set_ylabel("지수 (첫 분기=100)", color=INK_SECONDARY, fontsize=9.5)

    fig.text(0.06, 0.99, "유동인구·아파트·매출·점포 분기별 추세 (서울 전체, 단위별)",
              color=INK_PRIMARY, fontsize=14, fontweight="bold", ha="left", va="top")
    fig.text(0.06, 0.945, "2021Q1~2026Q1(21개 분기) · 각 데이터셋 첫 분기=100 지수화 · 임대료 제외",
              color=INK_MUTED, fontsize=9.5, ha="left", va="top")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, frameon=False, fontsize=9.5, ncol=4, labelcolor=INK_SECONDARY,
               loc="lower center", bbox_to_anchor=(0.5, -0.02))
    fig.tight_layout(rect=[0, 0.08, 1, 0.88])
    out = os.path.join(FIG_DIR, "seoul_trend_by_unit.png")
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print("saved:", out)


# ========== (B) crosswalk 겹침결합 "전체지역" 통합 추세 ==========
def load_codes():
    trdar = set(pd.read_csv(os.path.join(CROSSWALKS, "crosswalk_trdar_dong.csv"),
                             encoding="utf-8-sig")["TRDAR_CD"].astype(int))
    return sorted(trdar)


def build_combined_by_trdar(trdar_codes, quarters, dong_df, alley_df):
    """상권마다 (a) 걸친 행정동들의 면적가중평균, (b) 자기 배후지+겹치는 인접 배후지의
    겹침면적가중평균을 분기별로 계산한다. seoul_apartment_sales_multilevel.py의
    build_combined_income_by_trdar()를 값 종류에 상관없이 쓸 수 있도록 일반화한 버전."""
    tw = pd.read_csv(os.path.join(CROSSWALKS, "crosswalk_trdar_dong.csv"), encoding="utf-8-sig")
    tw = tw[tw["TRDAR_CD"].isin(trdar_codes)]
    ta = pd.read_csv(os.path.join(CROSSWALKS, "crosswalk_trdar_alley.csv"), encoding="utf-8-sig")
    ta = ta[ta["TRDAR_CD"].isin(trdar_codes) & ta["has_alley"]]
    aso = pd.read_csv(os.path.join(CROSSWALKS, "crosswalk_alley_self_overlap.csv"), encoding="utf-8-sig")

    dong_lookup = dong_df.set_index(["기준_년분기_코드", "code"])["value"].to_dict()
    alley_lookup = alley_df.set_index(["기준_년분기_코드", "code"])["value"].to_dict()

    trdar_dong_weights = {}
    for trdar, grp in tw.groupby("TRDAR_CD"):
        trdar_dong_weights[trdar] = list(grp[["ADSTRD_CD", "ratio_of_trdar"]].itertuples(index=False, name=None))

    trdar_alley_weights = {}
    ta_by_trdar = ta.groupby("TRDAR_CD")["ALLEY_TRDA"].first()
    for trdar, own_alley in ta_by_trdar.items():
        own_alley = int(own_alley)
        neigh = aso[(aso["ALLEY_TRDA_A"] == own_alley) | (aso["ALLEY_TRDA_B"] == own_alley)]
        weights = [(own_alley, 1.0)]
        for _, r in neigh.iterrows():
            other = int(r["ALLEY_TRDA_B"]) if r["ALLEY_TRDA_A"] == own_alley else int(r["ALLEY_TRDA_A"])
            ratio = r["ratio_of_a"] if r["ALLEY_TRDA_A"] == own_alley else r["ratio_of_b"]
            weights.append((other, float(ratio)))
        trdar_alley_weights[trdar] = weights

    rows = []
    for trdar in trdar_codes:
        dong_weights = trdar_dong_weights.get(trdar, [])
        alley_weights = trdar_alley_weights.get(trdar, [])
        for q in quarters:
            dvals, dwts = [], []
            for adstrd_cd, ratio in dong_weights:
                v = dong_lookup.get((q, int(adstrd_cd)))
                if v is not None:
                    dvals.append(v)
                    dwts.append(float(ratio))
            dong_val = float(np.average(dvals, weights=dwts)) if dvals else np.nan

            avals, awts = [], []
            for code, w in alley_weights:
                v = alley_lookup.get((q, code))
                if v is not None:
                    avals.append(v)
                    awts.append(w)
            alley_val = float(np.average(avals, weights=awts)) if avals else np.nan

            rows.append({"TRDAR_CD": trdar, "기준_년분기_코드": q, "행정동결합": dong_val, "배후지결합": alley_val})
    return pd.DataFrame(rows)


def part_b(trdar_codes):
    print("\n" + "=" * 60, "\n(B) crosswalk 겹침결합 전체지역 통합 추세\n", "=" * 60)
    trdar_data = {name: LOADERS[name]("상권") for name in DATASET_ORDER}
    dong_data = {name: LOADERS[name]("행정동") for name in DATASET_ORDER}
    alley_data = {name: LOADERS[name]("상권배후지") for name in DATASET_ORDER}
    quarters = common_quarters({**trdar_data, **dong_data, **alley_data})
    print(f"공통분기: {quarters[0]}~{quarters[-1]} ({len(quarters)}개), 상권 {len(trdar_codes)}개")

    combined_trends = {}
    for name in DATASET_ORDER:
        own = trdar_data[name][trdar_data[name]["code"].isin(trdar_codes)]
        combined = build_combined_by_trdar(trdar_codes, quarters, dong_data[name], alley_data[name])
        combined_csv = os.path.join(OUT, f"seoul_combined_{name}_by_trdar.csv")
        combined.to_csv(combined_csv, index=False, encoding="utf-8-sig")

        own_q = own[own["기준_년분기_코드"].isin(quarters)].groupby("기준_년분기_코드")["value"].mean()
        dong_q = combined.groupby("기준_년분기_코드")["행정동결합"].mean()
        alley_q = combined.groupby("기준_년분기_코드")["배후지결합"].mean()
        tr = pd.DataFrame({"상권자체": own_q, "행정동결합": dong_q, "배후지결합": alley_q}).sort_index()
        combined_trends[name] = tr
        tr.to_csv(os.path.join(OUT, f"seoul_trend_combined_{name}.csv"), encoding="utf-8-sig")
        print(f"\n[{name}]")
        print(tr.round(1).to_string())

    return combined_trends


def plot_part_b(combined_trends):
    fig, axes = plt.subplots(2, 2, figsize=(13, 9), facecolor=SURFACE)
    for ax, name in zip(axes.flat, DATASET_ORDER):
        ax.set_facecolor(SURFACE)
        tr = combined_trends[name]
        x = range(len(tr))
        for col, color in zip(["상권자체", "행정동결합", "배후지결합"], CAT3):
            ax.plot(x, index100(tr[col]), color=color, linewidth=2, marker="o", markersize=2.5, label=col)
        ax.axhline(100, color=GRIDLINE, linewidth=1, zorder=0)
        ax.set_xticks(list(x)[::4])
        ax.set_xticklabels([str(q) for q in tr.index][::4], rotation=45, ha="right",
                             color=INK_MUTED, fontsize=7.5)
        ax.set_title(name, color=INK_PRIMARY, fontsize=12, fontweight="bold")
        ax.tick_params(axis="y", length=0, labelcolor=INK_MUTED)
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)
        ax.spines["left"].set_color(GRIDLINE)
        ax.spines["bottom"].set_color(GRIDLINE)
        ax.grid(axis="y", color=GRIDLINE, linewidth=0.6, zorder=0)

    fig.text(0.06, 0.99, "crosswalk 겹침결합 기준 서울 전체 분기별 추세 (상권자체 vs 행정동결합 vs 배후지결합)",
              color=INK_PRIMARY, fontsize=14, fontweight="bold", ha="left", va="top")
    fig.text(0.06, 0.965, "2021Q1~2026Q1(21개 분기) · 상권 1,650개 기준 · 각 계열 첫 분기=100 지수화",
              color=INK_MUTED, fontsize=9.5, ha="left", va="top")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, frameon=False, fontsize=9.5, ncol=3, labelcolor=INK_SECONDARY,
               loc="lower center", bbox_to_anchor=(0.5, -0.01))
    fig.tight_layout(rect=[0, 0.05, 1, 0.93])
    out = os.path.join(FIG_DIR, "seoul_trend_combined.png")
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print("saved:", out)


def main():
    all_trend = part_a()
    plot_part_a(all_trend)

    trdar_codes = load_codes()
    combined_trends = part_b(trdar_codes)
    plot_part_b(combined_trends)


if __name__ == "__main__":
    main()
