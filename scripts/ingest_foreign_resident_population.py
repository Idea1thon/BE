"""서울 생활인구(외국인) 행정동·일·시간대 → 분기·월 집계 이식.

원천: 서울 열린데이터광장
  - 장기체류(거주): OA-14992, 파일 LONG_FOREIGNER_DONG_YYYYMM.csv
  - 단기체류(관광 방문): OA-14993, 파일 TEMP_FOREIGNER_DONG_YYYYMM.csv
  둘 다 6열: 기준일ID, 시간대구분(00-23), 행정동코드,
             총생활인구수(=중국인+중국외), 중국인체류인구수, 중국외외국인체류인구수
  좌표 없음(행정동 코드). 인코딩은 파일별로 utf-8-sig 또는 cp949.

산출(집계본만 커밋, 원천 월별 파일은 .gitignore):
  data/외국인생활인구/외국인생활인구_행정동_분기.csv
  data/외국인생활인구/외국인생활인구_행정동_월.csv

실행: .venv/bin/python3 scripts/ingest_foreign_resident_population.py [--src DIR]
"""
from __future__ import annotations
import csv, glob, os, sys, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _raw import raw  # noqa: E402
SRC = raw("외국인생활인구")
if "--src" in sys.argv:
    SRC = os.path.expanduser(sys.argv[sys.argv.index("--src") + 1])
OUT_DIR = os.path.join(ROOT, "data", "외국인생활인구")
AREA_DONG = os.path.join(ROOT, "data", "영역", "행정동",
                        "서울시 상권분석서비스(영역-행정동).csv")

DAY_HOURS = set(range(9, 19))   # 09~18시 = 주간

# 생활인구 ↔ 영역-행정동 코드 차이 (강북구 번동·수유동, 끝자리 개정). 위치 기반 추정.
DONG_ALIAS = {
    "11305590": "11305595", "11305600": "11305603", "11305606": "11305608",
    "11305610": "11305615", "11305620": "11305625", "11305630": "11305635",
}

SETS = {"장기": "LONG_FOREIGNER_DONG_*.csv", "단기": "TEMP_FOREIGNER_DONG_*.csv"}


