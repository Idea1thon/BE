"""
20장: 서울 전체 평균(18장)에서 한 걸음 더 들어가, 25개 자치구마다의 패턴을 본다.
"자치구마다 길단위인구, 상권변화, 점포, 추정매출의 변화추이" (사용자 요청, 2026-08-09).

18장은 유동인구·아파트·추정매출·점포 4개를 "서울 전체 평균" 한 줄로만 봤다. 이번엔
- 아파트는 계단식 데이터 특성(18-3, 89.4% 분기간 동일값)이 발견돼 트렌드 지표로 부적합하므로 제외
- 대신 상권변화지표(LL/LH/HL/HH, `.claude/CLAUDE.md`에 정리된 4분류)를 추가
- 4개 데이터셋(길단위인구·상권변화지표·점포·추정매출)을 25개 자치구 단위로 쪼개서 비교

행정동_코드의 앞 5자리가 자치구 코드이므로(영역-행정동 파일로 검증, 425개 행정동 전수
일치) 이를 이용해 행정동 -> 자치구 매핑을 만든다(`dong_lists/`는 이름 목록만 있고 코드가
없어 이름매칭이 필요했지만, 코드 접두사 방식이 더 안전해 이쪽을 사용).

점포 데이터는 11-1d 스키마 버그(2021~2024년 파일의 '점포_수'는 프랜차이즈 제외값) 수정을
동일하게 반영한다. 매출·점포는 10개 외식업 코드로 제한(프로젝트 공통 관례).
"""
import os

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

STORE_YEAR_FILES = [
    "2021년/서울시_상권분석서비스(점포-행정동)_2021년.csv",
    "2022년/서울시_상권분석서비스(점포-행정동)_2022년.csv",
    "2023년/서울시 상권분석서비스(점포-행정동)_2023년.csv",
    "2024년/서울시 상권분석서비스(점포-행정동)_2024년.csv",
    "2026년/서울시 상권분석서비스(점포-행정동).csv",
]
STORE_COLUMN_ALIASES = {"유사_업종_점포_수": "전체_점포_수"}  # 11-1d

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


def load_flow():
    df = _read(os.path.join(DATA, "길단위인구", "서울시 상권분석서비스(길단위인구-행정동).csv"))
    return df[["기준_년분기_코드", "행정동_코드", "총_유동인구_수"]]


def load_store():
    parts = []
    for rel in STORE_YEAR_FILES:
        df = _read(os.path.join(DATA, "점포", rel))
        df = df.rename(columns=STORE_COLUMN_ALIASES)
        df = df[df["서비스_업종_코드"].isin(INDUSTRY_CODES)]
        parts.append(df[["기준_년분기_코드", "행정동_코드", "서비스_업종_코드", "전체_점포_수"]])
    store = pd.concat(parts, ignore_index=True).drop_duplicates(
        subset=["기준_년분기_코드", "행정동_코드", "서비스_업종_코드"])
    return store.groupby(["기준_년분기_코드", "행정동_코드"])["전체_점포_수"].sum().reset_index()


def load_sales():
    parts = []
    for rel in SALES_YEAR_FILES:
        df = _read(os.path.join(DATA, "추정매출", rel))
        df = df[df["서비스_업종_코드"].isin(INDUSTRY_CODES)]
        parts.append(df[["기준_년분기_코드", "행정동_코드", "서비스_업종_코드", "당월_매출_금액"]])
    sales = pd.concat(parts, ignore_index=True).drop_duplicates(
        subset=["기준_년분기_코드", "행정동_코드", "서비스_업종_코드"])
    return sales.groupby(["기준_년분기_코드", "행정동_코드"])["당월_매출_금액"].sum().reset_index()


def load_change_indicator():
    df = _read(os.path.join(DATA, "상권변화지표", "서울시 상권분석서비스(상권변화지표-행정동).csv"))
    return df[["기준_년분기_코드", "행정동_코드", "상권_변화_지표"]]


