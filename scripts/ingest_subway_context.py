"""도시철도 보조 데이터 → 역 출입구 요약 + 서울 도시철도망 구축계획(계획 노선).

원천(`~/Documents/서울창업입지_원천데이터/도시철도역사/`):
  지하철출입구_버스연계.csv (t-data `tnSubwayEntrc.csv`)
      컬럼: 지하철역ID·출입구번호·정류장ID·주변건물·출구와정류장간이동거리·지하철역X좌표·지하철역Y좌표(EPSG:5181)
      주의: X/Y 는 연계 '버스정류장' 좌표(출입구 좌표 아님). 역당 출입구 수·연계 정류장 수·주변건물만 추출.
  전체_도시철도노선정보_20260630.xlsx (15013203) — 운영 노선 로스터. 참고용(이식 안 함).

제2차 서울특별시 도시철도망 구축계획(관보 제19878호, 2020-11-17) — 계획 노선 10 + 조건부 1.
  정거장·노선 위치는 '기본계획·실시설계로 확정될 예정'(관보 주1). 좌표 없음 → 자치구 경유 신호로만.

산출:
  data/도시철도역사/역출입구_요약.csv        — 역명(본 프로젝트 매칭) × 출입구 수 × 연계 정류장 수
  data/도시철도역사/도시철도망계획_노선.csv   — 계획 노선 개요(관보)
  data/도시철도역사/도시철도망계획_자치구.csv — 자치구별 통과 계획 노선 수

실행: .venv/bin/python3 scripts/ingest_subway_context.py
"""
from __future__ import annotations
import csv, collections, os, statistics, sys

import shapefile
from shapely.geometry import shape, Point
from shapely.strtree import STRtree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from _raw import raw

SRC_DIR = os.path.expanduser(sys.argv[sys.argv.index("--src") + 1]) if "--src" in sys.argv \
          else raw("도시철도역사")
ENTRC = os.path.join(SRC_DIR, "지하철출입구_버스연계.csv")
OUT_DIR = os.path.join(ROOT, "data", "도시철도역사")
STATIONS = os.path.join(OUT_DIR, "역사정보_서울.csv")

# 제2차 서울특별시 도시철도망 구축계획 (관보 제19878호, 2020-11-17). 계획기간 2021~2030.
# 주요경유지 = 관보 표기 자치구. 좌표·정거장 미확정.
# 노선유형: 신설=새 노선, 연장=기존 노선 연장, 운행개선=급행·직결(새 역세권 없음)
PLAN_LINES = [
    # 연번, 노선명, 기점, 종점, [경유 자치구], 규모_km, 구분, 노선유형
    (1, "강북횡단선", "청량리역", "목동역",
     ["동대문구", "성북구", "종로구", "서대문구", "은평구", "마포구", "강서구", "양천구"], 25.72, "선정", "신설"),
    (2, "서부선", "새절역", "서울대입구역",
     ["은평구", "서대문구", "마포구", "영등포구", "동작구", "관악구"], 15.77, "선정", "신설"),
    (3, "목동선", "신월동", "당산역", ["양천구", "영등포구"], 10.87, "선정", "신설"),
    (4, "면목선", "청량리역", "신내역", ["동대문구", "중랑구"], 9.05, "선정", "신설"),
    (5, "난곡선", "보라매공원", "난향동", ["동작구", "관악구"], 4.08, "선정", "신설"),
    (6, "우이신설연장선", "우이동", "방학역", ["강북구", "도봉구"], 3.50, "선정", "연장"),
    (7, "서부선남부연장", "서울대입구역", "서울대 정문", ["관악구"], 1.72, "선정", "연장"),
    (8, "신림선북부연장", "샛강역", "여의도", ["영등포구"], 0.34, "선정", "연장"),
    (9, "4호선 급행", "당고개역", "남태령역",
     ["노원구", "도봉구", "강북구", "성북구", "동대문구", "중구", "용산구", "동작구", "서초구", "관악구"], None, "선정", "운행개선"),
    (10, "5호선 직결", "둔촌동역", "굽은다리역", ["강동구"], None, "선정", "운행개선"),
    (11, "9호선 4단계 추가연장", "고덕강일1", "강일", ["강동구"], 1.25, "조건부", "연장"),
]


def load_trdar():
    sf = shapefile.Reader(os.path.join(ROOT, "data", "영역", "상권", "서울시 상권분석서비스(영역-상권)"),
                          encoding="utf-8")
    fields = [f[0] for f in sf.fields if f[0] != "DeletionFlag"]
    geoms, names = [], []
    for sr in sf.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        geoms.append(shape(sr.shape.__geo_interface__))
        names.append((rec["TRDAR_CD_N"], rec["SIGNGU_CD_"]))
    return STRtree(geoms), geoms, names


