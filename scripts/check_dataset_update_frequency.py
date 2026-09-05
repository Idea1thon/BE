"""
23장: 프로젝트가 쓰는 모든 시계열 데이터셋에 대해 "분기간 동일값 비율"을 한 번에 점검해,
계단식(step-function, 관리상 갱신 정지) 데이터와 진짜 시계열(매분기 갱신) 데이터를
전부 가려낸다 (사용자 요청, 2026-08-09).

배경: 18-3(아파트)·21-3(상주인구)·22-3(직장인구)에서 개별적으로 발견한 계단식 데이터
문제를 이번엔 프로젝트의 모든 데이터셋에 대해 한 번에, 체계적으로 점검한다
(memory: stepwise_low_frequency_datasets에서 "새 데이터셋 투입 전 항상 점검"으로 정한 절차).

판정 기준: 같은 지역(코드)의 연속된 두 분기 값이 동일한 비율(same_ratio).
- 90% 이상이면서 "특정 분기에만 몰아서 갱신되고 나머지는 100% 고정"되는 패턴이면 계단식(관리상 갱신 정지)
- 30~80%대라도 정수형 평균·소수 카운트처럼 자연스럽게 자주 겹칠 수 있는 값이면 시계열(매분기 갱신, 값만 안 바뀔 뿐)
따라서 same_ratio 하나만으로 자동 판정하지 않고, "분기별 same_ratio 분포가 균일한가(매분기 조금씩 갱신) vs
소수 분기에 몰려있는가(그 분기에만 전면 갱신 후 정지)"를 같이 본다.
"""
import os

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "output")

INDUSTRY_CODES = ["CS100001", "CS100002", "CS100003", "CS100004", "CS100005",
                   "CS100006", "CS100007", "CS100008", "CS100009", "CS100010"]


def _read(path):
    try:
        return pd.read_csv(path, encoding="cp949")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="utf-8")


def same_ratio_report(df, code_col, value_col, label):
    """code_col 별로 정렬 후 인접 분기 값이 동일한 비율을 전체/분기별로 계산."""
    d = df.sort_values([code_col, "기준_년분기_코드"]).copy()
    d["prev"] = d.groupby(code_col)[value_col].shift(1)
    d = d[d["prev"].notna()]
    overall = (d[value_col] == d["prev"]).mean()
    by_q = d.groupby("기준_년분기_코드").apply(lambda g: (g[value_col] == g["prev"]).mean())
    # 계단식 판정: "이 분기엔 거의 다 바뀌고(same_ratio<0.1, 실제 갱신) 나머지 대부분 분기는 거의 다 그대로(>0.9, 고정)"인
    # 뚜렷한 이분법이 있어야 함 -- 상권변화지표 라벨처럼 전 분기 내내 고르게 sticky한(0.9 근처에서 안 벗어남) 경우는
    # "매분기 갱신되지만 값 자체가 잘 안 바뀌는" 자연스러운 특성이지 관리상 갱신 정지가 아니므로 제외해야 함.
    n_real_update_q = (by_q < 0.1).sum()
    n_frozen_q = (by_q > 0.9).sum()
    is_bimodal_step = 1 <= n_real_update_q <= 6 and n_frozen_q >= len(by_q) - 6
    summary = {"데이터셋": label, "전체_동일값_비율": round(overall, 4),
               "분기별_최소": round(by_q.min(), 3), "분기별_최대": round(by_q.max(), 3),
               "계단식_의심": is_bimodal_step}
    return summary, by_q


