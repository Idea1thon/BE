"""정비사업 정보몽땅 '사업장목록' → 정리 CSV + 자치구 요약. 프로파일 FC-51 보조.

원천: 정비사업 정보몽땅 > 사업장검색 > 사업장목록.xls (재개발·재건축 조합 목록,
      자치구·사업구분·진행단계·대표지번·자료공개현황). 좌표 없음.

도시계획사업 shp(UQ120)가 공간 정보의 정본이고, 이 파일은 조합 운영단계·자료충실도
보조 지표로만 쓴다.

산출: data/도시계획사업/정비사업조합_목록.csv, 정비사업조합_자치구요약.csv

실행: .venv/bin/python3 scripts/ingest_redev_associations.py [--src 사업장목록.xls]
"""
from __future__ import annotations
import csv, os, sys, collections
import xlrd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _raw import raw
SRC = os.path.expanduser(sys.argv[sys.argv.index("--src") + 1]) if "--src" in sys.argv \
      else raw("도시계획사업", "사업장목록.xls")
OUT_DIR = os.path.join(ROOT, "data", "도시계획사업")
AREA = os.path.join(ROOT, "data", "영역", "상권", "서울시 상권분석서비스(영역-상권).csv")


def gu_codes():
    m = {}
    with open(AREA, encoding="cp949", newline="") as f:
        rd = csv.reader(f)
        H = {c: i for i, c in enumerate(next(rd))}
        for r in rd:
            m[r[H["자치구_코드_명"]].strip()] = r[H["자치구_코드"]].strip()
    return m


def main() -> int:
    if not os.path.isfile(SRC):
        print(f"FAIL: {SRC} 없음")
        return 1
    wb = xlrd.open_workbook(SRC)
    sh = wb.sheet_by_index(0)
    # 헤더 행 찾기
    hdr_row = next(r for r in range(sh.nrows) if "자치구" in [sh.cell_value(r, c) for c in range(sh.ncols)])
    header = [str(sh.cell_value(hdr_row, c)).strip() for c in range(sh.ncols)]
    H = {c: i for i, c in enumerate(header)}
    gmap = gu_codes()

    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []
    summ = collections.Counter()
    for r in range(hdr_row + 1, sh.nrows):
        gu = str(sh.cell_value(r, H["자치구"])).strip()
        if not gu:
            continue
        gubun = str(sh.cell_value(r, H["사업구분"])).strip()
        name = str(sh.cell_value(r, H["사업장명"])).strip()
        jibun = str(sh.cell_value(r, H["대표지번"])).strip()
        stage = str(sh.cell_value(r, H["진행단계"])).strip()
        op_stage = str(sh.cell_value(r, H.get("운영단계", -1))).strip() if "운영단계" in H else ""
        rows.append([gu, gmap.get(gu, ""), gubun, name, jibun, stage, op_stage])
        summ[(gu, gubun, stage)] += 1

    lst = os.path.join(OUT_DIR, "정비사업조합_목록.csv")
    with open(lst, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["자치구", "자치구_코드", "사업구분", "사업장명", "대표지번", "진행단계", "운영단계"])
        w.writerows(sorted(rows))

    su = os.path.join(OUT_DIR, "정비사업조합_자치구요약.csv")
    with open(su, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["자치구", "사업구분", "진행단계", "건수"])
        for (gu, gb, st), n in sorted(summ.items()):
            w.writerow([gu, gb, st, n])

    print(f"조합 {len(rows):,}건  자치구 {len({r[0] for r in rows})}")
    print(f"사업구분: {dict(collections.Counter(r[2] for r in rows).most_common())}")
    print(f"진행단계 상위: {dict(collections.Counter(r[5] for r in rows).most_common(10))}")
    print(f"자치구 코드 매칭 실패: {sorted({r[0] for r in rows if not r[1]}) or '없음'}")
    print(f"→ {os.path.relpath(lst, ROOT)}\n→ {os.path.relpath(su, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