def main():
    gu_map = dong_to_gu()

    flow = load_flow()
    store = load_store()
    sales = load_sales()
    ci = load_change_indicator()

    quarters = sorted(set(flow["기준_년분기_코드"]) & set(store["기준_년분기_코드"])
                       & set(sales["기준_년분기_코드"]) & set(ci["기준_년분기_코드"]))
    print(f"공통분기: {quarters[0]}~{quarters[-1]} ({len(quarters)}개)")

    # ---------- 자치구×분기 집계: 유동인구/점포/매출 (합계) ----------
    def gu_sum(df, value_col):
        d = df[df["기준_년분기_코드"].isin(quarters)].copy()
        d["자치구"] = d["행정동_코드"].map(gu_map)
        agg = d.groupby(["자치구", "기준_년분기_코드"])[value_col].sum().reset_index()
        return agg.rename(columns={value_col: "value"})

    flow_gu = gu_sum(flow, "총_유동인구_수")
    store_gu = gu_sum(store, "전체_점포_수")
    sales_gu = gu_sum(sales, "당월_매출_금액")

    for name, d in [("유동인구", flow_gu), ("점포", store_gu), ("추정매출", sales_gu)]:
        piv = d.pivot(index="기준_년분기_코드", columns="자치구", values="value").sort_index()
        piv.to_csv(os.path.join(OUT, f"gu_trend_{name}.csv"), encoding="utf-8-sig")

    # ---------- 자치구×분기 집계: 상권변화지표 (라벨 구성비 %) ----------
    ci_q = ci[ci["기준_년분기_코드"].isin(quarters)].copy()
    ci_q["자치구"] = ci_q["행정동_코드"].map(gu_map)
    comp = (ci_q.groupby(["자치구", "기준_년분기_코드"])["상권_변화_지표"]
            .value_counts(normalize=True).mul(100).rename("비중%").reset_index())
    comp_piv = comp.pivot_table(index=["자치구", "기준_년분기_코드"], columns="상권_변화_지표",
                                 values="비중%", fill_value=0).reset_index()
    for code in ["LL", "LH", "HL", "HH"]:
        if code not in comp_piv.columns:
            comp_piv[code] = 0.0
    comp_piv.to_csv(os.path.join(OUT, "gu_trend_change_indicator.csv"), index=False, encoding="utf-8-sig")

    # HH(정체) 비중만 별도 피벗(히트맵용)
    hh_piv = comp_piv.pivot(index="기준_년분기_코드", columns="자치구", values="HH").sort_index()
    hh_piv.to_csv(os.path.join(OUT, "gu_trend_change_indicator_HH_share.csv"), encoding="utf-8-sig")

    # ---------- 요약: 자치구별 첫/끝 분기 비교 성장률 + 최신 분기 상권변화지표 구성 ----------
    rows = []
    q_first, q_last = quarters[0], quarters[-1]
    gus = sorted(gu_map.unique())
    for gu in gus:
        row = {"자치구": gu}
        for name, d in [("유동인구", flow_gu), ("점포", store_gu), ("추정매출", sales_gu)]:
            sub = d[d["자치구"] == gu].set_index("기준_년분기_코드")["value"]
            v0, v1 = sub.get(q_first), sub.get(q_last)
            row[f"{name}_{q_first}"] = v0
            row[f"{name}_{q_last}"] = v1
            row[f"{name}_변화율%"] = (v1 / v0 - 1) * 100 if v0 else float("nan")
        latest = comp_piv[(comp_piv["자치구"] == gu) & (comp_piv["기준_년분기_코드"] == q_last)]
        for code in ["LL", "LH", "HL", "HH"]:
            row[f"{code}_최신비중%"] = latest[code].values[0] if len(latest) else float("nan")
        rows.append(row)
    summary = pd.DataFrame(rows).sort_values("추정매출_변화율%", ascending=False)
    summary.to_csv(os.path.join(OUT, "gu_trend_summary.csv"), index=False, encoding="utf-8-sig")

    print("\n===== 자치구별 요약 (매출 변화율 순) =====")
    print(summary[["자치구", "유동인구_변화율%", "추정매출_변화율%", "점포_변화율%",
                    "HH_최신비중%", "LL_최신비중%"]].round(1).to_string(index=False))

    print(f"\n저장: {OUT}/gu_trend_{{유동인구,점포,추정매출}}.csv, gu_trend_change_indicator.csv, "
          f"gu_trend_change_indicator_HH_share.csv, gu_trend_summary.csv")


if __name__ == "__main__":
    main()