def build_exit_summary():
    if not os.path.isfile(ENTRC):
        print(f"skip 출입구: {ENTRC} 없음")
        return
    if not os.path.isfile(STATIONS):
        print(f"skip 출입구: {STATIONS} 없음 (ingest_subway_stations.py 먼저)")
        return
    # 본 프로젝트 역 좌표(5181)
    st = list(csv.DictReader(open(STATIONS, encoding="utf-8-sig")))
    st_pts = [Point(float(r["X_5181"]), float(r["Y_5181"])) for r in st]
    st_tree = STRtree(st_pts)

    # tnSubwayEntrc: 역ID -> {출입구번호 집합, 정류장ID 집합, 좌표 리스트, 주변건물 집합}
    agg = collections.defaultdict(lambda: {"exits": set(), "stops": set(), "xy": [], "bldg": collections.Counter()})
    with open(ENTRC, encoding="utf-8-sig", newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for row in rd:
            row = [c.lstrip("﻿").strip() for c in row]
            if len(row) < 7:
                continue
            sid, exno, stopid, bldg, _, xs, ys = row[:7]
            a = agg[sid]
            a["exits"].add(exno)
            if stopid:
                a["stops"].add(stopid)
            try:
                a["xy"].append((float(xs), float(ys)))
            except ValueError:
                pass
            for b in (bldg or "").replace("·", ".").split("."):
                b = b.strip()
                if len(b) >= 2:
                    a["bldg"][b] += 1

    out = []
    n_match = 0
    for sid, a in agg.items():
        if not a["xy"]:
            continue
        cx = statistics.mean(x for x, _ in a["xy"])
        cy = statistics.mean(y for _, y in a["xy"])
        p = Point(cx, cy)
        j = st_tree.nearest(p)
        d = st_pts[j].distance(p)
        matched = st[j]["역사명"] if d <= 600 else ""
        if matched:
            n_match += 1
        top_b = [b for b, _ in a["bldg"].most_common(6)]
        out.append({
            "지하철역ID": sid, "매칭_역사명": matched, "매칭_거리_m": round(d, 1),
            "출입구_수": len(a["exits"]), "연계_버스정류장_수": len(a["stops"]),
            "주변건물_대표": " / ".join(top_b),
        })
    out.sort(key=lambda r: (-r["출입구_수"], r["지하철역ID"]))
    p = os.path.join(OUT_DIR, "역출입구_요약.csv")
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader(); w.writerows(out)
    print(f"역출입구_요약: {len(out)}역 (본 프로젝트 역 매칭 {n_match}), 출입구 최다 {out[0]['출입구_수']} ({out[0]['매칭_역사명']})")
    print(f"→ {p}")


def build_plan_tables():
    lp = os.path.join(OUT_DIR, "도시철도망계획_노선.csv")
    with open(lp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["연번", "노선명", "기점", "종점", "경유_자치구", "규모_km", "구분",
                    "노선유형", "계획기간", "상태_2026", "출처"])
        for no, name, a, b, gus, km, kind, ltype in PLAN_LINES:
            w.writerow([no, name, a, b, ";".join(gus), (km if km is not None else ""),
                        kind, ltype, "2021~2030", "계획(미개통·정거장 위치 미확정)",
                        "제2차 서울특별시 도시철도망 구축계획(관보 제19878호, 2020-11-17)"])
    # 자치구 요약 (신설·연장만 역세권 신호로 카운트, 운행개선 별도)
    gu_new = collections.Counter()
    gu_lines = collections.defaultdict(list)
    for no, name, a, b, gus, km, kind, ltype in PLAN_LINES:
        for g in gus:
            gu_lines[g].append(f"{name}({ltype})")
            if ltype in ("신설", "연장"):
                gu_new[g] += 1
    all_gus = sorted(gu_lines, key=lambda x: (-gu_new[x], x))
    gp = os.path.join(OUT_DIR, "도시철도망계획_자치구.csv")
    with open(gp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["자치구", "신설연장_노선_수", "전체_계획노선_목록", "출처"])
        for g in all_gus:
            w.writerow([g, gu_new[g], ";".join(sorted(set(gu_lines[g]))),
                        "제2차 서울특별시 도시철도망 구축계획(2020-11-17)"])
    print(f"도시철도망계획: 노선 {len(PLAN_LINES)}, 자치구 {len(all_gus)}  → {lp} / {gp}")


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    build_exit_summary()
    build_plan_tables()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
