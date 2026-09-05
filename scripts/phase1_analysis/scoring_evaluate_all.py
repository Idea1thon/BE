"""
24장 확장: scoring_topk_recommend.py의 검증을 10개 업종 전체에 대해 돌려서
"스코어링이 실제로 쓸모가 있는가"를 업종별로 비교한다.

핵심 질문: A안(이미 잘되는 상권 맞히기)과 B안(앞으로 유망한 상권 맞히기) 중
어느 쪽에서 우리가 만든 구조 피처(개업률·폐업률·점포수·유동인구·상권변화지표)가
실제로 기여하는가? -- '과거매출단독' 대비 '전체피처'의 F1 차이로 판단한다.
"""
import os

import numpy as np
import pandas as pd

from scoring_topk_recommend import (INDUSTRY_CODES, FEATURE_COLS, build_panel,
                                     evaluate_cv, fit_weights)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "output", "scoring")
os.makedirs(OUT, exist_ok=True)

# k=None -> 폴드의 정답 개수(상위 20%)에 맞춰 k를 자동 설정(R-precision).
# 고정 k를 쓰면 (a) k가 작을 때 A안이 포화되어 P@k=1.0이 되고, (b) 상권 수가 적은 업종에서는
# k가 폴드 크기를 넘어 "전부 추천"이 되면서 F1이 0.333으로 고정되는 퇴화가 생긴다.
# k=n_pos로 두면 precision=recall=F1이 되어 업종 간 비교가 공정해진다.
K = None
FEATURE_SETS = {
    "전체피처": FEATURE_COLS,
    "과거매출제외": [c for c in FEATURE_COLS if c != "과거매출"],
    "과거매출단독": ["과거매출"],
}


def main():
    rows, weight_rows = [], []
    for code, name in INDUSTRY_CODES.items():
        panel, seoul_chg = build_panel(code)
        for label_col, target_col, plan in [("label_A", "target_A", "A안(이미 잘되는 상권)"),
                                              ("label_B", "target_B", "B안(앞으로 유망한 상권)")]:
            for set_name, cols in FEATURE_SETS.items():
                cv = evaluate_cv(panel, target_col, label_col, K, feature_cols=cols)
                m, s = cv.mean(numeric_only=True), cv.std(numeric_only=True)
                rows.append({
                    "업종": name, "정답기준": plan, "피처조합": set_name,
                    "상권수": len(panel), "F1_평균": m["F1@k"], "F1_표준편차": s["F1@k"],
                    "Precision_평균": m["Precision@k"], "Recall_평균": m["Recall@k"],
                    "lift": m["lift"], "랜덤_베이스라인": m["랜덤_베이스라인_precision"],
                })
            # 전체피처 기준 가중치(업종별 비교용)
            X = panel[FEATURE_COLS].to_numpy(float)
            coef, mu, sd = fit_weights(X, panel[target_col].to_numpy(float))
            for f, w in zip(FEATURE_COLS, coef[1:]):
                weight_rows.append({"업종": name, "정답기준": plan, "피처": f, "표준화계수": w})
        print(f"완료: {name} ({len(panel)}개 상권, 서울 벤치마크 {seoul_chg * 100:+.1f}%)")

    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(OUT, "cv_all_industries.csv"), index=False, encoding="utf-8-sig")
    weights = pd.DataFrame(weight_rows)
    weights.to_csv(os.path.join(OUT, "weights_all_industries.csv"), index=False, encoding="utf-8-sig")

    print("\n" + "=" * 78)
    print(f"업종별 F1 (k=폴드 정답수, R-precision · 5-fold 평균) -- 피처조합 비교")
    print("=" * 78)
    piv = res.pivot_table(index=["정답기준", "업종"], columns="피처조합", values="F1_평균")
    piv = piv[["전체피처", "과거매출제외", "과거매출단독"]]
    piv["구조피처_기여"] = piv["전체피처"] - piv["과거매출단독"]
    print(piv.round(3).to_string())

    print("\n" + "=" * 78)
    print("정답기준별 요약 (10개 업종 평균)")
    print("=" * 78)
    summ = res.groupby(["정답기준", "피처조합"])[["F1_평균", "lift", "랜덤_베이스라인"]].mean()
    print(summ.round(3).to_string())

    print("\n" + "=" * 78)
    print("B안(유망 상권) 업종별 평균 표준화계수 -- 어떤 지표가 유망함을 예측하는가")
    print("=" * 78)
    wb = (weights[weights["정답기준"] == "B안(앞으로 유망한 상권)"]
          .groupby("피처")["표준화계수"].agg(["mean", "std"]).sort_values("mean", key=abs, ascending=False))
    print(wb.round(3).to_string())

    print(f"\n저장: {OUT}/cv_all_industries.csv, {OUT}/weights_all_industries.csv")


if __name__ == "__main__":
    main()
