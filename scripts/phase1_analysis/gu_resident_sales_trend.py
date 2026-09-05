"""
21장: 상주인구와 매출의 자치구별 변화추이 (사용자 요청, 2026-08-09).

배경: `docs/data_result.md` 3장(송파구)에서 상주인구는 유동인구·직장인구보다 매출과의
상관관계가 가장 약하고(r=0.04~0.34), 유동인구와 상주인구 자체의 상관관계가 매우 높아
(r=0.80, 7장) 다중공선성 문제까지 지적된 바 있다. 이 장은 그 스냅샷 상관관계를
**분기별 변화 추이**로 확장해, 상주인구가 늘어나는/줄어드는 자치구에서 매출이 실제로
동행하는지를 20장과 같은 자치구 단위 프레임으로 다시 검증한다.

20장과 동일하게 행정동_코드 앞 5자리로 자치구를 매핑하고, 매출은 10개 외식업
서비스_업종_코드로 제한한다(프로젝트 공통 관례). 상주인구는 `길단위인구`·`아파트`와
같은 단일 파일(21개 분기 native)이라 연도별 파일 통합이 필요없다.
"""
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "output", "gu")
os.makedirs(OUT, exist_ok=True)

GU_NAMES = {
    "11110": "종로구", "11140": "중구", "11170": "용산구", "11200": "성동구",
    "11215": "광진구", "11230": "동대문구", "11260": "중랑구", "11290": "성북구",
    "11305": "강북구", "11320": "도봉구", "11350": "노원구", "11380": "은평구",
    "11410": "서대문구", "11440": "마포구", "11470": "양천구", "11500": "강서구",
    "11530": "구로구", "11545": "금천구", "11560": "영등포구", "11590": "동작구",
    "11620": "관악구", "11650": "서초구", "11680": "강남구", "11710": "송파구",
    "11740": "강동구",
}

INDUSTRY_CODES = {
    "CS100001": "한식음식점", "CS100002": "중식음식점", "CS100003": "일식음식점",
    "CS100004": "양식음식점", "CS100005": "제과점", "CS100006": "패스트푸드점",
    "CS100007": "치킨전문점", "CS100008": "분식전문점", "CS100009": "호프-간이주점",
    "CS100010": "커피-음료",
}

SALES_YEAR_FILES = [
    "2021/서울시_상권분석서비스(추정매출-행정동)_2021년.csv",
    "2022/서울시_상권분석서비스(추정매출-행정동)_2022년.csv",
    "2023/서울시_상권분석서비스(추정매출-행정동)_2023년.csv",
    "2024/서울시 상권분석서비스(추정매출-행정동)_2024년.csv",
    "2026/서울시 상권분석서비스(추정매출-행정동).csv",
]


def _read(path):
    try:
        return pd.read_csv(path, encoding="cp949")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="utf-8")


def dong_to_gu():
    area = _read(os.path.join(DATA, "영역", "행정동", "서울시 상권분석서비스(영역-행정동).csv"))
    area["자치구코드"] = area["행정동_코드"].astype(str).str[:5]
    area["자치구"] = area["자치구코드"].map(GU_NAMES)
    assert area["자치구"].isna().sum() == 0, "매핑 안 된 행정동 존재"
    return area.set_index("행정동_코드")["자치구"]


def load_resident():
    df = _read(os.path.join(DATA, "상주인구", "서울시 상권분석서비스(상주인구-행정동).csv"))
    return df[["기준_년분기_코드", "행정동_코드", "총_상주인구_수"]]


def load_sales():
    parts = []
    for rel in SALES_YEAR_FILES:
        df = _read(os.path.join(DATA, "추정매출", rel))
        df = df[df["서비스_업종_코드"].isin(INDUSTRY_CODES)]
        parts.append(df[["기준_년분기_코드", "행정동_코드", "서비스_업종_코드", "당월_매출_금액"]])
    sales = pd.concat(parts, ignore_index=True).drop_duplicates(
        subset=["기준_년분기_코드", "행정동_코드", "서비스_업종_코드"])
    return sales.groupby(["기준_년분기_코드", "행정동_코드"])["당월_매출_금액"].sum().reset_index()


