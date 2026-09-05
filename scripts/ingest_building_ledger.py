"""건축물대장 통합정보(GIS건물통합정보, 서울) → 상업용 건물 단위 정규화 + 상권 요약.

원천: 국토교통부 GIS건물통합정보 (공공데이터포털 15083092) 서울 추출본
      ~/Documents/서울창업입지_원천데이터/공동주택/GIS건물통합정보_서울/AL_D010_11_YYYYMMDD.{shp,dbf}
      건물 footprint 폴리곤 + 건축물대장 속성(주용도·층수·면적·사용승인일·지번주소).
      좌표계 EPSG:5186(중부원점 2010, false_northing 600000), dbf 인코딩 cp949.
      아파트 이식(ingest_apartment_complex.py)은 A8='02000'(공동주택)만 사용 —
      이 스크립트는 상업용 주용도(근린생활·판매)를 대상으로 한다.

이건 개별 "매물"이 아니다. GIS건물통합정보가 기록한 **건물 주용도** 기준이므로
층별 용도·호실(전유부)·주차대수·공실·임대료는 없다 (Tier 2 = 건축HUB API 15134735).
주상복합처럼 주용도가 공동주택·업무시설인 건물의 저층부 상가는 여기서 누락된다.

산출(요약본만 커밋, 건물 단위 파일은 .gitignore — 원천에서 재생성 가능):
  data/건축물대장/상가건물_서울.csv        — 상업용 건물 단위 (좌표·용도·규모·상권 배정)
  data/건축물대장/상권_상가건물_요약.csv    — 상권 × 용도군 집계
  data/건축물대장/manifest.json            — 원천·필터·좌표계·커버리지

실행: .venv/bin/python3 scripts/ingest_building_ledger.py [--src DIR] [--include-secondary]
"""
from __future__ import annotations
import csv, glob, json, os, statistics, sys, collections, datetime

import shapefile
from shapely.geometry import shape, Point
from shapely.strtree import STRtree
from pyproj import Transformer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from _raw import raw

SRC_DIR = (
    os.path.expanduser(sys.argv[sys.argv.index("--src") + 1])
    if "--src" in sys.argv
    else raw("공동주택", "GIS건물통합정보_서울")
)
INCLUDE_SECONDARY = "--include-secondary" in sys.argv
LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

OUT_DIR = os.path.join(ROOT, "data", "건축물대장")
TRDAR_SHP = os.path.join(ROOT, "data", "영역", "상권", "서울시 상권분석서비스(영역-상권)")
DONG_SHP = os.path.join(ROOT, "data", "영역", "행정동", "서울시 상권분석서비스(영역-행정동)")

# GIS건물통합정보 A8 주용도 대분류 → 용도군.
# core = 창업 입지에서 직접 상가 후보가 되는 근린생활·판매 계열.
USE_CORE = {
    "03000": "근린생활1",
    "04000": "근린생활2",
    "07000": "판매시설",
    "Z3000": "근린생활",   # 구 코드
    "Z6000": "판매영업",   # 구 코드
}
# secondary = 저층부 상가가 흔하지만 주용도가 상가가 아닌 계열 (--include-secondary 로만)
USE_SECONDARY = {
    "14000": "업무시설",
    "05000": "문화집회시설",
    "15000": "숙박시설",
    "16000": "위락시설",
    "27000": "관광휴게시설",
    "Z5000": "문화집회시설",
}

SNAPSHOT_YEAR = 2026  # AL_D010_11_20260809 기준. manifest 에서 실제 파일명으로 재확인.


def find_source_shp(src_dir: str) -> str:
    cands = sorted(glob.glob(os.path.join(src_dir, "AL_D010_*.shp")))
    if not cands:
        cands = sorted(glob.glob(os.path.join(src_dir, "*.shp")))
    if not cands:
        raise SystemExit(f"원천 shp 없음: {src_dir}/AL_D010_*.shp")
    return cands[-1]


