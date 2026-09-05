"""네이버 데이터랩 검색어 트렌드 → FC-41(자치구·행정동)·FC-42(10 업종) 이식.

FC-42: data/ontology/업종_검색키워드.json 의 업종별 대표 키워드(OR 결합) 관심도.
FC-41: 자치구명(+"맛집"/"카페"), 행정동명(숫자·중점 앞부분 통용지명 +"맛집").

방식:
  - 요청마다 대상 4그룹 + 고정 앵커("_anchor": 서울 맛집/서울 카페) 1그룹 = 5그룹.
  - DataLab 은 요청 내 max=100 정규화 → raw_ratio 는 요청 간 비교 불가.
    같은 요청의 anchor 로 나눈 rel_index (= 대상/서울전체) 만 요청 간 비교 가능.
  - 무료 구간 가드는 scripts/naver_datalab.py + _budget.py 가 담당(월 28000).

산출(집계본 커밋):
  data/네이버트렌드/업종_검색트렌드_월.csv
  data/네이버트렌드/자치구_검색트렌드_월.csv
  data/네이버트렌드/행정동_검색트렌드_월.csv
  data/네이버트렌드/_요약.csv   (key별 최근 관심도·6개월 추세)

실행: .venv/bin/python3 scripts/ingest_naver_trend.py [--start 2021-01-01] [--end 2026-08-01]
      [--only 업종|자치구|행정동] [--topn 5]
"""
from __future__ import annotations
import csv, datetime as dt, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from naver_datalab import search_trend  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "네이버트렌드")
ONTOLOGY = os.path.join(ROOT, "data", "ontology", "업종_검색키워드.json")
AREA_TRDAR = os.path.join(ROOT, "data", "영역", "상권", "서울시 상권분석서비스(영역-상권).csv")
AREA_DONG = os.path.join(ROOT, "data", "영역", "행정동", "서울시 상권분석서비스(영역-행정동).csv")

ANCHOR = {"groupName": "_anchor", "keywords": ["서울 맛집", "서울 카페"]}
UNIT = "month"