def detect_enc(path: str) -> str:
    raw = open(path, "rb").read(4096)
    if raw[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    for enc in ("utf-8", "cp949"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "cp949"


def yq(yyyymm: str) -> str:
    return f"{yyyymm[:4]}{(int(yyyymm[4:6]) - 1) // 3 + 1}"


def read_area_dong() -> set[str]:
    with open(AREA_DONG, encoding="cp949", newline="") as f:
        rdr = csv.reader(f)
        i = next(rdr).index("행정동_코드")
        return {r[i].strip() for r in rdr if len(r) > i}


def missing_months(months: list[str]) -> list[str]:
    def nxt(m):
        y, mm = int(m[:4]), int(m[4:]) + 1
        return f"{y + 1}01" if mm > 12 else f"{y}{mm:02d}"
    out, cur = [], months[0]
    while cur < months[-1]:
        if cur not in months:
            out.append(cur)
        cur = nxt(cur)
    return out


def aggregate(pattern: str):
    """returns (q_agg, m_agg, meta). agg key -> [tot,cn,ncn,cnt,day_tot,day_cnt,night_tot,night_cnt,dates]"""
    files = sorted(glob.glob(os.path.join(SRC, pattern)))
    months = [os.path.basename(f).split("_")[-1].replace(".csv", "") for f in files]

    def new():
        return [0.0, 0.0, 0.0, 0, 0.0, 0, 0.0, 0, set()]
    q_agg = collections.defaultdict(new)
    m_agg = collections.defaultdict(new)
    enc_seen = collections.Counter()
    bad = 0
    for p in files:
        mm = os.path.basename(p).split("_")[-1].replace(".csv", "")
        q = yq(mm)
        enc = detect_enc(p)
        enc_seen[enc] += 1
        with open(p, encoding=enc, newline="") as f:
            rdr = csv.reader(f)
            next(rdr)
            for r in rdr:
                if len(r) < 6:
                    bad += 1
                    continue
                dong = DONG_ALIAS.get(r[2].strip(), r[2].strip())
                try:
                    tot, cn, ncn = float(r[3]), float(r[4]), float(r[5])
                    hh = int(r[1])
                except ValueError:
                    bad += 1
                    continue
                day = hh in DAY_HOURS
                for agg, key in ((q_agg, (q, dong)), (m_agg, (mm, dong))):
                    a = agg[key]
                    a[0] += tot; a[1] += cn; a[2] += ncn; a[3] += 1
                    if day:
                        a[4] += tot; a[5] += 1
                    else:
                        a[6] += tot; a[7] += 1
                    a[8].add(r[0].strip())
    return q_agg, m_agg, dict(files=len(files), months=months,
                             missing=missing_months(months) if months else [],
                             enc=dict(enc_seen), bad=bad)


def mean(a, idx_sum, idx_cnt):
    return round(a[idx_sum] / a[idx_cnt], 2) if a[idx_cnt] else ""


def main() -> int:
    aggs = {}
    for name, pat in SETS.items():
        q, m, meta = aggregate(pat)
        if not meta["months"]:
            print(f"⚠ {name}: {pat} 파일 없음 — 스킵")
            continue
        aggs[name] = (q, m, meta)
        print(f"{name}체류: {meta['files']}개 {meta['months'][0]}~{meta['months'][-1]}  "
              f"인코딩 {meta['enc']}  누락 {meta['missing'] or '없음'}  파싱실패 {meta['bad']}")
    if not aggs:
        print("FAIL: 원천 파일 없음")
        return 1

    area = read_area_dong()
    os.makedirs(OUT_DIR, exist_ok=True)

    for label, ai in (("분기", 1), ("월", 2)):
        keycol = "기준_년분기_코드" if label == "분기" else "기준_년월"
        aidx = 0 if label == "분기" else 1
        # 모든 (key, dong) union
        keys = set()
        for name in aggs:
            keys |= set(aggs[name][aidx].keys())
        path = os.path.join(OUT_DIR, f"외국인생활인구_행정동_{label}.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                keycol, "행정동_코드",
                "장기_외국인_평균", "단기_외국인_평균", "외국인_합_평균",
                "장기_야간_평균", "단기_주간_평균",
                "장기_중국인_평균", "장기_중국외_평균",
                "단기_중국인_평균", "단기_중국외_평균",
                "장기_관측일수", "단기_관측일수", "영역행정동_매칭",
            ])
            for (k, dong) in sorted(keys):
                la = aggs.get("장기", (None, None))[aidx].get((k, dong)) if "장기" in aggs else None
                sa = aggs.get("단기", (None, None))[aidx].get((k, dong)) if "단기" in aggs else None
                lm = round(la[0] / la[3], 2) if la and la[3] else ""
                sm = round(sa[0] / sa[3], 2) if sa and sa[3] else ""
                tot = round((la[0] / la[3] if la and la[3] else 0)
                            + (sa[0] / sa[3] if sa and sa[3] else 0), 2)
                w.writerow([
                    k, dong, lm, sm, tot,
                    mean(la, 6, 7) if la else "",
                    mean(sa, 4, 5) if sa else "",
                    mean(la, 1, 3) if la else "", mean(la, 2, 3) if la else "",
                    mean(sa, 1, 3) if sa else "", mean(sa, 2, 3) if sa else "",
                    len(la[8]) if la else 0, len(sa[8]) if sa else 0,
                    "Y" if dong in area else "N",
                ])
        dongs = {d for _, d in keys}
        qs = sorted({k for k, _ in keys})
        print(f"\n{label} → {os.path.relpath(path, ROOT)}  행 {len(keys):,}  "
              f"{keycol} {qs[0]}~{qs[-1]}({len(qs)})  행정동 {len(dongs)}  "
              f"영역매칭 {len(dongs & area)}/{len(area)}  영역에만 {sorted(area - dongs)}")

    # 계단식 여부 (분기, 외국인 합)
    if "장기" in aggs:
        q = aggs["장기"][0]
        byd = collections.defaultdict(list)
        for (k, dong), a in q.items():
            byd[dong].append(round(a[0] / (a[3] or 1), 1))
        dist = [len(set(v)) for v in byd.values() if len(v) >= 4]
        many = sum(1 for d in dist if d >= 6)
        print(f"\n계단식 검사(장기): {len(dist)}개 중 distinct 6+ = {many} "
              f"→ {'분기 시계열' if many > len(dist) * 0.8 else '계단식 의심'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