def load_polygons(path: str, code_field: str, name_field: str):
    sf = shapefile.Reader(path, encoding="utf-8")
    fields = [f[0] for f in sf.fields if f[0] != "DeletionFlag"]
    geoms, codes, names = [], [], []
    for sr in sf.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        g = shape(sr.shape.__geo_interface__)
        if not g.is_valid:
            g = g.buffer(0)
        geoms.append(g)
        codes.append(str(rec[code_field]))
        names.append(str(rec[name_field]))
    return STRtree(geoms), geoms, codes, names


def assign_area(pt, tree, geoms, codes, names, near_m: float):
    """contains → 없으면 near_m 이내 최근접. (code, name, 결합유형)."""
    for idx in tree.query(pt):
        if geoms[idx].contains(pt):
            return codes[idx], names[idx], "내부"
    try:
        idx = int(tree.nearest(pt))
    except Exception:
        return "", "", "미결합"
    if geoms[idx].distance(pt) <= near_m:
        return codes[idx], names[idx], "근접"
    return "", "", "미결합"


def year_of(datestr: str):
    d = (datestr or "").strip()[:4]
    if d.isdigit() and 1900 <= int(d) <= SNAPSHOT_YEAR + 1:
        return int(d)
    return None


def num(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def gu_of(addr: str) -> str:
    if "서울특별시 " in addr:
        parts = addr.split("서울특별시 ", 1)[1].split()
        if parts and (parts[0].endswith("구") or parts[0].endswith("시")):
            return parts[0]
    return ""


def main() -> int:
    shp_path = find_source_shp(SRC_DIR)
    fname = os.path.basename(shp_path)
    snap = fname.replace("AL_D010_11_", "").replace(".shp", "")

    use_map = dict(USE_CORE)
    if INCLUDE_SECONDARY:
        use_map.update(USE_SECONDARY)

    print(f"원천: {shp_path}")
    print(f"상권·행정동 폴리곤 로드 (EPSG:5181) ...")
    t_tree, t_g, t_c, t_n = load_polygons(TRDAR_SHP, "TRDAR_CD", "TRDAR_CD_N")
    d_tree, d_g, d_c, d_n = load_polygons(DONG_SHP, "ADSTRD_CD", "ADSTRD_NM")
    tf = Transformer.from_crs("EPSG:5186", "EPSG:5181", always_xy=True)
    tf_wgs = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)

    r = shapefile.Reader(shp_path, encoding="cp949")
    flds = [f[0] for f in r.fields if f[0] != "DeletionFlag"]
    idx = {c: i for i, c in enumerate(flds)}
    total = len(r)
    print(f"GIS 건물 레코드: {total:,}  (상업 주용도 필터 → {'core+secondary' if INCLUDE_SECONDARY else 'core'})")

    os.makedirs(OUT_DIR, exist_ok=True)
    bld_path = os.path.join(OUT_DIR, "상가건물_서울.csv")

    cols = [
        "건물관리번호", "PNU", "시군구코드", "시군구명", "법정동코드", "대지위치", "지번", "지번구분",
        "용도코드", "용도명", "용도군", "지상층수", "지하층수",
        "건축면적_㎡", "연면적_㎡", "건폐율_pct", "용적률_pct", "높이_m", "구조",
        "사용승인일", "건물연식_년", "footprint_㎡", "x_5181", "y_5181", "경도", "위도",
        "상권_코드", "상권_명", "상권_결합", "행정동_코드", "행정동_명",
    ]

    stat = collections.Counter()
    per_area = collections.defaultdict(lambda: collections.defaultdict(list))  # 상권 -> 용도군 -> [row dict]
    ages, gfas = [], []

    with open(bld_path, "w", encoding="utf-8-sig", newline="") as bf:
        bw = csv.writer(bf)
        bw.writerow(cols)
        for n, sr in enumerate(r.iterShapeRecords()):
            if LIMIT and n >= LIMIT:
                break
            if n % 100000 == 0 and n:
                print(f"  {n:,}/{total:,}  (상업 {stat['상업']:,})")
            rec = sr.record
            code = str(rec[idx["A8"]]).strip()
            grp = use_map.get(code)
            if not grp:
                continue
            stat["상업"] += 1

            try:
                g = shape(sr.shape.__geo_interface__)
                if not g.is_valid:
                    g = g.buffer(0)
                c = g.centroid
                if c.is_empty:
                    xmin, ymin, xmax, ymax = sr.shape.bbox
                    cx86, cy86 = (xmin + xmax) / 2, (ymin + ymax) / 2
                    fp = None
                else:
                    cx86, cy86 = c.x, c.y
                    fp = round(g.area, 1)
            except Exception:
                stat["geom오류"] += 1
                continue

            x5, y5 = tf.transform(cx86, cy86)
            lon, lat = tf_wgs.transform(cx86, cy86)
            pt = Point(x5, y5)
            a_c, a_n, a_join = assign_area(pt, t_tree, t_g, t_c, t_n, near_m=150.0)
            dd_c, dd_n, _ = assign_area(pt, d_tree, d_g, d_c, d_n, near_m=50.0)
            if a_join == "내부":
                stat["상권내부"] += 1
            elif a_join == "근접":
                stat["상권근접"] += 1
            else:
                stat["상권밖"] += 1

            addr = str(rec[idx["A4"]]).strip()
            appr = str(rec[idx["A13"]]).strip()[:10]
            yr = year_of(appr)
            age = SNAPSHOT_YEAR - yr if yr else ""
            gfa = num(rec[idx["A14"]])
            floors_up = rec[idx["A26"]] or ""
            floors_dn = rec[idx["A27"]] or ""
            row = [
                str(rec[idx["A1"]]).strip(), str(rec[idx["A2"]]).strip(),
                str(rec[idx["A23"]]).strip(), gu_of(addr), str(rec[idx["A3"]]).strip(),
                addr, str(rec[idx["A5"]]).strip(), str(rec[idx["A7"]]).strip(),
                code, str(rec[idx["A9"]]).strip(), grp, floors_up, floors_dn,
                num(rec[idx["A12"]]) or "", gfa or "",
                num(rec[idx["A17"]]) or "", num(rec[idx["A18"]]) or "",
                num(rec[idx["A16"]]) or "", str(rec[idx["A11"]]).strip(),
                appr, age, fp if fp is not None else "",
                round(x5, 2), round(y5, 2), round(lon, 6), round(lat, 6),
                a_c, a_n, a_join, dd_c, dd_n,
            ]
            bw.writerow(row)

            if a_c:
                per_area[a_c][grp].append({"gfa": gfa, "up": _int(floors_up),
                                           "dn": _int(floors_dn), "age": age if age != "" else None,
                                           "name": a_n})
            if age != "":
                ages.append(age)
            if gfa:
                gfas.append(gfa)

    # ── 상권 × 용도군 요약 ────────────────────────────────────────────
    sum_path = os.path.join(OUT_DIR, "상권_상가건물_요약.csv")
    with open(sum_path, "w", encoding="utf-8-sig", newline="") as sf:
        sw = csv.writer(sf)
        sw.writerow(["상권_코드", "상권_명", "상가건물_수",
                     "근생1_수", "근생2_수", "판매_수", "기타상업_수",
                     "연면적_중위_㎡", "지상층수_중위", "지하층보유_수", "건물연식_중위_년",
                     "신축_10년이내_수", "노후_30년이상_수"])
        for a_c in sorted(per_area):
            groups = per_area[a_c]
            allrows = [x for rs in groups.values() for x in rs]
            name = allrows[0]["name"] if allrows else ""
            gfa_v = [x["gfa"] for x in allrows if x["gfa"]]
            up_v = [x["up"] for x in allrows if x["up"]]
            age_v = [x["age"] for x in allrows if x["age"] is not None]
            sw.writerow([
                a_c, name, len(allrows),
                len(groups.get("근린생활1", [])), len(groups.get("근린생활2", [])),
                len(groups.get("판매시설", [])) + len(groups.get("판매영업", [])),
                sum(len(v) for k, v in groups.items()
                    if k not in ("근린생활1", "근린생활2", "판매시설", "판매영업", "근린생활")),
                round(statistics.median(gfa_v), 1) if gfa_v else "",
                int(statistics.median(up_v)) if up_v else "",
                sum(1 for x in allrows if x["dn"]),
                int(statistics.median(age_v)) if age_v else "",
                sum(1 for a in age_v if a <= 10),
                sum(1 for a in age_v if a >= 30),
            ])

    # ── manifest ─────────────────────────────────────────────────────
    manifest = {
        "source": {
            "name": "국토교통부 GIS건물통합정보 (공공데이터포털 15083092) 서울 추출본",
            "file": fname,
            "snapshot": snap,
            "crs_src": "EPSG:5186",
            "encoding": "cp949",
        },
        "generated_at": datetime.date.today().isoformat(),
        "filter": {
            "use_codes": sorted(use_map),
            "use_groups": use_map,
            "include_secondary": INCLUDE_SECONDARY,
            "note": "GIS건물통합정보의 건물 주용도(A8) 기준. 층별 용도·전유부 호실·주차·공실·임대료 없음.",
        },
        "counts": {
            "gis_records": total,
            "commercial_buildings": stat["상업"],
            "geom_errors": stat["geom오류"],
            "trdar_inside": stat["상권내부"],
            "trdar_near_150m": stat["상권근접"],
            "trdar_outside": stat["상권밖"],
            "trdar_covered": len(per_area),
        },
        "distributions": {
            "building_age_year": _summ(ages),
            "gfa_m2": _summ(gfas),
        },
        "outputs": {
            "building_level": "data/건축물대장/상가건물_서울.csv (gitignore)",
            "trdar_summary": "data/건축물대장/상권_상가건물_요약.csv",
        },
        "limitations": [
            "건물 주용도 기준 — 주상복합 등 주용도가 비상업인 건물의 저층 상가 누락",
            "GIS건물통합정보는 집합건물(구분상가)을 일반건물과 구분하지 않음 — 건물당 footprint 1개, 전유부 호실 없음",
            "footprint 중심좌표이며 개별 호실·매물 위치 아님",
            "사용승인일 결측 건물 존재 → 건물연식 일부 공란",
            "상권 폴리곤 밖(서울 면적의 ~73%) 건물은 상권 미결합",
        ],
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, ensure_ascii=False, indent=2)

    # ── 리포트 ───────────────────────────────────────────────────────
    print(f"\n=== 정합성 검사 ===")
    print(f"GIS 건물 {total:,} → 상업용 {stat['상업']:,}  (geom 오류 {stat['geom오류']})")
    print(f"상권 배정: 내부 {stat['상권내부']:,} / 근접150m {stat['상권근접']:,} / 밖 {stat['상권밖']:,}")
    print(f"상권 커버: {len(per_area)}/1650")
    if ages:
        print(f"건물연식(년): 중위 {statistics.median(ages)} / 10년이내 {sum(1 for a in ages if a<=10):,} / 30년이상 {sum(1 for a in ages if a>=30):,}")
    if gfas:
        print(f"연면적(㎡): 중위 {statistics.median(gfas):.0f} / p90 {statistics.quantiles(gfas, n=10)[-1]:.0f}")
    print(f"\n산출:")
    for p in (bld_path, sum_path, os.path.join(OUT_DIR, "manifest.json")):
        print(f"  {os.path.relpath(p, ROOT)}  {os.path.getsize(p)/1e6:.2f}MB")
    return 0


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _summ(vals):
    if not vals:
        return None
    vals = sorted(vals)
    q = statistics.quantiles(vals, n=10) if len(vals) >= 10 else [vals[0], vals[-1]]
    return {"n": len(vals), "min": round(vals[0], 1), "median": round(statistics.median(vals), 1),
            "p90": round(q[-1], 1), "max": round(vals[-1], 1)}


if __name__ == "__main__":
    sys.exit(main())
