import csv
import os

import shapefile
from shapely.geometry import shape
from shapely.strtree import STRtree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "영역")
OUT = os.path.join(ROOT, "output", "crosswalks")
os.makedirs(OUT, exist_ok=True)

MIN_OVERLAP_AREA = 1.0  # 경계선 접촉 등 노이즈성 겹침 제외 기준(㎡)


def load_shp(path):
    sf = shapefile.Reader(path, encoding="utf-8")
    fields = [f[0] for f in sf.fields if f[0] != "DeletionFlag"]
    items = []
    for sr in sf.iterShapeRecords():
        geom = shape(sr.shape.__geo_interface__)
        rec = dict(zip(fields, sr.record))
        items.append((geom, rec))
    return items


def write_csv(path, rows, fieldnames):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{path}: {len(rows)}행")


def build_trdar_dong_crosswalk(trda, dong):
    """상권 <-> 행정동: 라벨(ADSTRD_CD) 대신 실제 지오메트리 겹침을 면적비율로 기록."""
    dong_tree = STRtree([g for g, r in dong])
    rows = []
    for g, r in trda:
        for j in dong_tree.query(g):
            j = int(j)
            dg, dr = dong[j]
            if not g.intersects(dg):
                continue
            inter_area = g.intersection(dg).area
            if inter_area <= MIN_OVERLAP_AREA:
                continue
            rows.append({
                "TRDAR_CD": r["TRDAR_CD"],
                "TRDAR_CD_NM": r["TRDAR_CD_N"],
                "TRDAR_SE_NM": r["TRDAR_SE_1"],
                "ADSTRD_CD": dr["ADSTRD_CD"],
                "ADSTRD_NM": dr["ADSTRD_NM"],
                "overlap_area": round(inter_area, 2),
                "trdar_area": round(g.area, 2),
                "dong_area": round(dg.area, 2),
                "ratio_of_trdar": round(inter_area / g.area, 4),
                "ratio_of_dong": round(inter_area / dg.area, 4),
                "is_label_match": r["ADSTRD_CD"] == dr["ADSTRD_CD"],
            })
    rows.sort(key=lambda x: (x["TRDAR_CD"], -x["ratio_of_trdar"]))
    return rows


def build_alley_dong_crosswalk(back, dong):
    """상권배후지 <-> 행정동: 자치구/행정동별 배후지 멤버십 판별용 겹침 면적/비율."""
    dong_tree = STRtree([g for g, r in dong])
    rows = []
    for g, r in back:
        for j in dong_tree.query(g):
            j = int(j)
            dg, dr = dong[j]
            if not g.intersects(dg):
                continue
            inter_area = g.intersection(dg).area
            if inter_area <= MIN_OVERLAP_AREA:
                continue
            rows.append({
                "ALLEY_TRDA": r["ALLEY_TRDA"],
                "ALLEY_TRDA_NM": r["ALLEY_TR_1"],
                "ADSTRD_CD": dr["ADSTRD_CD"],
                "ADSTRD_NM": dr["ADSTRD_NM"],
                "overlap_area": round(inter_area, 2),
                "alley_area": round(g.area, 2),
                "dong_area": round(dg.area, 2),
                "ratio_of_alley": round(inter_area / g.area, 4),
                "ratio_of_dong": round(inter_area / dg.area, 4),
                "is_label_match": r["ADSTRD_CD"] == dr["ADSTRD_CD"],
            })
    rows.sort(key=lambda x: (x["ALLEY_TRDA"], -x["ratio_of_alley"]))
    return rows


def build_alley_self_overlap(back):
    """상권배후지 <-> 상권배후지 자기겹침: 인접 배후지 데이터 결합용 근거 테이블."""
    geoms = [g for g, r in back]
    tree = STRtree(geoms)
    seen = set()
    rows = []
    for i, g in enumerate(geoms):
        for j in tree.query(g):
            j = int(j)
            if j <= i or (i, j) in seen:
                continue
            seen.add((i, j))
            other, r_j = back[j]
            if not g.intersects(other):
                continue
            inter_area = g.intersection(other).area
            if inter_area <= MIN_OVERLAP_AREA:
                continue
            r_i = back[i][1]
            rows.append({
                "ALLEY_TRDA_A": r_i["ALLEY_TRDA"],
                "ALLEY_TRDA_A_NM": r_i["ALLEY_TR_1"],
                "ALLEY_TRDA_B": r_j["ALLEY_TRDA"],
                "ALLEY_TRDA_B_NM": r_j["ALLEY_TR_1"],
                "overlap_area": round(inter_area, 2),
                "alley_a_area": round(g.area, 2),
                "alley_b_area": round(other.area, 2),
                "ratio_of_a": round(inter_area / g.area, 4),
                "ratio_of_b": round(inter_area / other.area, 4),
            })
    rows.sort(key=lambda x: -x["overlap_area"])
    return rows