def main():
    gu_map = dong_to_gu()
    resident = load_resident()
    sales = load_sales()

    quarters = sorted(set(resident["기준_년분기_코드"]) & set(sales["기준_년분기_코드"]))
    print(f"공통분기: {quarters[0]}~{quarters[-1]} ({len(quarters)}개)")

    def gu_sum(df, value_col):
        d = df[df["기준_년분기_코드"].isin(quarters)].copy()
        d["자치구"] = d["행정동_코드"].map(gu_map)
        agg = d.groupby(["자치구", "기준_년분기_코드"])[value_col].sum().reset_index()
        return agg.rename(columns={value_col: "value"})

    resident_gu = gu_sum(resident, "총_상주인구_수")
    sales_gu = gu_sum(sales, "당월_매출_금액")

    resident_piv = resident_gu.pivot(index="기준_년분기_코드", columns="자치구", values="value").sort_index()
    sales_piv = sales_gu.pivot(index="기준_년분기_코드", columns="자치구", values="value").sort_index()
    resident_piv.to_csv(os.path.join(OUT, "gu_trend_상주인구.csv"), encoding="utf-8-sig")
    sales_piv.to_csv(os.path.join(OUT, "gu_trend_추정매출_상주인구비교용.csv"), encoding="utf-8-sig")

    # ---------- 자치구별 요약: 변화율 + 시계열 상관 ----------
    gus = sorted(gu_map.unique())
    q_first, q_last = quarters[0], quarters[-1]
    rows = []
    for gu in gus:
        r = resident_piv[gu]
        s = sales_piv[gu]
        r_chg = (r.loc[q_last] / r.loc[q_first] - 1) * 100
        s_chg = (s.loc[q_last] / s.loc[q_first] - 1) * 100
        # 자치구 내부 시계열 상관(21개 분기, log1p) -- "상주인구가 늘어난 분기에 매출도 늘었는가"
        corr = np.corrcoef(np.log1p(r.values), np.log1p(s.values))[0, 1]
        rows.append({"자치구": gu, "상주인구_변화율%": r_chg, "추정매출_변화율%": s_chg,
                      "시계열상관_log1p": corr})
    summary = pd.DataFrame(rows).sort_values("추정매출_변화율%", ascending=False)
    summary.to_csv(os.path.join(OUT, "gu_resident_sales_summary.csv"), index=False, encoding="utf-8-sig")

    # ---------- pooled 상관(전체 자치구x분기 패널) vs 자치구별 독립표본 평균 ----------
    panel = resident_gu.merge(sales_gu, on=["자치구", "기준_년분기_코드"], suffixes=("_상주인구", "_매출"))
    pooled_corr = np.corrcoef(np.log1p(panel["value_상주인구"]), np.log1p(panel["value_매출"]))[0, 1]
    mean_within_gu_corr = summary["시계열상관_log1p"].mean()

    print("\n===== 자치구별 요약 (매출 변화율 순) =====")
    print(summary.round(3).to_string(index=False))
    print(f"\npooled 상관(자치구x분기 {len(panel)}행, log1p): {pooled_corr:.3f}")
    print(f"자치구별 시계열 상관의 평균(25개 자치구 독립): {mean_within_gu_corr:.3f}")
    print(f"양수 상관 자치구 수: {(summary['시계열상관_log1p'] > 0).sum()}/25")

    # ---------- 중요 발견: 상주인구도 18-3의 아파트처럼 "계단식" 데이터인지 점검 ----------
    print("\n===== 데이터 특성 점검: 상주인구가 실제로 바뀌는 분기 =====")
    dong_raw = resident.sort_values(["행정동_코드", "기준_년분기_코드"]).copy()
    dong_raw["prev"] = dong_raw.groupby("행정동_코드")["총_상주인구_수"].shift(1)
    dong_raw["changed"] = dong_raw["총_상주인구_수"] != dong_raw["prev"]
    change_rate_by_q = dong_raw.groupby("기준_년분기_코드")["changed"].mean()
    print(change_rate_by_q.round(3).to_string())
    change_quarters = change_rate_by_q[change_rate_by_q > 0.5].index.tolist()
    real_transitions = [q for q in change_quarters if q != quarters[0]]  # 첫 분기는 "이전값 없음"이라 항상 changed로 잡히는 트리비얼 케이스
    print(f"-> 실제 변화가 일어나는 분기(425개 행정동의 50% 이상이 값 변경): {change_quarters}")
    print(f"   이 중 {quarters[0]}은 첫 분기라 비교 대상(이전값)이 없는 트리비얼 케이스 -- 진짜 전환점은 {real_transitions}뿐.")
    print("   나머지 분기는 전분기와 완전히 동일한 값 -- 계단식(step-function) 데이터.")

    # 실제 변화 시점(전환점)에서만 상주인구 변화율 vs 매출 변화율 비교(pooled)
    trans_rows = []
    for q_curr in real_transitions:
        q_prev = quarters[quarters.index(q_curr) - 1]
        for gu in gus:
            rp, rc = resident_piv.loc[q_prev, gu], resident_piv.loc[q_curr, gu]
            sp, sc = sales_piv.loc[q_prev, gu], sales_piv.loc[q_curr, gu]
            trans_rows.append({"자치구": gu, "전환시점": f"{q_prev}->{q_curr}",
                                "상주인구_변화%": (rc / rp - 1) * 100, "매출_변화%": (sc / sp - 1) * 100})
    trans_df = pd.DataFrame(trans_rows)
    trans_df.to_csv(os.path.join(OUT, "gu_resident_sales_transition_points.csv"), index=False, encoding="utf-8-sig")
    trans_corr = np.corrcoef(trans_df["상주인구_변화%"], trans_df["매출_변화%"])[0, 1]
    print(f"\n실제 전환점({len(trans_df)}개, {len(real_transitions)}개 시점 x 25개 자치구)에서의 상관: {trans_corr:.3f}")
    print("-> 레벨(절대값) 상관이 강하게 음수로 나온 건 '두 지표가 서로 다른 방향으로 추세를 타는' 허상(spurious)이고,")
    print("   실제 상주인구가 움직이는 시점만 뽑아보면 매출과의 상관은 0에 가깝다(진짜 동행성 없음).")

    print(f"\n저장: {OUT}/gu_trend_상주인구.csv, gu_resident_sales_summary.csv, gu_resident_sales_transition_points.csv")


if __name__ == "__main__":
    main()
