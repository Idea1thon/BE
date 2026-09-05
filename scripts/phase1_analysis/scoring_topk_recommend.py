"""
24장: 업종을 입력하면 스코어링으로 top-k 상권을 추천하고, F1·CV로 검증까지 하는 최종 모델
(사용자 요청, 2026-08-09).

설계 원칙 (앞선 장들의 검증 결과를 그대로 반영):
1. 피처는 23장에서 "진짜 시계열"로 판정된 데이터셋만 사용 -- 유동인구·점포·추정매출·상권변화지표.
   아파트·상주인구·직장인구는 계단식이라 제외(memory: stepwise_low_frequency_datasets),
   임대료도 17-6에서 고유 기여 없음이 확인돼 제외(memory: rent_excluded_from_scoring).
2. **시간 분할**: 피처는 20241~20244(4개 분기)에서만, 정답은 20251~20261(5개 분기)에서만 만든다.
   같은 기간 데이터로 학습하고 같은 기간을 맞히면 F1이 부풀려지므로, "과거로 미래를 예측"하는
   구조로 강제한다.
3. **정답 2종을 각각 따로 평가** (사용자 지정):
   - A안 "이미 잘되는 상권": 정답기간 해당 업종 매출 상위 20%
   - B안 "앞으로 유망한 상권": 정답기간 매출 초과변화율(서울 평균 대비) 상위 20%
     초과변화율 = (자기 매출 변화율) - (같은 업종 서울 전체 평균 변화율) -- 19장에서 쓴 방식 재사용.
4. **5-fold CV**: 상권을 5등분해 4개로 가중치를 학습하고 나머지 1개에서 top-k를 뽑아 채점.
   폴드마다 Precision@k / Recall@k / F1@k를 구하고 평균±표준편차로 보고한다.
5. **랜덤 베이스라인 동시 보고**: F1 숫자만으로는 좋은지 알 수 없다. 정답이 상위 20%이므로
   무작위로 k개를 찍어도 Precision은 약 0.2가 나온다 -- 이 값과 비교해야 의미가 있다(lift).

가중치는 표준화(z-score) 다중선형회귀 계수로 학습한다(sklearn 미설치 환경이라 numpy lstsq 사용,
7장·17장과 동일한 방식). 표준화했으므로 계수 크기를 서로 다른 단위의 지표끼리 바로 비교 가능.
"""
import argparse
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "output", "scoring")
os.makedirs(OUT, exist_ok=True)

INDUSTRY_CODES = {
    "CS100001": "한식음식점", "CS100002": "중식음식점", "CS100003": "일식음식점",
    "CS100004": "양식음식점", "CS100005": "제과점", "CS100006": "패스트푸드점",
    "CS100007": "치킨전문점", "CS100008": "분식전문점", "CS100009": "호프-간이주점",
    "CS100010": "커피-음료",
}
NAME_TO_CODE = {v: k for k, v in INDUSTRY_CODES.items()}

FEATURE_QUARTERS = [20241, 20242, 20243, 20244]
LABEL_QUARTERS = [20251, 20252, 20253, 20254, 20261]

SALES_FILES = [
    "2024/서울시 상권분석서비스(추정매출-상권)_2024년.csv",
    "2026/서울시 상권분석서비스(추정매출-상권).csv",
]
STORE_FILES = [
    "2024년/서울시 상권분석서비스(점포-상권)_2024년.csv",
    "2026년/서울시 상권분석서비스(점포-상권).csv",
]
# 11-1d: 2024년 파일의 '점포_수'는 프랜차이즈 제외값 -- '유사_업종_점포_수'가 신 스키마 '전체_점포_수'에 대응
STORE_ALIASES = {"유사_업종_점포_수": "전체_점포_수"}

FEATURE_COLS = ["유동인구", "과거매출", "전체_점포_수", "개업_율", "폐업_률", "프랜차이즈_비율",
                 "gap_survive", "gap_close"]


def _read(path):
    try:
        return pd.read_csv(path, encoding="cp949")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="utf-8")


def load_sales(code):
    parts = []
    for rel in SALES_FILES:
        df = _read(os.path.join(DATA, "추정매출", rel))
        df = df[df["서비스_업종_코드"] == code]
        parts.append(df[["기준_년분기_코드", "상권_코드", "당월_매출_금액"]])
    return pd.concat(parts, ignore_index=True).drop_duplicates(subset=["기준_년분기_코드", "상권_코드"])