def build_trdar_alley_map(trda, back):
    """상권 -> 배후지 대응: 코드 매칭 + 포함비율, 배후지 없는 상권은 결측 플래그."""
    back_by_code = {r["ALLEY_TRDA"]: (g, r) for g, r in back}
    rows = []
    for g, r in trda:
        code = r["TRDAR_CD"]
        match = back_by_code.get(code)
        if match is None:
            rows.append({
                "TRDAR_CD": code,
                "TRDAR_CD_NM": r["TRDAR_CD_N"],
                "ALLEY_TRDA": None,
                "has_alley": False,
                "trdar_area": round(g.area, 2),
                "alley_area": None,
                "containment_ratio": None,
            })
            continue
        bg, br = match
        inter_area = g.intersection(bg).area
        rows.append({
            "TRDAR_CD": code,
            "TRDAR_CD_NM": r["TRDAR_CD_N"],
            "ALLEY_TRDA": code,
            "has_alley": True,
            "trdar_area": round(g.area, 2),
            "alley_area": round(bg.area, 2),
            "containment_ratio": round(inter_area / g.area, 4),
        })
    return rows


def build_trdar_alley_spatial_overlap(trda, back):
    """상권 <-> 상권배후지: 코드 identity가 아니라 실제 지오메트리 겹침을 면적비율로 기록.

    build_trdar_alley_map()은 같은 TRDAR_CD/ALLEY_TRDA 코드를 가진 배후지가 있는지만
    보는 1:1 identity 매칭이라, 코드가 다른 배후지 위에 상권이 걸쳐 있는 경우(흔함 —
    상권배후지가 서울 전역을 덮지 않으므로 여러 상권이 이웃 상권의 배후지와 겹칠 수 있음)를
    놓친다. 여기서는 trdar_dong 크로스워크와 동일한 방식으로 전수 공간 교차를 계산해,
    상권 하나가 여러 배후지에 면적비중으로 걸쳐 있는 경우까지 전부 기록한다.
    """
    back_tree = STRtree([g for g, r in back])
    rows = []
    for g, r in trda:
        for j in back_tree.query(g):
            j = int(j)
            bg, br = back[j]
            if not g.intersects(bg):
                continue
            inter_area = g.intersection(bg).area
            if inter_area <= MIN_OVERLAP_AREA:
                continue
            rows.append({
                "TRDAR_CD": r["TRDAR_CD"],
                "TRDAR_CD_NM": r["TRDAR_CD_N"],
                "ALLEY_TRDA": br["ALLEY_TRDA"],
                "ALLEY_TRDA_NM": br["ALLEY_TR_1"],
                "overlap_area": round(inter_area, 2),
                "trdar_area": round(g.area, 2),
                "alley_area": round(bg.area, 2),
                "ratio_of_trdar": round(inter_area / g.area, 4),
                "ratio_of_alley": round(inter_area / bg.area, 4),
                "is_code_match": r["TRDAR_CD"] == br["ALLEY_TRDA"],
            })
    rows.sort(key=lambda x: (x["TRDAR_CD"], -x["ratio_of_trdar"]))
    return rows


def build_trdar_self_overlap(trda):
    """상권 <-> 상권 자기겹침(53쌍): 상위(포함하는 쪽)/하위 계층 관계로 정리."""
    geoms = [g for g, r in trda]
    tree = STRtree(geoms)
    seen = set()
    rows = []
    for i, g in enumerate(geoms):
        for j in tree.query(g):
            j = int(j)
            if j <= i or (i, j) in seen:
                continue
            seen.add((i, j))
            other = geoms[j]
            if not g.intersects(other):
                continue
            inter_area = g.intersection(other).area
            if inter_area <= MIN_OVERLAP_AREA:
                continue
            ri, rj = trda[i][1], trda[j][1]
            ratio_i = inter_area / g.area  # i가 j에 포함되는 비율
            ratio_j = inter_area / other.area  # j가 i에 포함되는 비율
            if ratio_i >= ratio_j:
                parent_rec, child_rec, parent_ratio = rj, ri, ratio_i
            else:
                parent_rec, child_rec, parent_ratio = ri, rj, ratio_j
            rows.append({
                "PARENT_TRDAR_CD": parent_rec["TRDAR_CD"],
                "PARENT_TRDAR_NM": parent_rec["TRDAR_CD_N"],
                "PARENT_SE_NM": parent_rec["TRDAR_SE_1"],
                "CHILD_TRDAR_CD": child_rec["TRDAR_CD"],
                "CHILD_TRDAR_NM": child_rec["TRDAR_CD_N"],
                "CHILD_SE_NM": child_rec["TRDAR_SE_1"],
                "overlap_area": round(inter_area, 2),
                "child_containment_ratio": round(parent_ratio, 4),
            })
    rows.sort(key=lambda x: -x["child_containment_ratio"])
    return rows


