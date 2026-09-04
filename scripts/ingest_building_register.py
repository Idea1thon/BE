"""건축HUB 건축물대장 API → 상업용 건물의 표제부·층별 용도·전유부 호실 (Tier 2, 파일럿).

`ingest_building_ledger.py`(GIS건물통합정보 파생, `context.commercial_building`)가 좌표를 가진
건물 모집단이라면, 이건 **도로명주소·층별 용도·개별 호실·주차·승강기**를 건축물대장 현행
등록 상태에서 REAL 로 채운다. 건축인허가(`ingest_building_permit.py`)보다 커버리지가 높다
(허가 시점이 아니라 현재 등록된 모든 건물).

파일럿: 1개 자치구(기본 송파구). 법정동 코드는 `data/건축물대장/상가건물_서울.csv` 에서.

산출 → `data/건축물대장/api/`:
  표제부_{구}.csv     — mgmBldrgstPk 단위: 지번·도로명주소·주용도·규모·층수·주차·승강기·사용승인일
  층별용도_{구}.csv   — (mgmBldrgstPk, 층): 층 용도·면적  (상업 용도만)
  전유부_{구}.csv     — (mgmBldrgstPk, 동, 호): 호명칭·층  (--with-units, 상업 용도 건물만)
  건물링크_{구}.csv   — Tier1 PNU ↔ mgmBldrgstPk
  manifest_{구}.json

실행: .venv/bin/python3 scripts/ingest_building_register.py [--sigungu 송파구] [--with-units]
"""
from __future__ import annotations
import csv
import datetime
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from datago_hub import fetch_all, budget_status

SERVICE = "BldRgstHubService"
ROOT = Path(__file__).resolve().parents[1]
TIER1_CSV = ROOT / "data" / "건축물대장" / "상가건물_서울.csv"
OUT_DIR = ROOT / "data" / "건축물대장" / "api"

COMMERCIAL_PREFIX = {"03": "근린생활1", "04": "근린생활2", "07": "판매시설", "14": "업무시설"}
OPS_CORE = ["getBrTitleInfo", "getBrFlrOulnInfo"]
OPS_UNITS = ["getBrExposInfo", "getBrExposPubuseAreaInfo"]
_PLATGB = {"0": "1", "1": "2", "2": "3"}


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def s(v):
    return str(v).strip() if v is not None else ""


def n(v):
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def i(v):
    f = n(v)
    return int(f) if f is not None else None


def ymd(v):
    t = s(v)
    return f"{t[:4]}-{t[4:6]}-{t[6:8]}" if len(t) == 8 and t.isdigit() else ""


def pnu_of(r):
    b, j = s(r.get("bun")).zfill(4), s(r.get("ji")).zfill(4)
    if b == "0000":
        return ""
    return s(r.get("sigunguCd")) + s(r.get("bjdongCd")) + _PLATGB.get(s(r.get("platGbCd")), "1") + b + j


def cgroup(cd):
    return COMMERCIAL_PREFIX.get(s(cd)[:2])


def dong_codes_for(gu):
    seen = {}
    with TIER1_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["시군구명"] == gu and row["법정동코드"] and row["법정동코드"] not in seen:
                seen[row["법정동코드"]] = (row["대지위치"].split() or [""])[-1]
    return sorted((c, c[:5], c[5:10], nm) for c, nm in seen.items())


def write(name, rows, cols):
    p = OUT_DIR / name
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  → {p.relative_to(ROOT)}  {len(rows)}행  {p.stat().st_size/1024:.0f}KB", flush=True)
    return len(rows)


