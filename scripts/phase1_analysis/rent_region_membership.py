"""
10-3c. 임대료 지역(63개) -> 행정동/상권배후지 면적가중 다중소속 멤버십 테이블.

기존 rent_region_match.py의 point-in-polygon 대표좌표 방식(대표좌표 하나가 어느 폴리곤
안에 들어있는지만 판정, 10-4/10-5)을 대체한다. 10-3b에서 확인한 대로 대표좌표는
(a) 여러 후보 상권을 합칠 때 생기는 인공적인 중심점이라 면적 1위가 아닌 동에 찍힐 수 있고,
(b) 상권배후지의 경우 "코드가 같은 배후지가 있는가"(identity)와 "좌표가 우연히 어느
배후지에나 들어있는가"(point-in-polygon)가 서로 다른 답을 준다.

여기서는 대표좌표를 아예 거치지 않고, 각 임대료 지역의 선택된 상권(들) 코드가 실제로
겹치는 행정동/상권배후지를 crosswalk_trdar_dong.csv / crosswalk_trdar_alley_spatial.csv에서
면적으로 전부 뽑아 지역 내 비중(weight, 합=1)을 매긴다 -- [[overlap_strategy_combine_not_split]]
원칙대로 쪼개지 않고 결합해서 쓴다.
"""
import os

import pandas as pd
import shapefile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "output")

# 1. 상권명 -> TRDAR_CD (이름 전부 고유함, rent_crosswalk_overlap_check.py와 동일 소스)
sf = shapefile.Reader(os.path.join(ROOT, "data", "영역", "상권", "서울시 상권분석서비스(영역-상권).shp"))
fields = [f[0] for f in sf.fields[1:]]
recs = [dict(zip(fields, sr.record)) for sr in sf.iterShapeRecords()]
name_to_code = {r["TRDAR_CD_N"]: int(r["TRDAR_CD"]) for r in recs}

# 2. 임대료 대표결정 테이블(10-5 산출물)에서 지역별 선택된 상권(들)만 재사용
#    -- 대표좌표(X, Y)와 point-in-polygon 소속 컬럼은 여기서부터 쓰지 않는다.
rep = pd.read_csv(os.path.join(OUT, "rent_region_representative.csv"))
rep = rep[rep["대표결정방식"] != "매칭없음"].copy()
rep["TRDAR_CODES"] = rep["선택된_상권"].apply(lambda sel: [name_to_code[n.strip()] for n in sel.split(" | ")])


def build_membership(crosswalk_path, code_col, area_col, name_col, out_id_col, out_name_col):
    cw = pd.read_csv(crosswalk_path)
    rows = []
    for _, row in rep.iterrows():
        codes = row["TRDAR_CODES"]
        sub = cw[cw["TRDAR_CD"].isin(codes)]
        if len(sub) == 0:
            continue
        agg = sub.groupby([code_col, name_col])[area_col].sum().reset_index()
        total = agg[area_col].sum()
        for _, r in agg.iterrows():
            rows.append({
                "임대료_지역": row["임대료_지역"],
                out_id_col: int(r[code_col]),
                out_name_col: r[name_col],
                "overlap_area": round(r[area_col], 2),
                "weight": round(r[area_col] / total, 6),
            })
    return pd.DataFrame(rows)


dong_membership = build_membership(
    os.path.join(OUT, "crosswalks", "crosswalk_trdar_dong.csv"),
    "ADSTRD_CD", "overlap_area", "ADSTRD_NM", "행정동_코드", "행정동_명",
)
dong_path = os.path.join(OUT, "rent_region_dong_membership.csv")
dong_membership.to_csv(dong_path, index=False, encoding="utf-8-sig")

alley_membership = build_membership(
    os.path.join(OUT, "crosswalks", "crosswalk_trdar_alley_spatial.csv"),
    "ALLEY_TRDA", "overlap_area", "ALLEY_TRDA_NM", "상권배후지_코드", "상권배후지_명",
)
alley_path = os.path.join(OUT, "rent_region_alley_membership.csv")
alley_membership.to_csv(alley_path, index=False, encoding="utf-8-sig")

print(f"saved: {dong_path} ({len(dong_membership)}행)")
print(f"  행정동 커버: {dong_membership['임대료_지역'].nunique()}/63개 지역, "
      f"지역당 평균 소속 행정동 수 {dong_membership.groupby('임대료_지역').size().mean():.2f}개")
weight_sum = dong_membership.groupby('임대료_지역')['weight'].sum()
assert ((weight_sum - 1.0).abs() < 1e-4).all(), "행정동 weight 합이 1이 아닌 지역 존재"
print("  weight 합 검증: 전 지역 1.0 확인")

print(f"\nsaved: {alley_path} ({len(alley_membership)}행)")
print(f"  상권배후지 커버: {alley_membership['임대료_지역'].nunique()}/63개 지역 "
      f"(기존 point-in-polygon 방식은 39개, 코드identity 방식은 20개 -- 10-3b 참고)")
if len(alley_membership) > 0:
    weight_sum_a = alley_membership.groupby('임대료_지역')['weight'].sum()
    assert ((weight_sum_a - 1.0).abs() < 1e-4).all(), "상권배후지 weight 합이 1이 아닌 지역 존재"
    print("  weight 합 검증: 전 지역 1.0 확인")
    print(f"  지역당 평균 소속 배후지 수 {alley_membership.groupby('임대료_지역').size().mean():.2f}개")