def load_store(code):
    parts = []
    for rel in STORE_FILES:
        df = _read(os.path.join(DATA, "점포", rel)).rename(columns=STORE_ALIASES)
        df = df[df["서비스_업종_코드"] == code]
        parts.append(df[["기준_년분기_코드", "상권_코드", "전체_점포_수", "프랜차이즈_점포_수",
                          "개업_율", "폐업_률"]])
    return pd.concat(parts, ignore_index=True).drop_duplicates(subset=["기준_년분기_코드", "상권_코드"])


def load_flow():
    df = _read(os.path.join(DATA, "길단위인구", "서울시 상권분석서비스(길단위인구-상권).csv"))
    return df[["기준_년분기_코드", "상권_코드", "총_유동인구_수"]]


def load_change_indicator():
    df = _read(os.path.join(DATA, "상권변화지표", "서울시 상권분석서비스(상권변화지표-상권).csv"))
    df = df.copy()
    # gap_survive/gap_close: 지역 평균 - 서울 평균 (양수일수록 오래 버팀). 4장·16장과 동일 정의.
    df["gap_survive"] = df["운영_영업_개월_평균"] - df["서울_운영_영업_개월_평균"]
    df["gap_close"] = df["폐업_영업_개월_평균"] - df["서울_폐업_영업_개월_평균"]
    return df[["기준_년분기_코드", "상권_코드", "gap_survive", "gap_close", "상권_변화_지표"]]


def build_panel(code):
    sales, store, flow, ci = load_sales(code), load_store(code), load_flow(), load_change_indicator()

    def period_mean(df, quarters, cols, how="mean"):
        d = df[df["기준_년분기_코드"].isin(quarters)]
        return d.groupby("상권_코드")[cols].agg(how)

    # ---- 피처 기간(20241~20244) ----
    f_sales = period_mean(sales, FEATURE_QUARTERS, ["당월_매출_금액"]).rename(columns={"당월_매출_금액": "과거매출"})
    f_store = period_mean(store, FEATURE_QUARTERS, ["전체_점포_수", "프랜차이즈_점포_수", "개업_율", "폐업_률"])
    f_flow = period_mean(flow, FEATURE_QUARTERS, ["총_유동인구_수"]).rename(columns={"총_유동인구_수": "유동인구"})
    f_ci = period_mean(ci, FEATURE_QUARTERS, ["gap_survive", "gap_close"])
    # 상권변화지표 라벨은 피처기간 마지막 분기 기준(배경 리스크 신호, 4장 활용방향 참고)
    ci_last = ci[ci["기준_년분기_코드"] == FEATURE_QUARTERS[-1]].set_index("상권_코드")["상권_변화_지표"]

    panel = f_store.join(f_sales, how="inner").join(f_flow, how="inner").join(f_ci, how="inner")
    panel["상권_변화_지표"] = ci_last.reindex(panel.index)
    panel["프랜차이즈_비율"] = np.where(panel["전체_점포_수"] > 0,
                                    panel["프랜차이즈_점포_수"] / panel["전체_점포_수"] * 100, 0.0)

    # ---- 정답 기간(20251~20261) ----
    l_sales = period_mean(sales, LABEL_QUARTERS, ["당월_매출_금액"]).rename(columns={"당월_매출_금액": "미래매출"})
    panel = panel.join(l_sales, how="inner")

    # 피처기간에 그 업종 점포/매출이 아예 없던 상권은 "창업 후보"로서 의미가 없고 변화율도 정의 불가 -> 제외
    panel = panel[(panel["과거매출"] > 0) & (panel["전체_점포_수"] > 0)]

    # A안 정답: 미래매출 상위 20%
    panel["label_A"] = (panel["미래매출"] >= panel["미래매출"].quantile(0.8)).astype(int)

    # B안 정답: 초과변화율(서울 평균 대비) 상위 20%
    panel["매출_변화율"] = panel["미래매출"] / panel["과거매출"] - 1
    seoul_chg = panel["미래매출"].sum() / panel["과거매출"].sum() - 1  # 같은 업종 서울 전체 벤치마크
    panel["초과변화율"] = (panel["매출_변화율"] - seoul_chg) * 100
    panel["label_B"] = (panel["초과변화율"] >= panel["초과변화율"].quantile(0.8)).astype(int)

    # 회귀 타깃(연속값): A안은 log 매출, B안은 초과변화율
    panel["target_A"] = np.log1p(panel["미래매출"])
    panel["target_B"] = panel["초과변화율"]

    # 왜도가 큰 규모 변수는 log1p (프로젝트 공통 관례)
    for c in ["유동인구", "과거매출", "전체_점포_수"]:
        panel[c] = np.log1p(panel[c])

    return panel.reset_index(), seoul_chg


