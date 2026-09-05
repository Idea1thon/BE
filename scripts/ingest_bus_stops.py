"""서울 + 인접 경기 버스정류소 → 정류소 목록 + 상권별 버스 접근성.

원천(`~/Documents/서울창업입지_원천데이터/버스정류장/`):
  서울_버스정류소_20260804.xlsx   — 서울시 버스정류소 위치정보(OA-15067 스냅샷). NODE_ID·ARS_ID·정류소명·X/Y(WGS84)·정류소타입
  전국_버스정류장_20251031.csv    — 국토교통부 전국 버스정류장(15067528). cp949. 서울 경계 인접 경기·인천 정류장 보존용

정류소는 거의 정적(계단식) — 최근 스냅샷만 사용, 분기 트렌드 금지.

산출(집계본만 커밋):
  data/버스정류장/버스정류소_서울.csv     — 정류소 단위(서울 + 인접), 상권·행정동 배정
  data/버스정류장/상권_버스접근성.csv     — 상권 1,650: 상권내·250m·최근접 정류소, 마을버스/간선(중앙차로) 수

실행: .venv/bin/python3 scripts/ingest_bus_stops.py
"""
from __future__ import annotations
import csv, json, os, sys, unicodedata

import openpyxl
import shapefile
from shapely.geometry import shape, Point
from shapely.strtree import STRtree
from pyproj import Transformer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from _raw import raw

SRC_DIR = os.path.expanduser(sys.argv[sys.argv.index("--src") + 1]) if "--src" in sys.argv \
          else raw("버스정류장")
SEOUL_XLSX = os.path.join(SRC_DIR, "서울_버스정류소_20260804.xlsx")
NATION_CSV = os.path.join(SRC_DIR, "전국_버스정류장_20251031.csv")
OUT_DIR = os.path.join(ROOT, "data", "버스정류장")
TRDAR_SHP = os.path.join(ROOT, "data", "영역", "상권", "서울시 상권분석서비스(영역-상권)")
DONG_SHP = os.path.join(ROOT, "data", "영역", "행정동", "서울시 상권분석서비스(영역-행정동)")

NEAR_M = 250       # 도보권(약 3~4분). 상권↔정류소 이 거리 안이면 "인접"
MARGIN_M = 1000    # 서울 밖이어도 상권에서 이 거리면 인접역처럼 보존
TRUNK_TYPES = {"중앙차로", "일반중앙차로"}      # 간선·중앙버스전용차로
DROP_TYPES = {"가상정류장", "한강선착장"}       # 접근성 지표에서 제외


def load_shp(path, code_field, name_field):
    sf = shapefile.Reader(path, encoding="utf-8")
    fields = [f[0] for f in sf.fields if f[0] != "DeletionFlag"]
    geoms, recs = [], []
    for sr in sf.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        geoms.append(shape(sr.shape.__geo_interface__))
        recs.append((str(rec[code_field]), rec[name_field], rec))
    return STRtree(geoms), geoms, recs


def pip(tree, geoms, recs, pt):
    for idx in tree.query(pt):
        if geoms[idx].covers(pt):
            return recs[idx]
    return None


def read_seoul(tf):
    wb = openpyxl.load_workbook(SEOUL_XLSX, read_only=True)
    ws = wb["Data"]
    rows = ws.iter_rows(values_only=True)
    next(rows)
    out = []
    for r in rows:
        if r[0] is None or not isinstance(r[3], (int, float)) or not isinstance(r[4], (int, float)):
            continue
        typ = str(r[5] or "").strip()
        out.append({"src": "서울", "node_id": str(r[0]), "ars": str(r[1] or ""),
                    "name": str(r[2] or "").strip(), "type": typ,
                    "lon": float(r[3]), "lat": float(r[4])})
    wb.close()
    return out


def read_nation():
    """서울 경계 인접 경기·인천 정류장만 (서울특별시 정류장은 서울 스냅샷이 정본)."""
    out = []
    with open(NATION_CSV, encoding="cp949", newline="") as f:
        for r in csv.DictReader(f):
            try:
                lat = float(r["위도"]); lon = float(r["경도"])
            except (ValueError, KeyError):
                continue
            city = (r.get("도시명") or "")
            if city.startswith("서울"):
                continue
            if not (37.40 <= lat <= 37.72 and 126.74 <= lon <= 127.22):
                continue
            out.append({"src": "인접", "node_id": r.get("정류장번호", ""), "ars": "",
                        "name": (r.get("정류장명") or "").strip(), "type": "인접",
                        "lon": lon, "lat": lat, "city": city})
    return out