def main():
    gu = arg("--sigungu", "송파구")
    limit = i(arg("--limit-dong")) if "--limit-dong" in sys.argv else None
    with_units = "--with-units" in sys.argv
    ops = OPS_CORE + (OPS_UNITS if with_units else [])
    dongs = dong_codes_for(gu)
    if limit:
        dongs = dongs[:limit]
    if not dongs:
        print(f"FAIL: {TIER1_CSV} 에서 {gu} 법정동 없음")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{gu}: 법정동 {len(dongs)}개  units={with_units}  ({budget_status(SERVICE, ops)})", flush=True)

    titles: dict[str, dict] = {}
    floors: list[dict] = []
    expos: list[dict] = []
    pnu_to_pk: dict[str, set] = defaultdict(set)
    commercial_pks: set[str] = set()
    failed_dongs: list[str] = []

    for code10, sg, bj, nm in dongs:
        got = {}
        failed = False
        for op in ops:
            try:
                got[op] = fetch_all(SERVICE, op, sigungu_cd=sg, bjdong_cd=bj)
            except RuntimeError as exc:
                print(f"  {code10} {nm:8} ⚠ {op} 실패, 이 법정동 건너뜀 — {exc}", flush=True)
                failed_dongs.append(f"{code10}:{op}")
                failed = True
                break
        if failed:
            continue
        for op in OPS_UNITS:
            got.setdefault(op, [])
        print(f"  {code10} {nm:8} " + " ".join(
            f"{op.replace('getBr','').replace('Info','')}={len(v)}" for op, v in got.items()), flush=True)

        for r in got["getBrTitleInfo"]:
            pk = s(r.get("mgmBldrgstPk"))
            if not pk or s(r.get("mainAtchGbCd")) == "1":   # 부속건축물 제외
                continue
            grp = cgroup(r.get("mainPurpsCd"))
            titles[pk] = {
                "mgmBldrgstPk": pk, "PNU": pnu_of(r), "지번주소": s(r.get("platPlc")),
                "도로명주소": s(r.get("newPlatPlc")), "건물명": s(r.get("bldNm")), "동명칭": s(r.get("dongNm")),
                "대장종류": s(r.get("regstrKindCdNm")), "주용도코드": s(r.get("mainPurpsCd")),
                "주용도": s(r.get("mainPurpsCdNm")), "상세용도": s(r.get("etcPurps")),
                "용도군": grp or "", "구조": s(r.get("strctCdNm")), "지붕": s(r.get("roofCdNm")),
                "대지면적_㎡": n(r.get("platArea")), "건축면적_㎡": n(r.get("archArea")),
                "연면적_㎡": n(r.get("totArea")), "건폐율_pct": n(r.get("bcRat")), "용적률_pct": n(r.get("vlRat")),
                "높이_m": n(r.get("heit")), "지상층수": i(r.get("grndFlrCnt")), "지하층수": i(r.get("ugrndFlrCnt")),
                "승용승강기": i(r.get("rideUseElvtCnt")), "비상용승강기": i(r.get("emgenUseElvtCnt")),
                "호수": i(r.get("hoCnt")), "세대수": i(r.get("hhldCnt")), "가구수": i(r.get("fmlyCnt")),
                "옥내기계식_대수": i(r.get("indrMechUtcnt")), "옥외기계식_대수": i(r.get("oudrMechUtcnt")),
                "옥내자주식_대수": i(r.get("indrAutoUtcnt")), "옥외자주식_대수": i(r.get("oudrAutoUtcnt")),
                "허가일": ymd(r.get("pmsDay")), "착공일": ymd(r.get("stcnsDay")), "사용승인일": ymd(r.get("useAprDay")),
                "생성일": ymd(r.get("crtnDay")),
            }
            if pnu_of(r):
                pnu_to_pk[pnu_of(r)].add(pk)
            if grp:
                commercial_pks.add(pk)

        for r in got["getBrFlrOulnInfo"]:
            grp = cgroup(r.get("mainPurpsCd"))
            if not grp:
                continue
            pk = s(r.get("mgmBldrgstPk"))
            floors.append({
                "mgmBldrgstPk": pk, "PNU": pnu_of(r), "지번주소": s(r.get("platPlc")),
                "도로명주소": s(r.get("newPlatPlc")), "층구분": s(r.get("flrGbCdNm")),
                "층번호": i(r.get("flrNo")), "층번호명": s(r.get("flrNoNm")), "층면적_㎡": n(r.get("area")),
                "용도코드": s(r.get("mainPurpsCd")), "용도": s(r.get("mainPurpsCdNm")),
                "상세용도": s(r.get("etcPurps")), "용도군": grp, "구조": s(r.get("strctCdNm")),
            })
            if pk and pnu_of(r):
                pnu_to_pk[pnu_of(r)].add(pk)

        for r in got["getBrExposInfo"]:
            pk = s(r.get("mgmBldrgstPk"))
            if pk not in commercial_pks:   # 상업 주용도 건물의 전유부만
                continue
            expos.append({
                "mgmBldrgstPk": pk, "지번주소": s(r.get("platPlc")), "도로명주소": s(r.get("newPlatPlc")),
                "건물명": s(r.get("bldNm")), "동명칭": s(r.get("dongNm")), "호명칭": s(r.get("hoNm")),
                "층구분": s(r.get("flrGbCdNm")), "층번호": i(r.get("flrNo")),
                "대장종류": s(r.get("regstrKindCdNm")),
            })

    # 층별 용도가 있는데 표제부 주용도가 비상업인 건물도 포함 (주상복합 저층상가)
    with_commercial_floor = {r["mgmBldrgstPk"] for r in floors if r["mgmBldrgstPk"]}

    n_title = write(f"표제부_{gu}.csv", list(titles.values()), [
        "mgmBldrgstPk", "PNU", "지번주소", "도로명주소", "건물명", "동명칭", "대장종류",
        "주용도코드", "주용도", "상세용도", "용도군", "구조", "지붕",
        "대지면적_㎡", "건축면적_㎡", "연면적_㎡", "건폐율_pct", "용적률_pct", "높이_m", "지상층수", "지하층수",
        "승용승강기", "비상용승강기", "호수", "세대수", "가구수",
        "옥내기계식_대수", "옥외기계식_대수", "옥내자주식_대수", "옥외자주식_대수",
        "허가일", "착공일", "사용승인일", "생성일"])
    n_flr = write(f"층별용도_{gu}.csv", floors, [
        "mgmBldrgstPk", "PNU", "지번주소", "도로명주소", "층구분", "층번호", "층번호명", "층면적_㎡",
        "용도코드", "용도", "상세용도", "용도군", "구조"])
    n_exp = write(f"전유부_{gu}.csv", expos, [
        "mgmBldrgstPk", "지번주소", "도로명주소", "건물명", "동명칭", "호명칭",
        "층구분", "층번호", "대장종류"]) if with_units else 0

    # ── Tier1 ↔ 대장 링크 ───────────────────────────────────────────
    fetched = {c for c, _, _, _ in dongs}
    t1: dict[str, dict] = {}
    with TIER1_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["법정동코드"] in fetched and row["PNU"]:
                t1.setdefault(row["PNU"], row)
    bun_pk: dict[str, set] = defaultdict(set)
    for p, pks in pnu_to_pk.items():
        bun_pk[p[:15]].update(pks)

    link, m_jibun, m_bonbun = [], 0, 0
    for pnu, brow in t1.items():
        pks, kind = pnu_to_pk.get(pnu), "지번"
        if not pks and pnu[11:15] != "0000" and len(bun_pk.get(pnu[:15], ())) == 1:
            pks, kind = bun_pk[pnu[:15]], "본번"
        if not pks:
            continue
        if kind == "지번":
            m_jibun += 1
        else:
            m_bonbun += 1
        for pk in sorted(pks):
            t = titles.get(pk, {})
            link.append({"PNU": pnu, "지번주소": brow["대지위치"], "용도군_tier1": brow["용도군"],
                         "mgmBldrgstPk": pk, "도로명주소": t.get("도로명주소", ""),
                         "대장_주용도": t.get("주용도", ""), "매칭": kind})
    n_link = write(f"건물링크_{gu}.csv", link, [
        "PNU", "지번주소", "도로명주소", "용도군_tier1", "대장_주용도", "mgmBldrgstPk", "매칭"])
    matched = {r["PNU"] for r in link}

    manifest = {
        "source": {"name": "국토교통부 건축HUB 건축물대장정보 서비스 (1613000/BldRgstHubService)",
                   "endpoints": ops, "note": "현행 등록 상태 — 건축인허가보다 커버리지 높음"},
        "generated_at": datetime.date.today().isoformat(),
        "scope": {"sigungu": gu, "bjdong_count": len(dongs), "failed_dongs": failed_dongs,
                  "complete": not failed_dongs},
        "counts": {"title": n_title, "commercial_titles": len(commercial_pks),
                   "commercial_floors": n_flr, "buildings_with_commercial_floor": len(with_commercial_floor),
                   "expos_units": n_exp if with_units else None},
        "tier1_link": {"tier1_commercial_pnu": len(t1), "matched_pnu": len(matched),
                       "by_jibun": m_jibun, "by_bonbun": m_bonbun,
                       "match_rate": round(len(matched) / len(t1), 3) if t1 else None},
        "commercial_filter": COMMERCIAL_PREFIX,
        "budget": budget_status(SERVICE, ops),
        "limitations": [
            "표제부 주용도 기준 상업 필터 + 층별개요로 주상복합 저층상가 일부 포착",
            "대형 필지(아파트단지 등)는 bun-본번 집계라 PNU 정확매칭 안 될 수 있음",
            "임대료·보증금·권리금·공실·매물 여부는 없음 — 이 데이터로 생성 불가",
        ],
    }
    (OUT_DIR / f"manifest_{gu}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== {gu} 건축물대장 API ({'core+units' if with_units else 'core'}) ===", flush=True)
    print(f"표제부 {n_title} (상업주용도 {len(commercial_pks)}) · 상업층 {n_flr} "
          f"(주상복합 포함 건물 {len(with_commercial_floor)}) · 전유부 {n_exp} · 링크 {n_link}행")
    print(f"Tier1 상업건물 {len(t1)} 중 대장 매칭 {len(matched)} "
          f"(지번 {m_jibun} + 본번 {m_bonbun}, {manifest['tier1_link']['match_rate']})")
    print(f"예산: {budget_status(SERVICE, ops)}")
    if failed_dongs:
        print(f"⚠ 미완료: {failed_dongs} — 같은 명령 재실행하면 완료된 법정동은 캐시로 건너뛰고 이어감")
    return 1 if failed_dongs else 0


if __name__ == "__main__":
    sys.exit(main())
