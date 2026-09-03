"""서울 자치구별 고용률(반기) 이식 — 프로파일 FC-53.

원천: KOSIS 지역별고용조사 '고용률.csv' (서울 25구 × {계,남자,여자} × 반기).
      wide: 행정구역별(1), 성별(1), 2021.1/2, 2021.2/2, ... 2026.1/2. 값 = 고용률(%).

산출: data/고용률/자치구_고용률_반기.csv (long, ~825행)

실행: .venv/bin/python3 scripts/ingest_employment_rate.py [--src 고용률.csv]
"""
from __future__ import annotations
import csv, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _raw import raw
SRC = os.path.expanduser(sys.argv[sys.argv.index("--src") + 1]) if "--src" in sys.argv \
      else raw("고용률", "고용률.csv")
OUT = os.path.join(ROOT, "data", "고용률", "자치구_고용률_반기.csv")
AREA = os.path.join(ROOT, "data", "영역", "상권", "서울시 상권분석서비스(영역-상권).csv")


def gu_code_map() -> dict[str, str]:
    m = {}
    with open(AREA, encoding="cp949", newline="") as f:
        rd = csv.reader(f)
        H = {c: i for i, c in enumerate(next(rd))}
        for r in rd:
            m[r[H["자치구_코드_명"]].strip()] = r[H["자치구_코드"]].strip()
    return m


def norm_period(col: str) -> str:
    # "2021.1/2" -> "2021H1", "2021.2/2" -> "2021H2"
    y, half = col.split(".")
    return f"{y}H{half.split('/')[0]}"


def main() -> int:
    if not os.path.isfile(SRC):
        print(f"FAIL: {SRC} 없음")
        return 1
    enc = "utf-8-sig"
    with open(SRC, encoding=enc, newline="") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    period_cols = [(i, norm_period(c)) for i, c in enumerate(header) if "/" in c]

    gmap = gu_code_map()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    n = 0
    unmapped = set()
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["기준_반기", "자치구", "자치구_코드", "성별", "고용률"])
        for r in rows[1:]:
            region = r[0].strip()          # "서울 종로구"
            sex = r[1].strip()
            gu = region.replace("서울", "").strip()
            code = gmap.get(gu, "")
            if not code:
                unmapped.add(gu)
            for idx, period in period_cols:
                val = r[idx].strip()
                if not val:
                    continue
                w.writerow([period, gu, code, sex, val])
                n += 1

    gus = {r[0].replace("서울", "").strip() for r in rows[1:]}
    periods = [p for _, p in period_cols]
    print(f"→ {os.path.relpath(OUT, ROOT)}  {n}행")
    print(f"  자치구 {len(gus)}개  반기 {periods[0]}~{periods[-1]} ({len(periods)}개)  성별 계/남자/여자")
    print(f"  자치구 코드 매칭 실패: {sorted(unmapped) or '없음'}")
    # 샘플
    print("  샘플(계, 2026H1):")
    with open(OUT, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["성별"] == "계" and row["기준_반기"] == periods[-1]:
                print(f"    {row['자치구']}({row['자치구_코드']}) {row['고용률']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
