"""송파구 상권: 지금까지 따로 분석한 인구(유동/상주/직장)·아파트(소득)·점포·상권변화지표를
하나의 피처 테이블로 합쳐 업종별 매출을 다변량으로 설명하는 회귀분석.

목적: 1:1 상관관계로는 "이 지표 하나가 매출과 얼마나 관련있나"만 보였는데, 여러 지표를
동시에 넣었을 때 (a) 어떤 지표가 상대적으로 더 중요한지, (b) 여러 지표를 합치면 설명력
(R^2)이 단일 지표보다 얼마나 좋아지는지 확인한다.

방법(가정):
- sklearn/statsmodels 미설치 상태라 numpy로 표준화(z-score) 다중선형회귀를 직접 구현.
  모든 피처와 타깃을 표준화했기 때문에 회귀계수(표준화 베타)는 서로 다른 단위의 피처라도
  크기를 직접 비교할 수 있다.
- 상권변화지표(gap_survive/gap_close)는 업종 구분이 없는 상권 단위 값이라, 그 상권의
  업종 10개 행 모두에 동일한 값을 broadcast join한다 (.claude/CLAUDE.md 참고).
- 인구/아파트도 상권 단위(업종 무관) 값이라 마찬가지로 broadcast join.
- 데이터셋 7개의 공통 분기(20251~20254, 20261)만 사용, 상권-분기-업종 단위 패널로 구성.
- 유동/상주/직장인구는 서로 상관이 매우 높다는 게 이미 확인된 사실이므로(과거 검증),
  다중공선성을 명시적으로 점검한다.
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

INDUSTRY_CODES = {
    "CS100001": "한식음식점", "CS100002": "중식음식점", "CS100003": "일식음식점",
    "CS100004": "양식음식점", "CS100005": "제과점", "CS100006": "패스트푸드점",
    "CS100007": "치킨전문점", "CS100008": "분식전문점", "CS100009": "호프-간이주점",
    "CS100010": "커피-음료",
}
FEATURES = ["유동인구", "상주인구", "직장인구", "아파트_평균_시가", "아파트_평균_면적",
            "전체_점포_수", "개업_율", "폐업_률", "프랜차이즈_비율", "gap_survive", "gap_close"]
LOG_FEATURES = {"유동인구", "상주인구", "직장인구", "아파트_평균_시가", "아파트_평균_면적", "전체_점포_수"}

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
    paths = {
        # data/추정매출/·data/점포/ 모두 연도별 폴더로 재구성됨 — 필요 분기(20251~20254, 20261)는
        # 모두 '2026'/'2026년' 폴더 파일에 있음
        "추정매출": "추정매출/2026/서울시 상권분석서비스(추정매출-상권).csv",
        "아파트": "아파트/서울시 상권분석서비스(아파트-상권).csv",
        "점포": "점포/2026년/서울시 상권분석서비스(점포-상권).csv",
        "길단위인구": "길단위인구/서울시 상권분석서비스(길단위인구-상권).csv",
        "상주인구": "상주인구/서울시 상권분석서비스(상주인구-상권).csv",
        "직장인구": "직장인구/서울시 상권분석서비스(직장인구-상권).csv",
        "상권변화지표": "상권변화지표/서울시 상권분석서비스(상권변화지표-상권).csv",
    }
    sets = []
    for rel in paths.values():
        s = set(pd.read_csv(os.path.join(DATA, rel), encoding="cp949", usecols=["기준_년분기_코드"])["기준_년분기_코드"])
        sets.append(s)
    return sorted(set.intersection(*sets))


def load_songpa_trdar_codes():
    return set(pd.read_csv(os.path.join(SONGPA_DIR, "songpa_trdar_membership.csv"),
                            encoding="utf-8-sig")["TRDAR_CD"].astype(int))


def load_area_scalar(rel_path, value_col, codes, quarters, out_name):
    df = pd.read_csv(os.path.join(DATA, rel_path), encoding="cp949")
    df = df[df["기준_년분기_코드"].isin(quarters) & df["상권_코드"].isin(codes)]
    return df[["기준_년분기_코드", "상권_코드", value_col]].rename(columns={value_col: out_name})


def build_panel(codes, quarters):
    flow = load_area_scalar("길단위인구/서울시 상권분석서비스(길단위인구-상권).csv", "총_유동인구_수", codes, quarters, "유동인구")
    resident = load_area_scalar("상주인구/서울시 상권분석서비스(상주인구-상권).csv", "총_상주인구_수", codes, quarters, "상주인구")
    worker = load_area_scalar("직장인구/서울시 상권분석서비스(직장인구-상권).csv", "총_직장_인구_수", codes, quarters, "직장인구")

    apt = pd.read_csv(os.path.join(DATA, "아파트/서울시 상권분석서비스(아파트-상권).csv"), encoding="cp949")
    apt = apt[apt["기준_년분기_코드"].isin(quarters) & apt["상권_코드"].isin(codes)]
    apt = apt[["기준_년분기_코드", "상권_코드", "아파트_평균_시가", "아파트_평균_면적"]]

    ci = pd.read_csv(os.path.join(DATA, "상권변화지표/서울시 상권분석서비스(상권변화지표-상권).csv"), encoding="cp949")
    ci = ci[ci["기준_년분기_코드"].isin(quarters) & ci["상권_코드"].isin(codes)].copy()
    ci["gap_survive"] = ci["운영_영업_개월_평균"] - ci["서울_운영_영업_개월_평균"]
    ci["gap_close"] = ci["폐업_영업_개월_평균"] - ci["서울_폐업_영업_개월_평균"]
    ci = ci[["기준_년분기_코드", "상권_코드", "gap_survive", "gap_close"]]

    base = flow.merge(resident, on=["기준_년분기_코드", "상권_코드"]) \
        .merge(worker, on=["기준_년분기_코드", "상권_코드"]) \
        .merge(apt, on=["기준_년분기_코드", "상권_코드"], how="left") \
        .merge(ci, on=["기준_년분기_코드", "상권_코드"], how="left")

    store = pd.read_csv(os.path.join(DATA, "점포/2026년/서울시 상권분석서비스(점포-상권).csv"), encoding="cp949")
    store = store[
        store["기준_년분기_코드"].isin(quarters) & store["상권_코드"].isin(codes)
        & store["서비스_업종_코드"].isin(INDUSTRY_CODES)
    ].copy()
    store["프랜차이즈_비율"] = store["프랜차이즈_점포_수"] / store["전체_점포_수"].replace(0, np.nan)
    store["업종명"] = store["서비스_업종_코드"].map(INDUSTRY_CODES)
    store = store[["기준_년분기_코드", "상권_코드", "업종명", "전체_점포_수", "개업_율", "폐업_률", "프랜차이즈_비율"]]

    sales = pd.read_csv(os.path.join(DATA, "추정매출/2026/서울시 상권분석서비스(추정매출-상권).csv"), encoding="cp949")
    sales = sales[
        sales["기준_년분기_코드"].isin(quarters) & sales["상권_코드"].isin(codes)
        & sales["서비스_업종_코드"].isin(INDUSTRY_CODES)
    ].copy()
    sales["업종명"] = sales["서비스_업종_코드"].map(INDUSTRY_CODES)
    sales = sales[["기준_년분기_코드", "상권_코드", "업종명", "당월_매출_금액"]]

    panel = store.merge(sales, on=["기준_년분기_코드", "상권_코드", "업종명"]) \
        .merge(base, on=["기준_년분기_코드", "상권_코드"], how="left")
    return panel.dropna(subset=["아파트_평균_시가"])


def standardized_ols(X, y):
    """z-score 표준화 후 numpy lstsq로 다중선형회귀. sklearn/statsmodels 미설치 환경 대응."""
    Xz = (X - X.mean(axis=0)) / X.std(axis=0, ddof=0)
    yz = (y - y.mean()) / y.std(ddof=0)
    design = np.column_stack([np.ones(len(Xz)), Xz])
    coef, _, _, _ = np.linalg.lstsq(design, yz, rcond=None)
    y_pred = design @ coef
    ss_res = np.sum((yz - y_pred) ** 2)
    ss_tot = np.sum((yz - yz.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot
    return coef[1:], r2


def main():
    quarters = common_quarters()
    print(f"공통 분기: {quarters}")
    codes = load_songpa_trdar_codes()
    panel = build_panel(codes, quarters)
    print(f"패널 크기: {len(panel)}행 (상권-분기-업종)\n")

    panel_csv = os.path.join(SONGPA_DIR, "songpa_multivariate_panel.csv")
    panel.to_csv(panel_csv, index=False, encoding="utf-8-sig")
    print(f"{panel_csv} 저장\n")

    log_panel = panel.copy()
    for col in list(LOG_FEATURES) + ["당월_매출_금액"]:
        log_panel[col] = np.log1p(log_panel[col].fillna(0))

    # === 예측변수 간 다중공선성 점검 ===
    predictor_corr = log_panel[FEATURES].corr()
    predictor_corr_csv = os.path.join(SONGPA_DIR, "songpa_multivariate_predictor_corr.csv")
    predictor_corr.to_csv(predictor_corr_csv, encoding="utf-8-sig")
    print("=== 예측변수 간 상관계수 (다중공선성 점검) ===")
    print(predictor_corr.round(2).to_string())
    print(f"{predictor_corr_csv} 저장\n")

    # === 업종별 표준화 다중회귀 ===
    coef_rows = {}
    r2_compare = []
    for biz in INDUSTRY_CODES.values():
        sub = log_panel[log_panel["업종명"] == biz].dropna(subset=FEATURES + ["당월_매출_금액"])
        X = sub[FEATURES].values
        y = sub["당월_매출_금액"].values
        coefs, r2 = standardized_ols(X, y)
        coef_rows[biz] = dict(zip(FEATURES, coefs))

        single_r2 = max(sub[f].corr(sub["당월_매출_금액"]) ** 2 for f in FEATURES)
        r2_compare.append({"업종": biz, "다변량_R2": r2, "최고단일지표_R2": single_r2, "n": len(sub)})

    coef_df = pd.DataFrame(coef_rows).T[FEATURES]
    coef_csv = os.path.join(SONGPA_DIR, "songpa_multivariate_standardized_coef.csv")
    coef_df.to_csv(coef_csv, encoding="utf-8-sig")
    print("=== 업종별 표준화 회귀계수 ===")
    print(coef_df.round(2).to_string())
    print(f"{coef_csv} 저장\n")

    r2_df = pd.DataFrame(r2_compare).set_index("업종")
    r2_csv = os.path.join(SONGPA_DIR, "songpa_multivariate_r2_compare.csv")
    r2_df.to_csv(r2_csv, encoding="utf-8-sig")
    print("=== 다변량 R^2 vs 최고 단일지표 R^2 ===")
    print(r2_df.round(3).to_string())
    print(f"{r2_csv} 저장\n")

    # === 시각화 1: 표준화 회귀계수 히트맵 ===
    fig, ax = plt.subplots(figsize=(9, 6))
    im = ax.imshow(coef_df.values, cmap=DIVERGING_CMAP, vmin=-0.6, vmax=0.6, aspect="auto")
    ax.set_xticks(range(len(coef_df.columns)))
    ax.set_xticklabels(coef_df.columns, rotation=35, ha="right", color=INK_SECONDARY, fontsize=9)
    ax.set_yticks(range(len(coef_df.index)))
    ax.set_yticklabels(coef_df.index, color=INK_SECONDARY, fontsize=10)
    for i in range(coef_df.shape[0]):
        for j in range(coef_df.shape[1]):
            v = coef_df.values[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                     color="white" if abs(v) > 0.35 else INK_PRIMARY, fontsize=8)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title("송파구: 업종별 표준화 다중회귀계수 (매출 설명, log1p)", color=INK_PRIMARY, fontsize=12, pad=12)
    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.ax.tick_params(colors=INK_MUTED)
    cbar.outline.set_visible(False)
    fig.tight_layout()
    out1 = os.path.join(FIG_DIR, "songpa_multivariate_coef_heatmap.png")
    fig.savefig(out1, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out1} 저장")

    # === 시각화 2: R^2 비교 (다변량 vs 최고 단일지표) ===
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.set_facecolor(SURFACE)
    x = np.arange(len(r2_df))
    width = 0.35
    ax.bar(x - width / 2, r2_df["다변량_R2"], width, color=BLUE, label="다변량 회귀 R²")
    ax.bar(x + width / 2, r2_df["최고단일지표_R2"], width, color=ORANGE, label="최고 단일지표 R²")
    ax.set_xticks(x)
    ax.set_xticklabels(r2_df.index, rotation=30, ha="right", color=INK_SECONDARY, fontsize=9)
    ax.set_ylabel("R²", color=INK_SECONDARY, fontsize=10)
    ax.set_title("송파구: 다변량 회귀 R² vs 최고 단일지표 R² 비교", color=INK_PRIMARY, fontsize=12)
    ax.tick_params(colors=INK_MUTED)
    ax.legend(frameon=False, labelcolor=INK_SECONDARY)
    ax.grid(axis="y", color=GRIDLINE, linewidth=1)
    ax.set_axisbelow(True)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color(GRIDLINE)
    fig.tight_layout()
    out2 = os.path.join(FIG_DIR, "songpa_multivariate_r2_compare.png")
    fig.savefig(out2, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out2} 저장")

    # === 시각화 3: 예측변수 간 상관계수(다중공선성) 히트맵 ===
    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.imshow(predictor_corr.values, cmap=DIVERGING_CMAP, vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(len(predictor_corr.columns)))
    ax.set_xticklabels(predictor_corr.columns, rotation=40, ha="right", color=INK_SECONDARY, fontsize=8)
    ax.set_yticks(range(len(predictor_corr.index)))
    ax.set_yticklabels(predictor_corr.index, color=INK_SECONDARY, fontsize=8)
    for i in range(predictor_corr.shape[0]):
        for j in range(predictor_corr.shape[1]):
            v = predictor_corr.values[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                     color="white" if abs(v) > 0.5 else INK_PRIMARY, fontsize=7)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title("송파구: 예측변수 간 상관계수 (다중공선성 점검)", color=INK_PRIMARY, fontsize=12, pad=12)
    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.ax.tick_params(colors=INK_MUTED)
    cbar.outline.set_visible(False)
    fig.tight_layout()
    out3 = os.path.join(FIG_DIR, "songpa_multivariate_predictor_corr.png")
    fig.savefig(out3, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out3} 저장")


if __name__ == "__main__":
    main()
