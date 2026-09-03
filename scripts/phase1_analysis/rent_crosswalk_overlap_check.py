"""
10-3b. 사전에 구축한 겹침 crosswalk(scripts/build_overlap_crosswalks.py 산출물)로
임대료 지역(rent_region_match.py 산출물, 63개)의 공간적 소속을 재검증.

10-3의 point-in-polygon 방식(대표좌표 하나가 어느 폴리곤 안에 '들어있는지'만 판정)과 달리,
crosswalk_trdar_dong.csv / crosswalk_trdar_alley.csv / crosswalk_trdar_self_overlap.csv는
실제 폴리곤 면적 교차를 전수 계산해둔 테이블이므로, "대표좌표가 속한 동이 실제로 그 상권의
면적 대부분을 차지하는 동인가?", "합집합으로 묶은 후보들이 실제로 공간적으로 인접/겹치는가?"
같은 질문에 답할 수 있다.
"""
import os

import pandas as pd
import shapefile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "output")

# 1. 상권명 -> TRDAR_CD (shapefile에서 직접, 이름 전부 고유함 확인됨)
sf = shapefile.Reader(os.path.join(ROOT, "data", "영역", "상권", "서울시 상권분석서비스(영역-상권).shp"))
fields = [f[0] for f in sf.fields[1:]]
recs = [dict(zip(fields, sr.record)) for sr in sf.iterShapeRecords()]
name_to_code = {r["TRDAR_CD_N"]: int(r["TRDAR_CD"]) for r in recs}

# 2. 임대료 대표좌표 테이블 (10-5 산출물, 63행 = 68 - 5 제외)
rent = pd.read_csv(os.path.join(OUT, "rent_region_representative.csv"))
rent = rent[rent["대표결정방식"] != "매칭없음"].copy()


def resolve_codes(sel):
    return [name_to_code[n.strip()] for n in sel.split(" | ")]


rent["TRDAR_CODES"] = rent["선택된_상권"].apply(resolve_codes)

# 3. 사전 구축 crosswalk 3종 로드
cw_dong = pd.read_csv(os.path.join(OUT, "crosswalks", "crosswalk_trdar_dong.csv"))
cw_alley = pd.read_csv(os.path.join(OUT, "crosswalks", "crosswalk_trdar_alley.csv")).set_index("TRDAR_CD")
cw_self = pd.read_csv(os.path.join(OUT, "crosswalks", "crosswalk_trdar_self_overlap.csv"))

results = []
for _, row in rent.iterrows():
    codes = row["TRDAR_CODES"]

    # (a) 행정동 겹침: crosswalk 전수 교차 vs point-in-polygon 대표좌표
    sub = cw_dong[cw_dong["TRDAR_CD"].isin(codes)]
    n_dong_touched = sub["ADSTRD_NM"].nunique()
    if len(sub) > 0:
        dong_area = sub.groupby("ADSTRD_NM")["overlap_area"].sum().sort_values(ascending=False)
        dominant_dong = dong_area.index[0]
        dominant_share = dong_area.iloc[0] / dong_area.sum()
    else:
        dominant_dong, dominant_share = None, None

    # (b) 상권배후지 존재: crosswalk 코드매칭(자기 코드와 동일한 배후지가 있는가) vs point-in-polygon
    alley_sub = cw_alley.reindex(codes)
    has_alley_code = bool(alley_sub["has_alley"].fillna(False).any())
    max_containment = alley_sub["containment_ratio"].max()

    # (c) 합집합(대표결정방식) 후보들이 실제로 공간적으로 겹치는 쌍인지
    verified_merge = None
    if len(codes) > 1:
        found, total = 0, 0
        for i in range(len(codes)):
            for j in range(i + 1, len(codes)):
                total += 1
                a, b = codes[i], codes[j]
                hit = cw_self[
                    ((cw_self["PARENT_TRDAR_CD"] == a) & (cw_self["CHILD_TRDAR_CD"] == b))
                    | ((cw_self["PARENT_TRDAR_CD"] == b) & (cw_self["CHILD_TRDAR_CD"] == a))
                ]
                if len(hit) > 0:
                    found += 1
        verified_merge = f"{found}/{total}"

    results.append({
        "임대료_지역": row["임대료_지역"],
        "대표결정방식": row["대표결정방식"],
        "n_codes": len(codes),
        "point기반_소속행정동": row["소속_행정동"],
        "crosswalk_dominant_행정동": dominant_dong,
        "dominant_share": round(dominant_share, 3) if dominant_share is not None else None,
        "행정동_일치": row["소속_행정동"] == dominant_dong,
        "겹치는_행정동_개수": n_dong_touched,
        "point기반_상권배후지_있음": pd.notna(row["소속_상권배후지"]),
        "crosswalk_상권배후지_코드매칭": has_alley_code,
        "상권배후지_판정_일치": pd.notna(row["소속_상권배후지"]) == has_alley_code,
        "합집합_실제공간겹침": verified_merge,
    })

out = pd.DataFrame(results)
out_path = os.path.join(OUT, "rent_crosswalk_overlap_check.csv")
out.to_csv(out_path, index=False, encoding="utf-8-sig")
print(f"saved: {out_path} ({len(out)}행)\n")

print("=== (a) 행정동: point-in-polygon(대표좌표) vs crosswalk(면적 1위) ===")
print(out["행정동_일치"].value_counts().to_string())
print(f"겹치는 행정동 100% 존재(0개 없음): {(out['겹치는_행정동_개수'] > 0).all()}")
print()
print("불일치 9건 (대표좌표가 최대면적 동이 아닌 다른 동에 위치):")
print(out[~out["행정동_일치"]][
    ["임대료_지역", "point기반_소속행정동", "crosswalk_dominant_행정동", "dominant_share", "겹치는_행정동_개수"]
].to_string(index=False))
print()

print("=== (b) 상권배후지 존재: point-in-polygon(공간) vs crosswalk(코드매칭) ===")
print(pd.crosstab(out["point기반_상권배후지_있음"], out["crosswalk_상권배후지_코드매칭"]))
print()

print("=== (c) 합집합(19건) 대표결정 후보들의 실제 공간적 겹침 검증 ===")
merged = out[out["n_codes"] > 1]
print(merged[["임대료_지역", "합집합_실제공간겹침"]].to_string(index=False))
n_overlap = (merged["합집합_실제공간겹침"] != "0/1").sum() if len(merged) else 0
print(f"실제로 겹치는(overlap) 쌍이 하나라도 있는 케이스: {n_overlap}/{len(merged)}")
