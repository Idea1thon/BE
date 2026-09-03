"""식품 인허가(일반음식점·휴게음식점, 서울) → 업소 정규화 + 상권×업종×분기 패널.

원천: 공공데이터포털 행정안전부_식품_일반음식점(15045016)·휴게음식점(15006730)
      서울 필터본 CSV, 39열, cp949. 좌표계 EPSG:5174(중부원점 TM), 좌표에 후행 공백.
      폐업 업소도 폐업일자와 함께 보존(1976~). 인허가일자 1900 등 불량치 존재.

산출(집계본만 커밋, 원천은 .gitignore):
  data/인허가/음식점_인허가_서울.csv        — 업소 단위 정규화(상권 배정 포함)
  data/인허가/음식점_상권분기_패널.csv       — 상권×업종×분기: 영업중·신규개업·폐업

실행: .venv/bin/python3 scripts/ingest_food_license.py [--src DIR]
"""
from __future__ import annotations
import csv, json, os, sys, collections
import shapefile
from shapely.geometry import shape, Point
from shapely.strtree import STRtree
from pyproj import Transformer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _raw import raw
SRC = os.path.expanduser(sys.argv[sys.argv.index("--src") + 1]) if "--src" in sys.argv \
      else raw("식품인허가")
OUT_DIR = os.path.join(ROOT, "data", "인허가")
SHP = os.path.join(ROOT, "data", "영역", "상권", "서울시 상권분석서비스(영역-상권)")
ONTOLOGY = os.path.join(ROOT, "data", "ontology", "업종_검색키워드.json")

FILES = {
    "일반": "식품_일반음식점_서울특별시.csv",
    "휴게": "식품_휴게음식점_서울특별시.csv",
}


def load_uptae_map() -> dict:
    """온톨로지 data/ontology/업종_검색키워드.json 의 인허가_업태 → 10개 업종 코드."""
    onto = json.load(open(ONTOLOGY, encoding="utf-8"))["업종"]
    m = {}
    for code, meta in onto.items():
        for u in meta.get("인허가_업태", []):
            m[u] = code
    # 온톨로지에 없지만 표기 변형이 실데이터에 있는 것 보정
    m.setdefault("외국음식전문점(인도,태국등)", "CS100004")
    m.setdefault("일식/횟집", "CS100003")
    return m


UPTAE_MAP = load_uptae_map()

Q_MIN, Q_MAX = "20211", "20263"   # 패널 대상 분기 범위 (프로젝트 창 + 최신)
# 스냅샷 기준일(최대 갱신시점/인허가일)로 마지막 "완전" 분기를 판정. 이후는 "부분".
LAST_FULL_Q = "20262"   # 2026-08 스냅샷 기준: 20263(7~9월)은 8월까지만 → 부분


def q_of(datestr: str) -> str | None:
    d = datestr.strip()[:10]
    if len(d) < 7 or not d[:4].isdigit():
        return None
    y = int(d[:4]); m = int(d[5:7]) if d[5:7].isdigit() else 0
    if y < 2000 or y > 2035 or not (1 <= m <= 12):
        return None
    return f"{y}{(m - 1) // 3 + 1}"


def quarters(a: str, b: str):
    y, q = int(a[:4]), int(a[4])
    while f"{y}{q}" <= b:
        yield f"{y}{q}"
        q += 1
        if q > 4:
            y += 1; q = 1


def load_areas():
    sf = shapefile.Reader(SHP, encoding="utf-8")
    fields = [f[0] for f in sf.fields if f[0] != "DeletionFlag"]
    geoms, codes, names = [], [], []
    for sr in sf.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        geoms.append(shape(sr.shape.__geo_interface__))
        codes.append(str(rec["TRDAR_CD"]))
        names.append(rec["TRDAR_CD_N"])
    return STRtree(geoms), geoms, codes, names