def arg(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


START = arg("--start", "2021-01-01")
END = arg("--end", (dt.date.today().replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m-01"))
TOP_N = int(arg("--topn", "5"))
ONLY = arg("--only")


def batches(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def read_csv_col(path, col):
    with open(path, encoding="cp949", newline="") as f:
        rd = csv.reader(f)
        H = {c: i for i, c in enumerate(next(rd))}
        for r in rd:
            yield {c: r[H[c]] for c in H}


def run_targets(targets, keyfn, label):
    """targets: [(key, groupName, [keywords]), ...]. keyfn(key)->출력 row prefix dict 아님, 그냥 key 그대로 저장."""
    rows = []
    total_req = (len(targets) + 3) // 4
    print(f"[{label}] 대상 {len(targets)}개 → 요청 약 {total_req}회")
    for bi, batch in enumerate(batches(targets, 4), 1):
        groups = [{"groupName": g, "keywords": kw[:TOP_N] if label == "업종" else kw}
                  for (_, g, kw) in batch] + [ANCHOR]
        try:
            res = search_trend(groups, START, END, UNIT)
        except SystemExit as e:
            print(f"  중단({bi}/{total_req}): {e}")
            return rows, False
        by_title = {g["title"]: {p["period"][:7].replace("-", ""): p["ratio"] for p in g["data"]}
                    for g in res["results"]}
        anchor = by_title.get("_anchor", {})
        for (key, gname, _) in batch:
            series = by_title.get(gname, {})
            for ym, raw in series.items():
                a = anchor.get(ym)
                rel = round(raw / a, 4) if a else ""
                rows.append([ym, key, gname, round(raw, 3), round(a, 3) if a else "", rel])
        if bi % 10 == 0:
            print(f"  {bi}/{total_req} ...")
    return rows, True


def strip_dong(name: str) -> str:
    s = re.sub(r"[0-9·].*", "", name).strip()
    return s or name


def summarize(rows):
    """rows: [ym, key, gname, raw, anchor, rel]. key별 최근3개월 rel 평균 + 6개월 기울기."""
    from collections import defaultdict
    ser = defaultdict(list)
    for ym, key, *_ , rel in ((r[0], r[1], r[5]) for r in rows):
        if rel != "":
            ser[key].append((ym, rel))
    out = []
    for key, pts in ser.items():
        pts.sort()
        vals = [v for _, v in pts]
        last3 = vals[-3:]
        last6 = vals[-6:]
        slope = ""
        if len(last6) == 6:
            xs = list(range(6))
            mx, my = 2.5, sum(last6) / 6
            num = sum((x - mx) * (y - my) for x, y in zip(xs, last6))
            den = sum((x - mx) ** 2 for x in xs)
            slope = round(num / den, 4) if den else ""
        out.append([key, len(pts), round(sum(last3) / len(last3), 4) if last3 else "",
                    round(sum(vals) / len(vals), 4), slope])
    return out


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"기간 {START} ~ {END} ({UNIT})")

    # ── FC-42 업종 ──
    if not ONLY or ONLY == "업종":
        onto = json.load(open(ONTOLOGY, encoding="utf-8"))["업종"]
        tg = [(code, meta["명"], meta["검색키워드"]) for code, meta in onto.items()]
        rows, ok = run_targets(tg, None, "업종")
        namemap = {code: meta["명"] for code, meta in onto.items()}
        p = os.path.join(OUT_DIR, "업종_검색트렌드_월.csv")
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["기준_년월", "업종코드", "업종명", "raw_ratio", "anchor_ratio", "rel_index"])
            for r in rows:
                w.writerow([r[0], r[1], namemap.get(r[1], ""), r[3], r[4], r[5]])
        _write_summary(rows, os.path.join(OUT_DIR, "_요약.csv"), "업종", namemap, first=True)
        print(f"→ {os.path.relpath(p, ROOT)}  {len(rows)}행")

    # ── FC-41 자치구 ──
    if not ONLY or ONLY == "자치구":
        gus = sorted({r["자치구_코드_명"].strip() for r in read_csv_col(AREA_TRDAR, "자치구_코드_명") if r["자치구_코드_명"].strip()})
        tg = [(gu, gu, [gu, f"{gu} 맛집", f"{gu} 카페"]) for gu in gus]
        rows, ok = run_targets(tg, None, "자치구")
        p = os.path.join(OUT_DIR, "자치구_검색트렌드_월.csv")
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["기준_년월", "자치구", "raw_ratio", "anchor_ratio", "rel_index"])
            for r in rows:
                w.writerow([r[0], r[1], r[3], r[4], r[5]])
        _write_summary(rows, os.path.join(OUT_DIR, "_요약.csv"), "자치구", {})
        print(f"→ {os.path.relpath(p, ROOT)}  {len(rows)}행")

    # ── FC-41 행정동 ──
    if not ONLY or ONLY == "행정동":
        dongs = [(r["행정동_코드"].strip(), r["행정동_명"].strip())
                 for r in read_csv_col(AREA_DONG, "행정동_코드")]
        # 통용지명 단위로 유니크 질의 후 코드로 fan-out
        kw_of = {}
        for code, nm in dongs:
            kw_of[code] = strip_dong(nm)
        uniq = sorted(set(kw_of.values()))
        tg = [(u, u, [u, f"{u} 맛집"]) for u in uniq]
        rows, ok = run_targets(tg, None, "행정동")
        rel_by = {}
        for ym, u, gname, raw, a, rel in rows:
            rel_by.setdefault(u, []).append((ym, raw, a, rel))
        p = os.path.join(OUT_DIR, "행정동_검색트렌드_월.csv")
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["기준_년월", "행정동_코드", "행정동명", "검색키워드",
                        "raw_ratio", "anchor_ratio", "rel_index"])
            for code, nm in dongs:
                u = kw_of[code]
                for ym, raw, a, rel in rel_by.get(u, []):
                    w.writerow([ym, code, nm, u, raw, a, rel])
        _write_summary(rows, os.path.join(OUT_DIR, "_요약.csv"), "행정동_통용지명", {})
        print(f"→ {os.path.relpath(p, ROOT)}  ({len(uniq)} 통용지명 → {len(dongs)} 행정동)")

    print(f"\n예산: {open(os.path.join(ROOT, '.api_budget.json')).read().strip() if os.path.exists(os.path.join(ROOT, '.api_budget.json')) else '-'}")
    return 0


_SUM_STARTED = {"v": False}


def _write_summary(rows, path, keytype, namemap, first=False):
    mode = "w" if (first and not _SUM_STARTED["v"]) else "a"
    if mode == "w":
        _SUM_STARTED["v"] = True
    with open(path, mode, encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if mode == "w":
            w.writerow(["구분", "key", "이름", "관측월수", "최근3개월_rel평균", "전체_rel평균", "최근6개월_기울기"])
        for key, n, last3, allmean, slope in summarize(rows):
            w.writerow([keytype, key, namemap.get(key, ""), n, last3, allmean, slope])


if __name__ == "__main__":
    sys.exit(main())
