"""아파트(소득 대리지표) x 업종별 매출 상관관계 — 서울 전체, 상권/상권배후지/행정동 세 단위 비교.

scripts/songpa_apartment_sales_multilevel.py를 송파구 한정 없이 서울 전체로 일반화한 버전
(원본은 output/songpa/에 그대로 남겨 송파구 검증 결과와 비교 가능하게 유지).

두 갈래로 분석한다:
(A) 세 단위를 각각 독립적으로 비교: 상권 자체 vs 상권배후지 자체 vs 행정동 자체.
(B) 상권 기준으로 "겹치는 영역을 결합"한 소득지표(행정동 겹침결합/배후지 겹침결합)를 만들어
    상권 자체 값과 비교.

분기는 추정매출과 아파트 데이터의 공통 분기(20251~20254, 20261)만 사용.
크로스워크(crosswalk_trdar_dong.csv 등)는 서울 전체 기준으로 이미 만들어져 있어 별도
자치구 필터 없이 그대로 재사용한다.
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
CROSSWALKS = os.path.join(ROOT, "output", "crosswalks")
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

INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
SURFACE = "#fcfcfb"
GRIDLINE = "#e1e0d9"
RED = "#e34948"
GRAY_MID = "#f0efec"
BLUE = "#2a78d6"
DIVERGING_CMAP = LinearSegmentedColormap.from_list("diverging_blue_red", [RED, GRAY_MID, BLUE])


def common_quarters():
    sales_q = set(pd.read_csv(os.path.join(DATA, "추정매출", "2026", "서울시 상권분석서비스(추정매출-상권).csv"),
                               encoding="cp949", usecols=["기준_년분기_코드"])["기준_년분기_코드"].unique())
    apt_q = set(pd.read_csv(os.path.join(DATA, "아파트", "서울시 상권분석서비스(아파트-상권).csv"),
                             encoding="cp949", usecols=["기준_년분기_코드"])["기준_년분기_코드"].unique())
    return sorted(sales_q & apt_q)


def load_codes():
    """서울 전체 상권/상권배후지/행정동 코드 전부(자치구 필터 없음)."""
    trdar = set(pd.read_csv(os.path.join(CROSSWALKS, "crosswalk_trdar_dong.csv"),
                             encoding="utf-8-sig")["TRDAR_CD"].astype(int))
    alley = set(pd.read_csv(os.path.join(CROSSWALKS, "crosswalk_trdar_alley.csv"),
                             encoding="utf-8-sig").query("has_alley")["ALLEY_TRDA"].astype(int))
    dong_df = pd.read_csv(os.path.join(DATA, "영역", "행정동", "서울시 상권분석서비스(영역-행정동).csv"), encoding="cp949")
    dong = set(dong_df["행정동_코드"].astype(int))
    return trdar, alley, dong


def load_level_panel(unit_key, apt_fname, sales_fname, codes, quarters):
    apt = pd.read_csv(os.path.join(DATA, "아파트", apt_fname), encoding="cp949")
    apt = apt[apt["기준_년분기_코드"].isin(quarters) & apt[unit_key].isin(codes)]
    apt = apt[["기준_년분기_코드", unit_key, "아파트_평균_면적", "아파트_평균_시가"]]

    # data/추정매출/이 연도별 폴더(2021~2026)로 재구성됨 — 필요 분기(20251~20254, 20261)는 '2026' 폴더 파일에 있음
    sales = pd.read_csv(os.path.join(DATA, "추정매출", "2026", sales_fname), encoding="cp949")
    sales = sales[
        sales["기준_년분기_코드"].isin(quarters) & sales[unit_key].isin(codes)
        & sales["서비스_업종_코드"].isin(INDUSTRY_CODES)
    ]
    pivot = sales.pivot_table(index=["기준_년분기_코드", unit_key], columns="서비스_업종_코드",
                               values="당월_매출_금액", aggfunc="sum")
    pivot = pivot.reindex(columns=list(INDUSTRY_CODES.keys())).rename(columns=INDUSTRY_CODES).reset_index()

    panel = apt.merge(pivot, on=["기준_년분기_코드", unit_key], how="inner").dropna(subset=["아파트_평균_시가"])
    return panel


def pooled_corr(panel, value_col="아파트_평균_시가"):
    log_p = panel.copy()
    cols = [value_col] + list(INDUSTRY_CODES.values())
    for c in cols:
        log_p[c] = np.log1p(log_p[c].fillna(0))
    return pd.Series({biz: log_p[value_col].corr(log_p[biz]) for biz in INDUSTRY_CODES.values()})


def build_combined_income_by_trdar(trdar_codes, quarters):
    """상권마다 (a) 걸친 행정동들 면적가중평균 아파트시가, (b) 배후지+겹치는인접배후지
    겹침면적가중평균 아파트시가를 분기별로 계산."""
    tw = pd.read_csv(os.path.join(CROSSWALKS, "crosswalk_trdar_dong.csv"), encoding="utf-8-sig")
    tw = tw[tw["TRDAR_CD"].isin(trdar_codes)]
    ta = pd.read_csv(os.path.join(CROSSWALKS, "crosswalk_trdar_alley.csv"), encoding="utf-8-sig")
    ta = ta[ta["TRDAR_CD"].isin(trdar_codes) & ta["has_alley"]]
    aso = pd.read_csv(os.path.join(CROSSWALKS, "crosswalk_alley_self_overlap.csv"), encoding="utf-8-sig")

    dong_codes_needed = set(tw["ADSTRD_CD"].unique())
    own_alley_codes = set(ta["ALLEY_TRDA"].astype(int))
    neighbor_rows = aso[aso["ALLEY_TRDA_A"].isin(own_alley_codes) | aso["ALLEY_TRDA_B"].isin(own_alley_codes)]
    alley_codes_needed = own_alley_codes | set(neighbor_rows["ALLEY_TRDA_A"].astype(int)) | set(
        neighbor_rows["ALLEY_TRDA_B"].astype(int))

    dong_apt = pd.read_csv(os.path.join(DATA, "아파트", "서울시 상권분석서비스(아파트-행정동).csv"), encoding="cp949")
    dong_apt = dong_apt[dong_apt["기준_년분기_코드"].isin(quarters) & dong_apt["행정동_코드"].isin(dong_codes_needed)]
    dong_lookup = dong_apt.set_index(["기준_년분기_코드", "행정동_코드"])["아파트_평균_시가"].to_dict()

    alley_apt = pd.read_csv(os.path.join(DATA, "아파트", "서울시 상권분석서비스(아파트-상권배후지).csv"), encoding="cp949")
    alley_apt = alley_apt[alley_apt["기준_년분기_코드"].isin(quarters) & alley_apt["상권배후지_코드"].isin(alley_codes_needed)]
    alley_lookup = alley_apt.set_index(["기준_년분기_코드", "상권배후지_코드"])["아파트_평균_시가"].to_dict()

    # 상권별 가중치는 분기와 무관하므로 미리 한 번만 계산해둔다 (서울 전체 규모라 이 최적화가 체감됨)
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
            dong_income = float(np.average(dvals, weights=dwts)) if dvals else np.nan

            avals, awts = [], []
            for code, w in alley_weights:
                v = alley_lookup.get((q, code))
                if v is not None:
                    avals.append(v)
                    awts.append(w)
            alley_income = float(np.average(avals, weights=awts)) if avals else np.nan

            rows.append({"TRDAR_CD": trdar, "기준_년분기_코드": q,
                         "행정동결합_아파트시가": dong_income, "배후지결합_아파트시가": alley_income})
    return pd.DataFrame(rows)


def main():
    quarters = common_quarters()
    trdar_codes, alley_codes, dong_codes = load_codes()
    print(f"공통 분기: {quarters}")
    print(f"서울 전체 상권 {len(trdar_codes)}개 / 배후지 {len(alley_codes)}개 / 행정동 {len(dong_codes)}개\n")

    # === (A) 세 단위 독립 비교 ===
    trdar_panel = load_level_panel("상권_코드", "서울시 상권분석서비스(아파트-상권).csv",
                                   "서울시 상권분석서비스(추정매출-상권).csv", trdar_codes, quarters)
    alley_panel = load_level_panel("상권배후지_코드", "서울시 상권분석서비스(아파트-상권배후지).csv",
                                    "서울시 상권분석서비스(추정매출-상권배후지).csv", alley_codes, quarters)
    dong_panel = load_level_panel("행정동_코드", "서울시 상권분석서비스(아파트-행정동).csv",
                                   "서울시 상권분석서비스(추정매출-행정동).csv", dong_codes, quarters)

    print(f"상권 패널: {len(trdar_panel)}행 / 배후지 패널: {len(alley_panel)}행 / 행정동 패널: {len(dong_panel)}행\n")

    compare = pd.DataFrame({
        "상권 자체": pooled_corr(trdar_panel),
        "상권배후지 자체": pooled_corr(alley_panel),
        "행정동 자체": pooled_corr(dong_panel),
    })
    compare_csv = os.path.join(SEOUL_DIR, "seoul_apartment_sales_by_unit.csv")
    compare.to_csv(compare_csv, encoding="utf-8-sig")
    print("=== (A) 단위별 독립 비교: 아파트 평균시가 x 업종별 매출 상관계수 ===")
    print(compare.round(2).to_string())
    print(f"{compare_csv} 저장\n")

    fig, ax = plt.subplots(figsize=(7, 6))
    im = ax.imshow(compare.values, cmap=DIVERGING_CMAP, vmin=-0.5, vmax=0.5, aspect="auto")
    ax.set_xticks(range(len(compare.columns)))
    ax.set_xticklabels(compare.columns, color=INK_SECONDARY, fontsize=10)
    ax.set_yticks(range(len(compare.index)))
    ax.set_yticklabels(compare.index, color=INK_SECONDARY, fontsize=10)
    for i in range(compare.shape[0]):
        for j in range(compare.shape[1]):
            v = compare.values[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                     color="white" if abs(v) > 0.3 else INK_PRIMARY, fontsize=9)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title("서울 전체: 단위별(상권/배후지/행정동) 아파트시가-매출 상관계수", color=INK_PRIMARY, fontsize=12, pad=12)
    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.ax.tick_params(colors=INK_MUTED)
    cbar.outline.set_visible(False)
    fig.tight_layout()
    out1 = os.path.join(FIG_DIR, "seoul_apartment_sales_by_unit.png")
    fig.savefig(out1, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out1} 저장\n")

    # === (B) 겹침 결합 소득지표 vs 상권 자체 ===
    combined = build_combined_income_by_trdar(trdar_codes, quarters)
    combined_csv = os.path.join(SEOUL_DIR, "seoul_combined_income_by_trdar.csv")
    combined.to_csv(combined_csv, index=False, encoding="utf-8-sig")
    print(f"{combined_csv} 저장")

    combined_renamed = combined.rename(columns={"TRDAR_CD": "상권_코드"})
    trdar_full = trdar_panel.merge(combined_renamed, on=["상권_코드", "기준_년분기_코드"], how="left")

    log_full = trdar_full.copy()
    value_cols = ["아파트_평균_시가", "행정동결합_아파트시가", "배후지결합_아파트시가"]
    for c in value_cols + list(INDUSTRY_CODES.values()):
        log_full[c] = np.log1p(log_full[c].fillna(0)) if c in log_full else np.nan

    combo_compare = pd.DataFrame({
        "상권 자체": {biz: log_full["아파트_평균_시가"].corr(log_full[biz]) for biz in INDUSTRY_CODES.values()},
        "행정동 겹침결합": {biz: log_full["행정동결합_아파트시가"].corr(log_full[biz]) for biz in INDUSTRY_CODES.values()},
        "배후지 겹침결합": {biz: log_full["배후지결합_아파트시가"].corr(log_full[biz]) for biz in INDUSTRY_CODES.values()},
    })
    combo_csv = os.path.join(SEOUL_DIR, "seoul_apartment_sales_combined_vs_own.csv")
    combo_compare.to_csv(combo_csv, encoding="utf-8-sig")
    print("\n=== (B) 상권 자체 vs 겹침결합 소득지표: 매출 상관계수 비교 ===")
    print(combo_compare.round(2).to_string())
    print(f"{combo_csv} 저장\n")

    fig, ax = plt.subplots(figsize=(7, 6))
    im = ax.imshow(combo_compare.values, cmap=DIVERGING_CMAP, vmin=-0.5, vmax=0.5, aspect="auto")
    ax.set_xticks(range(len(combo_compare.columns)))
    ax.set_xticklabels(combo_compare.columns, color=INK_SECONDARY, fontsize=10)
    ax.set_yticks(range(len(combo_compare.index)))
    ax.set_yticklabels(combo_compare.index, color=INK_SECONDARY, fontsize=10)
    for i in range(combo_compare.shape[0]):
        for j in range(combo_compare.shape[1]):
            v = combo_compare.values[i, j]
            if np.isnan(v):
                continue
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                     color="white" if abs(v) > 0.3 else INK_PRIMARY, fontsize=9)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title("서울 전체: 상권 자체 vs 겹침결합 소득지표의 매출 설명력 비교", color=INK_PRIMARY, fontsize=11, pad=12)
    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.ax.tick_params(colors=INK_MUTED)
    cbar.outline.set_visible(False)
    fig.tight_layout()
    out2 = os.path.join(FIG_DIR, "seoul_apartment_sales_combined_vs_own.png")
    fig.savefig(out2, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out2} 저장")


if __name__ == "__main__":
    main()