def main() -> int:
    for name, fn in FILES.items():
        if not os.path.isfile(os.path.join(SRC, fn)):
            print(f"FAIL: {os.path.join(SRC, fn)} 없음")
            return 1

    print("상권 폴리곤 로드 + 좌표 변환기(5174→5181) 준비...")
    tree, geoms, codes, names = load_areas()
    tf = Transformer.from_crs("EPSG:5174", "EPSG:5181", always_xy=True)

    os.makedirs(OUT_DIR, exist_ok=True)
    biz_path = os.path.join(OUT_DIR, "음식점_인허가_서울.csv")
    stat = collections.Counter()
    uptae_unmapped = collections.Counter()
    # 패널 누적: (상권, 업종) -> {분기: [신규, 폐업]}  + 영업중 계산용 개별 리스트
    open_events = collections.defaultdict(lambda: collections.Counter())   # (area,ind) -> {분기: n}
    close_events = collections.defaultdict(lambda: collections.Counter())
    active_spans = collections.defaultdict(list)   # (area,ind) -> [(open_q, close_q or None)]
    n_coord_ok = n_coord_missing = n_area_hit = n_area_miss = 0
    dxdy = []

    with open(biz_path, "w", encoding="utf-8-sig", newline="") as bf:
        bw = csv.writer(bf)
        bw.writerow(["원천", "관리번호", "업태구분명", "업종코드", "인허가일자", "폐업일자",
                     "영업상태명", "인허가_분기", "폐업_분기", "자치구",
                     "좌표X_5181", "좌표Y_5181", "상권_코드", "상권_명"])
        for src, fn in FILES.items():
            with open(os.path.join(SRC, fn), encoding="cp949", newline="") as f:
                rd = csv.reader(f)
                H = {c: i for i, c in enumerate(next(rd))}
                for r in rd:
                    if len(r) < 39:
                        stat["행오류"] += 1
                        continue
                    stat["총"] += 1
                    uptae = r[H["업태구분명"]].strip()
                    ind = UPTAE_MAP.get(uptae, "")
                    if not ind:
                        uptae_unmapped[uptae] += 1
                    oq = q_of(r[H["인허가일자"]])
                    cq = q_of(r[H["폐업일자"]]) if r[H["폐업일자"]].strip() else None
                    addr = r[H["도로명주소"]] or r[H["지번주소"]]
                    gu = ""
                    if "서울특별시 " in addr:
                        parts = addr.split("서울특별시 ", 1)[1].split()
                        if parts and parts[0].endswith("구"):
                            gu = parts[0]
                    xs, ys = r[H["좌표정보(X)"]].strip(), r[H["좌표정보(Y)"]].strip()
                    area_code = area_name = ""
                    X5 = Y5 = ""
                    if xs and ys:
                        try:
                            x5, y5 = tf.transform(float(xs), float(ys))
                            X5, Y5 = round(x5, 2), round(y5, 2)
                            n_coord_ok += 1
                            pt = Point(x5, y5)
                            hit = None
                            for idx in tree.query(pt):
                                if geoms[idx].contains(pt):
                                    hit = idx
                                    break
                            if hit is not None:
                                area_code, area_name = codes[hit], names[hit]
                                n_area_hit += 1
                                if len(dxdy) < 2000:
                                    dxdy.append((float(xs) - x5, float(ys) - y5))
                            else:
                                n_area_miss += 1
                        except (ValueError, OverflowError):
                            n_coord_missing += 1
                    else:
                        n_coord_missing += 1

                    bw.writerow([src, r[H["관리번호"]], uptae, ind,
                                 r[H["인허가일자"]][:10], (r[H["폐업일자"]][:10] if cq or r[H["폐업일자"]].strip() else ""),
                                 r[H["영업상태명"]], oq or "", cq or "", gu,
                                 X5, Y5, area_code, area_name])

                    # 패널 (상권·업종·분기 유효할 때만)
                    if ind and area_code and oq and oq <= Q_MAX:
                        key = (area_code, ind)
                        if Q_MIN <= oq <= Q_MAX:
                            open_events[key][oq] += 1
                        if cq and Q_MIN <= cq <= Q_MAX:
                            close_events[key][cq] += 1
                        active_spans[key].append((oq, cq))

    # 영업중 계산 → 패널 작성
    panel_path = os.path.join(OUT_DIR, "음식점_상권분기_패널.csv")
    all_qs = list(quarters(Q_MIN, Q_MAX))
    keys = set(open_events) | set(close_events) | set(active_spans)
    with open(panel_path, "w", encoding="utf-8-sig", newline="") as pf:
        pw = csv.writer(pf)
        pw.writerow(["기준_년분기_코드", "상권_코드", "업종코드",
                     "영업중_수", "신규개업_수", "폐업_수", "개업률", "폐업률", "분기_상태"])
        for (area, ind) in sorted(keys):
            spans = active_spans[(area, ind)]
            prev_active = None
            for q in all_qs:
                active = sum(1 for (o, c) in spans if o <= q and (c is None or c > q))
                opened = open_events[(area, ind)].get(q, 0)
                closed = close_events[(area, ind)].get(q, 0)
                base = prev_active if prev_active else active
                orate = round(opened / base * 100, 2) if base else ""
                crate = round(closed / base * 100, 2) if base else ""
                st = "완전" if q <= LAST_FULL_Q else "부분"
                pw.writerow([q, area, ind, active, opened, closed, orate, crate, st])
                prev_active = active

    # 리포트
    print(f"\n=== 정합성 검사 ===")
    print(f"총 업소 {stat['총']:,} (행오류 {stat['행오류']})")
    print(f"좌표 변환 성공 {n_coord_ok:,} / 결측·불량 {n_coord_missing:,} ({n_coord_missing/stat['총']*100:.1f}%)")
    print(f"상권 배정 {n_area_hit:,} / 서울 내 미배정(상권 폴리곤 밖) {n_area_miss:,}")
    if dxdy:
        import statistics
        dx = [d[0] for d in dxdy]; dy = [d[1] for d in dxdy]
        print(f"5174→5181 이동량(m): dx μ={statistics.mean(dx):.1f} dy μ={statistics.mean(dy):.1f}  (0에 가까우면 좌표계 동일)")
    top_un = uptae_unmapped.most_common(12)
    print(f"업종 매핑 안 된 업태 상위: {top_un}")
    print(f"매핑된 업소 비율: {(stat['총'] - sum(uptae_unmapped.values())) / stat['총'] * 100:.1f}%")
    print(f"\n산출:")
    for p in (biz_path, panel_path):
        print(f"  {os.path.relpath(p, ROOT)}  {os.path.getsize(p)/1e6:.1f}MB")
    # 패널 커버리지
    parea = {a for a, _ in keys}
    print(f"패널: 상권 {len(parea)}/1650, (상권×업종×분기) 행 ≈ {len(keys) * len(all_qs):,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