def main() -> int:
    for p in (SEOUL_XLSX, NATION_CSV):
        if not os.path.isfile(p):
            print(f"FAIL: {p} 없음")
            return 1
    os.makedirs(OUT_DIR, exist_ok=True)

    tf = Transformer.from_crs("EPSG:4326", "EPSG:5181", always_xy=True)
    print("상권·행정동 폴리곤 로드...")
    d_tree, d_geoms, d_recs = load_shp(DONG_SHP, "ADSTRD_CD", "ADSTRD_NM")
    t_tree, t_geoms, t_recs = load_shp(TRDAR_SHP, "TRDAR_CD", "TRDAR_CD_N")

    seoul_stops = read_seoul(tf)
    nation_stops = read_nation()
    # 경계 BIS가 서울 정류장을 경기 도시명으로 태깅한 중복 제거(서울 정류소 30m 내 인접정류소 버림)
    seoul_pts = STRtree([Point(tf.transform(s["lon"], s["lat"])) for s in seoul_stops])
    kept = []
    for s in nation_stops:
        p = Point(tf.transform(s["lon"], s["lat"]))
        j = seoul_pts.nearest(p)
        if seoul_pts.geometries[j].distance(p) <= 30:
            continue
        kept.append(s)
    dup_removed = len(nation_stops) - len(kept)
    stops = seoul_stops + kept
    print(f"원천 정류소: 서울 {len(seoul_stops)} + 인접 {len(nation_stops)} (서울 중복 {dup_removed} 제거)")

    near = {code: [] for code, _, _ in t_recs}   # code -> [(dist, name, type, inside)]
    rows_out = []
    seoul_n = margin_n = drop_n = dropped_type = 0

    for s in stops:
        if s["type"] in DROP_TYPES:
            dropped_type += 1
            continue
        x, y = tf.transform(s["lon"], s["lat"])
        pt = Point(x, y)
        dong = pip(d_tree, d_geoms, d_recs, pt)
        if dong is None:
            nidx = t_tree.nearest(pt)
            if t_geoms[nidx].distance(pt) > MARGIN_M:
                drop_n += 1
                continue
            margin_n += 1
        else:
            seoul_n += 1
        trdar = pip(t_tree, t_geoms, t_recs, pt)
        rows_out.append({
            "정류소구분": s["src"], "NODE_ID": s["node_id"], "ARS_ID": s["ars"],
            "정류소명": s["name"], "정류소타입": s["type"],
            "위도": round(s["lat"], 7), "경도": round(s["lon"], 7),
            "X_5181": round(x, 2), "Y_5181": round(y, 2),
            "자치구코드": dong[2]["ADSTRD_CD"][:5] if dong else "",
            "행정동_코드": dong[0] if dong else "", "행정동명": dong[1] if dong else "",
            "상권_코드": trdar[0] if trdar else "", "상권명": trdar[1] if trdar else "",
        })
        buf = pt.buffer(NEAR_M)
        for gi in t_tree.query(buf):
            d = t_geoms[gi].distance(pt)
            if d <= NEAR_M:
                near[t_recs[gi][0]].append((round(d, 1), s["name"], s["type"], d == 0.0))

    # 정류소_서울.csv
    st_path = os.path.join(OUT_DIR, "버스정류소_서울.csv")
    cols = ["정류소구분", "NODE_ID", "ARS_ID", "정류소명", "정류소타입", "위도", "경도",
            "X_5181", "Y_5181", "자치구코드", "행정동_코드", "행정동명", "상권_코드", "상권명"]
    rows_out.sort(key=lambda r: (r["자치구코드"], r["정류소명"]))
    with open(st_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader(); w.writerows(rows_out)

    # 상권_버스접근성.csv
    sg_path = os.path.join(OUT_DIR, "상권_버스접근성.csv")
    n_with = 0
    with open(sg_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["상권_코드", "상권명", "자치구", "상권내_정류소_수", "250m내_정류소_수",
                    "최근접_정류소명", "최근접_정류소_거리_m", "마을버스_정류소_수",
                    "간선_중앙차로_정류소_수", "인접정류소_목록", "데이터기준"])
        for code, name, rec in t_recs:
            lst = sorted(near[code])
            inside = sum(1 for d, *_ in lst if d == 0.0)
            maeul = sum(1 for _, _, t, _ in lst if t == "마을버스")
            trunk = sum(1 for _, _, t, _ in lst if t in TRUNK_TYPES)
            if lst:
                n_with += 1
                bd, bn, *_ = lst[0]
                anchors = [{"name": n_, "type": t_, "distance_m": d_} for d_, n_, t_, _ in lst[:6]]
            else:
                bd = bn = ""
                anchors = []
            w.writerow([code, name, rec["SIGNGU_CD_"], inside, len(lst), bn,
                        (bd if bd != "" else ""), maeul, trunk,
                        json.dumps(anchors, ensure_ascii=False), "2026-08-04(서울)+2025-10-31(인접)"])

    print(f"\n정류소 배정: 서울 행정구역 {seoul_n} + 인접 {margin_n} (거리초과 제외 {drop_n}, 타입제외 {dropped_type})")
    print(f"정류소_서울.csv: {len(rows_out)}행  (상권 내부 {sum(1 for r in rows_out if r['상권_코드'])})")
    print(f"상권 {len(t_recs)} 중 {NEAR_M}m 내 정류소 있는 상권: {n_with}")
    print(f"→ {st_path}\n→ {sg_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