def zscore(X):
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd = np.where(sd == 0, 1.0, sd)
    return (X - mu) / sd, mu, sd


def fit_weights(X, y):
    """표준화 다중선형회귀 계수(절편 포함). numpy lstsq -- 7장·17장과 동일 방식."""
    Xz, mu, sd = zscore(X)
    A = np.column_stack([np.ones(len(Xz)), Xz])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    return coef, mu, sd


def apply_score(X, coef, mu, sd):
    Xz = (X - mu) / np.where(sd == 0, 1.0, sd)
    return coef[0] + Xz @ coef[1:]


def evaluate_cv(panel, target_col, label_col, k, n_folds=5, seed=42, feature_cols=None):
    """5-fold CV: 4폴드로 가중치 학습 -> 남은 폴드에서 상위 k개 추천 -> 정답과 대조."""
    feature_cols = feature_cols or FEATURE_COLS
    X_all = panel[feature_cols].to_numpy(float)
    y_all = panel[target_col].to_numpy(float)
    lab_all = panel[label_col].to_numpy(int)

    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(panel))
    folds = np.array_split(idx, n_folds)

    rows = []
    for i, test_idx in enumerate(folds):
        train_idx = np.concatenate([f for j, f in enumerate(folds) if j != i])
        coef, mu, sd = fit_weights(X_all[train_idx], y_all[train_idx])
        scores = apply_score(X_all[test_idx], coef, mu, sd)

        n_pos = int(lab_all[test_idx].sum())
        # k=None이면 폴드의 정답 개수(=상위 20%)에 맞춰 k를 정한다(R-precision).
        # 고정 k를 쓰면 상권 수가 적은 업종에서 k가 폴드 크기를 넘어 "전부 추천"이 되고,
        # 그러면 precision=기저율·recall=1이라 F1이 기계적으로 0.333으로 고정되는 퇴화가 생긴다.
        k_eff = min(k, len(test_idx)) if k is not None else max(n_pos, 1)
        top = np.argsort(-scores)[:k_eff]
        picked = lab_all[test_idx][top]

        tp = int(picked.sum())
        precision = tp / k_eff if k_eff else 0.0
        recall = tp / n_pos if n_pos else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        baseline = n_pos / len(test_idx)  # 무작위로 찍었을 때의 기대 precision
        rows.append({"fold": i + 1, "테스트_상권수": len(test_idx), "정답수": int(n_pos), "k": k_eff,
                      "Precision@k": precision, "Recall@k": recall, "F1@k": f1,
                      "랜덤_베이스라인_precision": baseline,
                      "lift": precision / baseline if baseline else np.nan})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("업종", nargs="?", default="한식음식점",
                    help="업종명 또는 코드 (예: 한식음식점, CS100001). 미지정 시 한식음식점")
    ap.add_argument("-k", type=int, default=20, help="추천 개수 top-k (기본 20)")
    args = ap.parse_args()

    key = args.업종
    code = key if key in INDUSTRY_CODES else NAME_TO_CODE.get(key)
    if code is None:
        raise SystemExit(f"알 수 없는 업종: {key}\n사용 가능: {', '.join(NAME_TO_CODE)}")
    name = INDUSTRY_CODES[code]

    panel, seoul_chg = build_panel(code)
    print(f"===== 업종: {name} ({code}) =====")
    print(f"분석 대상 상권: {len(panel)}개 "
          f"(피처기간 {FEATURE_QUARTERS[0]}~{FEATURE_QUARTERS[-1]}, 정답기간 {LABEL_QUARTERS[0]}~{LABEL_QUARTERS[-1]})")
    print(f"같은 업종 서울 전체 매출 변화율(벤치마크): {seoul_chg * 100:+.1f}%")

    # 피처 조합 3종을 비교(ablation) -- "과거매출 하나로 줄세우기"와 실제 구조 피처의 기여를 분리해서 본다.
    # 전체 피처로 A안을 돌리면 Precision@k가 1.000까지 나오는데, 이는 모델이 좋아서가 아니라
    # 과거매출 계수가 압도적(2위의 30배 이상)이라 사실상 과거매출 순위와 같아지기 때문이다(상관 0.9995).
    FEATURE_SETS = {
        "전체피처": FEATURE_COLS,
        "과거매출제외": [c for c in FEATURE_COLS if c != "과거매출"],
        "과거매출단독": ["과거매출"],
    }

    all_cv = []
    for label_col, target_col, desc in [("label_A", "target_A", "A안: 이미 잘되는 상권(미래매출 상위 20%)"),
                                          ("label_B", "target_B", "B안: 앞으로 유망한 상권(초과변화율 상위 20%)")]:
        print(f"\n{'=' * 70}\n{desc}\n{'=' * 70}")
        for set_name, cols in FEATURE_SETS.items():
            cv = evaluate_cv(panel, target_col, label_col, args.k, feature_cols=cols)
            cv.insert(0, "정답기준", label_col)
            cv.insert(1, "피처조합", set_name)
            all_cv.append(cv)
            m, s = cv.mean(numeric_only=True), cv.std(numeric_only=True)
            print(f"\n[{set_name}] F1@{args.k} = {m['F1@k']:.3f} ± {s['F1@k']:.3f} | "
                  f"P@{args.k} = {m['Precision@k']:.3f} | R@{args.k} = {m['Recall@k']:.3f} | "
                  f"lift = {m['lift']:.2f}배 (랜덤 {m['랜덤_베이스라인_precision']:.3f})")
            X = panel[cols].to_numpy(float)
            coef, mu, sd = fit_weights(X, panel[target_col].to_numpy(float))
            w = pd.Series(coef[1:], index=cols).sort_values(key=abs, ascending=False)
            print("   가중치: " + ", ".join(f"{k}={v:+.3f}" for k, v in w.items()))

    pd.concat(all_cv).to_csv(os.path.join(OUT, f"cv_{name}.csv"), index=False, encoding="utf-8-sig")

    # ---------- 최종 추천: 전체 데이터로 학습한 A·B 점수 결합 ----------
    X = panel[FEATURE_COLS].to_numpy(float)
    for tag, target_col in [("A", "target_A"), ("B", "target_B")]:
        coef, mu, sd = fit_weights(X, panel[target_col].to_numpy(float))
        raw = apply_score(X, coef, mu, sd)
        # 0~100 정규화(서로 다른 척도의 A·B 점수를 나란히 보기 위함)
        panel[f"점수_{tag}"] = (raw - raw.min()) / (raw.max() - raw.min()) * 100

    panel["종합점수"] = (panel["점수_A"] + panel["점수_B"]) / 2

    trdar_name = _read(os.path.join(DATA, "영역", "상권", "서울시 상권분석서비스(영역-상권).csv"))
    name_map = trdar_name.set_index("상권_코드")["상권_코드_명"].to_dict()
    panel["상권명"] = panel["상권_코드"].map(name_map)

    cols = ["상권_코드", "상권명", "종합점수", "점수_A", "점수_B", "상권_변화_지표",
            "미래매출", "초과변화율", "전체_점포_수", "label_A", "label_B"]
    for tag, title in [("종합점수", f"종합(A+B 평균) top-{args.k}"),
                        ("점수_A", f"A안(이미 잘되는 상권) top-{args.k}"),
                        ("점수_B", f"B안(앞으로 유망한 상권) top-{args.k}")]:
        print(f"\n===== {title} =====")
        top = panel.sort_values(tag, ascending=False).head(args.k)
        print(top[cols].round(2).to_string(index=False))

    panel.sort_values("종합점수", ascending=False).to_csv(
        os.path.join(OUT, f"recommend_{name}.csv"), index=False, encoding="utf-8-sig")
    print(f"\n저장: {OUT}/recommend_{name}.csv, {OUT}/cv_{name}.csv")


if __name__ == "__main__":
    main()