def main():
    dong = load_shp(os.path.join(DATA, "행정동", "서울시 상권분석서비스(영역-행정동).shp"))
    trda = load_shp(os.path.join(DATA, "상권", "서울시 상권분석서비스(영역-상권).shp"))
    back = load_shp(os.path.join(DATA, "상권배후지", "서울시 상권분석서비스(영역-상권배후지).shp"))

    print(f"행정동 {len(dong)}개 / 상권 {len(trda)}개 / 상권배후지 {len(back)}개 로드 완료\n")

    trdar_dong = build_trdar_dong_crosswalk(trda, dong)
    write_csv(
        os.path.join(OUT, "crosswalk_trdar_dong.csv"),
        trdar_dong,
        ["TRDAR_CD", "TRDAR_CD_NM", "TRDAR_SE_NM", "ADSTRD_CD", "ADSTRD_NM",
         "overlap_area", "trdar_area", "dong_area", "ratio_of_trdar", "ratio_of_dong", "is_label_match"],
    )

    alley_dong = build_alley_dong_crosswalk(back, dong)
    write_csv(
        os.path.join(OUT, "crosswalk_alley_dong.csv"),
        alley_dong,
        ["ALLEY_TRDA", "ALLEY_TRDA_NM", "ADSTRD_CD", "ADSTRD_NM",
         "overlap_area", "alley_area", "dong_area", "ratio_of_alley", "ratio_of_dong", "is_label_match"],
    )

    alley_overlap = build_alley_self_overlap(back)
    write_csv(
        os.path.join(OUT, "crosswalk_alley_self_overlap.csv"),
        alley_overlap,
        ["ALLEY_TRDA_A", "ALLEY_TRDA_A_NM", "ALLEY_TRDA_B", "ALLEY_TRDA_B_NM",
         "overlap_area", "alley_a_area", "alley_b_area", "ratio_of_a", "ratio_of_b"],
    )

    trdar_alley = build_trdar_alley_map(trda, back)
    write_csv(
        os.path.join(OUT, "crosswalk_trdar_alley.csv"),
        trdar_alley,
        ["TRDAR_CD", "TRDAR_CD_NM", "ALLEY_TRDA", "has_alley", "trdar_area", "alley_area", "containment_ratio"],
    )

    trdar_alley_spatial = build_trdar_alley_spatial_overlap(trda, back)
    write_csv(
        os.path.join(OUT, "crosswalk_trdar_alley_spatial.csv"),
        trdar_alley_spatial,
        ["TRDAR_CD", "TRDAR_CD_NM", "ALLEY_TRDA", "ALLEY_TRDA_NM",
         "overlap_area", "trdar_area", "alley_area", "ratio_of_trdar", "ratio_of_alley", "is_code_match"],
    )

    trdar_self = build_trdar_self_overlap(trda)
    write_csv(
        os.path.join(OUT, "crosswalk_trdar_self_overlap.csv"),
        trdar_self,
        ["PARENT_TRDAR_CD", "PARENT_TRDAR_NM", "PARENT_SE_NM", "CHILD_TRDAR_CD", "CHILD_TRDAR_NM", "CHILD_SE_NM",
         "overlap_area", "child_containment_ratio"],
    )

    # 요약 통계 (전처리 가정 근거용)
    by_trdar = {}
    for r in trdar_dong:
        by_trdar.setdefault(r["TRDAR_CD"], []).append(r)

    n_split_trdar = sum(1 for rows in by_trdar.values() if len(rows) > 1)
    n_label_mismatch = 0
    n_no_majority_60 = 0
    for rows in by_trdar.values():
        top = max(rows, key=lambda x: x["ratio_of_trdar"])
        if not top["is_label_match"]:
            n_label_mismatch += 1
        if top["ratio_of_trdar"] < 0.6:
            n_no_majority_60 += 1

    by_alley = {}
    for r in alley_dong:
        by_alley.setdefault(r["ALLEY_TRDA"], []).append(r)
    n_split_alley = sum(1 for rows in by_alley.values() if len(rows) > 1)

    n_no_alley = sum(1 for r in trdar_alley if not r["has_alley"])
    print("\n=== 요약 ===")
    print(f"상권-행정동 겹침 행 수: {len(trdar_dong)}")
    print(f"행정동 경계를 걸친 상권 수: {n_split_trdar} / {len(by_trdar)}")
    print(f"기존 라벨과 최대면적 행정동이 다른 상권 수: {n_label_mismatch}")
    print(f"60% 이상 단일 행정동에 속하지 않는 상권 수: {n_no_majority_60}")
    print(f"배후지 없는 상권 수: {n_no_alley} / {len(trdar_alley)}")
    print(f"배후지-행정동 겹침 행 수: {len(alley_dong)}")
    print(f"행정동 경계를 걸친 배후지 수: {n_split_alley} / {len(by_alley)}")
    print(f"배후지 자기겹침 쌍 수: {len(alley_overlap)}")
    print(f"상권 자기겹침(계층) 쌍 수: {len(trdar_self)}")


if __name__ == "__main__":
    main()