def main():
    results = []
    by_q_rows = []  # 분기별 동일값 비율 전체 저장(소규모 다중 시각화용)

    def add(summary, by_q):
        results.append(summary)
        for q, v in by_q.items():
            by_q_rows.append({"데이터셋": summary["데이터셋"], "기준_년분기_코드": q,
                               "동일값_비율": v, "계단식_의심": summary["계단식_의심"]})

    # ---------- 길단위인구(단일 파일, 행정동) ----------
    df = _read(os.path.join(DATA, "길단위인구", "서울시 상권분석서비스(길단위인구-행정동).csv"))
    add(*same_ratio_report(df, "행정동_코드", "총_유동인구_수", "길단위인구(유동인구)"))

    # ---------- 아파트(단일 파일, 행정동) ----------
    df = _read(os.path.join(DATA, "아파트", "서울시 상권분석서비스(아파트-행정동).csv"))
    add(*same_ratio_report(df, "행정동_코드", "아파트_평균_시가", "아파트"))

    # ---------- 상주인구(단일 파일, 행정동) ----------
    df = _read(os.path.join(DATA, "상주인구", "서울시 상권분석서비스(상주인구-행정동).csv"))
    add(*same_ratio_report(df, "행정동_코드", "총_상주인구_수", "상주인구"))

    # ---------- 직장인구(단일 파일, 행정동) ----------
    df = _read(os.path.join(DATA, "직장인구", "서울시 상권분석서비스(직장인구-행정동).csv"))
    add(*same_ratio_report(df, "행정동_코드", "총_직장_인구_수", "직장인구"))

    # ---------- 상권변화지표(단일 파일, 행정동, 3개 컬럼) ----------
    df = _read(os.path.join(DATA, "상권변화지표", "서울시 상권분석서비스(상권변화지표-행정동).csv"))
    add(*same_ratio_report(df, "행정동_코드", "운영_영업_개월_평균", "상권변화지표-운영_영업_개월_평균"))
    add(*same_ratio_report(df, "행정동_코드", "폐업_영업_개월_평균", "상권변화지표-폐업_영업_개월_평균"))
    add(*same_ratio_report(df, "행정동_코드", "상권_변화_지표", "상권변화지표-라벨(LL/LH/HL/HH)"))

    # ---------- 추정매출(연도별 5개 파일, 행정동, 10개 업종 합계) ----------
    sales_files = [
        "2021/서울시_상권분석서비스(추정매출-행정동)_2021년.csv",
        "2022/서울시_상권분석서비스(추정매출-행정동)_2022년.csv",
        "2023/서울시_상권분석서비스(추정매출-행정동)_2023년.csv",
        "2024/서울시 상권분석서비스(추정매출-행정동)_2024년.csv",
        "2026/서울시 상권분석서비스(추정매출-행정동).csv",
    ]
    parts = []
    for rel in sales_files:
        d = _read(os.path.join(DATA, "추정매출", rel))
        d = d[d["서비스_업종_코드"].isin(INDUSTRY_CODES)]
        parts.append(d[["기준_년분기_코드", "행정동_코드", "서비스_업종_코드", "당월_매출_금액"]])
    sales = pd.concat(parts, ignore_index=True).drop_duplicates(
        subset=["기준_년분기_코드", "행정동_코드", "서비스_업종_코드"])
    sales_agg = sales.groupby(["기준_년분기_코드", "행정동_코드"])["당월_매출_금액"].sum().reset_index()
    add(*same_ratio_report(sales_agg, "행정동_코드", "당월_매출_금액", "추정매출(10개 업종 합계)"))

    # ---------- 점포(연도별 5개 파일, 행정동, 10개 업종 합계, 11-1d 수정 반영) ----------
    store_files = [
        "2021년/서울시_상권분석서비스(점포-행정동)_2021년.csv",
        "2022년/서울시_상권분석서비스(점포-행정동)_2022년.csv",
        "2023년/서울시 상권분석서비스(점포-행정동)_2023년.csv",
        "2024년/서울시 상권분석서비스(점포-행정동)_2024년.csv",
        "2026년/서울시 상권분석서비스(점포-행정동).csv",
    ]
    parts = []
    for rel in store_files:
        d = _read(os.path.join(DATA, "점포", rel))
        d = d.rename(columns={"유사_업종_점포_수": "전체_점포_수"})
        d = d[d["서비스_업종_코드"].isin(INDUSTRY_CODES)]
        parts.append(d[["기준_년분기_코드", "행정동_코드", "서비스_업종_코드", "전체_점포_수"]])
    store = pd.concat(parts, ignore_index=True).drop_duplicates(
        subset=["기준_년분기_코드", "행정동_코드", "서비스_업종_코드"])
    store_agg = store.groupby(["기준_년분기_코드", "행정동_코드"])["전체_점포_수"].sum().reset_index()
    add(*same_ratio_report(store_agg, "행정동_코드", "전체_점포_수", "점포(10개 업종 합계)"))

    # ---------- 임대료(단일 파일, wide format, 지역 단위 -- 별도 파싱) ----------
    rent_raw = pd.read_csv(os.path.join(DATA, "매장용빌딩 임대료,공실률 및 수익률 통계 데이터.csv"),
                            encoding="utf-8", skiprows=2, header=None)
    rent_cols = [2 + 5 * i for i in range(20)]  # 20개 분기(2021Q1~2025Q4), 각 5개 서브컬럼 중 첫 번째=임대료
    rent = rent_raw.iloc[:, [0, 1] + rent_cols].copy()
    rent.columns = ["지역1", "지역2"] + [f"Q{i + 1}" for i in range(20)]
    rent = rent[rent["지역2"] != "소계"]
    for c in rent.columns[2:]:
        rent[c] = pd.to_numeric(rent[c], errors="coerce")
    same_total, count_total = 0, 0
    per_q_same = {}
    for i in range(1, 20):
        cur, prev = rent[f"Q{i + 1}"], rent[f"Q{i}"]
        valid = cur.notna() & prev.notna()
        same_total += (cur[valid] == prev[valid]).sum()
        count_total += valid.sum()
        per_q_same[i + 1] = (cur[valid] == prev[valid]).mean() if valid.sum() else float("nan")
    rent_by_q = pd.Series(per_q_same)
    rent_summary = {"데이터셋": "임대료(매장용빌딩)", "전체_동일값_비율": round(same_total / count_total, 4),
                     "분기별_최소": round(rent_by_q.min(), 3), "분기별_최대": round(rent_by_q.max(), 3),
                     "계단식_의심": False}
    add(rent_summary, rent_by_q)

    summary = pd.DataFrame(results)
    summary.to_csv(os.path.join(OUT, "dataset_update_frequency_check.csv"), index=False, encoding="utf-8-sig")
    print(summary.to_string(index=False))

    by_q_df = pd.DataFrame(by_q_rows)
    by_q_df.to_csv(os.path.join(OUT, "dataset_update_frequency_by_quarter.csv"), index=False, encoding="utf-8-sig")
    print(f"\n저장(분기별 상세): {OUT}/dataset_update_frequency_by_quarter.csv")
    print(f"\n저장: {OUT}/dataset_update_frequency_check.csv")


if __name__ == "__main__":
    main()
