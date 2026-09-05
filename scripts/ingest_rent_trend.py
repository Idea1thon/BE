"""한국부동산원 R-ONE 상업용부동산 임대동향 → 정규화 long 테이블. 프로파일 FC-20·21 보강.

원천: R-ONE '임대동향 지역별 임대료/임대가격지수' CSV 5개
  - 임대료(2024년3분기~)_소규모 상가.csv       천원/㎡, 실측 임대료
  - 임대가격지수(시계열)_{소규모,중대형,집합,통합} 상가.csv   지수(기준분기=100)
  3행 헤더(지역1/지역2/지역3, 지표명, 단위) + wide(분기 컬럼). cp949.
  지역 계층: 서울 > 권역(도심·강남·영등포신촌·기타) > R-ONE 상권(광화문·강남대로·홍대/합정 등)

산출: data/임대료/R-ONE_임대동향_분기.csv (long)
  기존 `매장용빌딩...csv`(권역만)보다 세분 — R-ONE 상권 약 50~70개.
  단, R-ONE 상권 → 서울시 상권분석 1,650 상권 연결은 별도 crosswalk
  (`scripts/build_rone_trdar_crosswalk.py`) — 경계가 다른 proxy이며 grain 표시.

실행: .venv/bin/python3 scripts/ingest_rent_trend.py [--src DIR]
"""
from __future__ import annotations
import csv, glob, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _raw import raw  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.expanduser(sys.argv[sys.argv.index("--src") + 1]) if "--src" in sys.argv \
      else raw("임대동향")
OUT = os.path.join(ROOT, "data", "임대료", "R-ONE_임대동향_분기.csv")

TYPE_FROM_NAME = {"소규모": "소규모상가", "중대형": "중대형상가", "집합": "집합상가", "통합": "통합상가"}


def q_code(label: str) -> str | None:
    m = re.match(r"(\d{4})년\s*(\d)분기", label.strip())
    return f"{m.group(1)}{m.group(2)}" if m else None


def parse_file(path: str):
    with open(path, encoding="cp949", newline="") as f:
        rows = list(csv.reader(f))
    header, metric_row = rows[0], rows[1]
    q_cols = [(i, q_code(header[i])) for i in range(4, len(header)) if q_code(header[i])]
    metric = "임대가격지수" if "지수" in "".join(metric_row) else "임대료_천원㎡"
    fn = os.path.basename(path)
    styp = next((v for k, v in TYPE_FROM_NAME.items() if k in fn), "미상")
    out = []
    for r in rows[3:]:
        if len(r) < 5 or not r[1].strip():
            continue
        sido, gwon, jm = r[1].strip(), r[2].strip(), r[3].strip()
        if sido != "서울":
            continue
        if jm == "서울":
            level, gwon2, sang = "서울전체", "", ""
        elif jm == gwon:
            level, gwon2, sang = "권역", gwon, ""
        else:
            level, gwon2, sang = "상권", gwon, jm
        for ci, qc in q_cols:
            v = r[ci].strip()
            if not v or v in ("-", "N/A"):
                continue
            try:
                val = float(v)
            except ValueError:
                continue
            out.append([styp, metric, level, gwon2, sang, qc, val])
    return out, styp, metric, [qc for _, qc in q_cols]


def main() -> int:
    files = sorted(glob.glob(os.path.join(SRC, "임대동향*.csv")))
    if not files:
        print(f"FAIL: {SRC} 에 임대동향*.csv 없음")
        return 1
    all_rows = []
    for p in files:
        rows, styp, metric, qs = parse_file(p)
        all_rows += rows
        print(f"  {os.path.basename(p)[:48]:48s} → {styp}/{metric}  {qs[0]}~{qs[-1]}  {len(rows):,}행")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["상가유형", "지표", "grain", "권역", "R_ONE_상권", "기준_년분기_코드", "값"])
        w.writerows(sorted(all_rows))

    n_sang = len({r[4] for r in all_rows if r[2] == "상권"})
    n_gwon = len({r[3] for r in all_rows if r[2] == "권역"})
    qs = sorted({r[5] for r in all_rows})
    print(f"\n→ {os.path.relpath(OUT, ROOT)}  {len(all_rows):,}행")
    print(f"  상가유형 4, 지표 2(임대료·지수), grain 서울전체/권역({n_gwon})/상권({n_sang})")
    print(f"  분기 {qs[0]}~{qs[-1]} ({len(qs)})")
    print("  ⚠ R-ONE 상권 → 서울시 상권분석 연결은 별도 proxy crosswalk 사용 — FC-20·21에서 grain='R-ONE 상권' 유지")
    # 샘플
    print("\n  샘플 (강남대로 소규모상가 임대료):")
    for r in sorted(all_rows):
        if r[4] == "강남대로" and r[0] == "소규모상가" and r[1].startswith("임대료"):
            print(f"    {r[5]} {r[6]} 천원/㎡")
    return 0


if __name__ == "__main__":
    sys.exit(main())
