"""건축HUB 건축인허가 → 상업용 건물의 층별 용도·호실 전유면적·주차 (Tier 2, 파일럿).

건축물대장 Tier 1(`ingest_building_ledger.py`, `context.commercial_building`)이 건물 단위라면,
이건 **층별 용도·개별 호실·주차대수**를 REAL 로 채운다. 원천은 세움터 건축인허가(허가 시점)라
전산화 이전 오래된 건물은 레코드가 없을 수 있다 — 커버리지를 manifest 에 기록한다.

파일럿: 1개 자치구(기본 송파구). 법정동 코드는 Tier 1 산출물
`data/건축물대장/상가건물_서울.csv`(상업용 건물이 있는 법정동)에서 가져온다.

산출 → `data/건축인허가/`:
  건물기본_{구}.csv   — mgmPmsrgstPk 단위: 대지위치·PNU·주용도·연면적·건폐율·용적률·주차·허가/사용승인일
  층별용도_{구}.csv   — (mgmPmsrgstPk, 층) 단위: 층 구분·번호·용도·면적  (상업 용도만)
  호실_{구}.csv       — mgmHoDetlPk 단위: 건물·호명칭·층·평형·전유면적합·공용면적합·주용도  (상업 용도만)
  manifest.json

실행: .venv/bin/python3 scripts/ingest_building_permit.py [--sigungu 송파구] [--limit-dong N]
"""
from __future__ import annotations
import csv
import json
import sys
import datetime
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from archpms_hub import fetch_all, budget_status

ROOT = Path(__file__).resolve().parents[1]
TIER1_CSV = ROOT / "data" / "건축물대장" / "상가건물_서울.csv"
OUT_DIR = ROOT / "data" / "건축인허가"

# 상업 관련 주용도 대분류 prefix (mainPurpsCd 앞 2자리)
COMMERCIAL_PREFIX = {"03": "근린생활1", "04": "근린생활2", "07": "판매시설", "14": "업무시설"}

OPS_CORE = ["getApBasisOulnInfo", "getApPlatPlcInfo", "getApFlrOulnInfo", "getApPklotInfo"]
OPS_UNITS = ["getApHoOulnInfo", "getApHoExposPubuseAreaInfo"]


def arg(name: str, default: str | None = None) -> str | None:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def s(v) -> str:
    return str(v).strip() if v is not None else ""


def n(v):
    try:
        f = float(v)
        return f if f != 0 else 0.0
    except (TypeError, ValueError):
        return None


def i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def ymd(v):
    t = s(v)
    return f"{t[:4]}-{t[4:6]}-{t[6:8]}" if len(t) == 8 and t.isdigit() else ""


_PLATGB_TO_PNU = {"0": "1", "1": "2", "2": "3"}  # API platGbCd(0대지/1산/2블록) → PNU 필지구분(1일반/2산)


def pnu_parts(sg: str, bj: str, platgb: str, bun: str, ji: str) -> str:
    b, j = s(bun).zfill(4), s(ji).zfill(4)
    if b == "0000":
        return ""
    return sg + bj + _PLATGB_TO_PNU.get(s(platgb), "1") + b + j


def pnu_of(rec: dict) -> str:
    return pnu_parts(s(rec.get("sigunguCd")), s(rec.get("bjdongCd")),
                     s(rec.get("platGbCd")), rec.get("bun"), rec.get("ji"))


def reljibun_pnu(sg: str, bj: str, platgb: str, rel: str) -> str:
    """relJibunNm '341-27' 또는 '341' → PNU."""
    bun, _, ji = s(rel).partition("-")
    bun, ji = bun.strip(), ji.strip()
    if not bun.isdigit():
        return ""
    return pnu_parts(sg, bj, platgb, bun, ji if ji.isdigit() else "0")


def commercial_group(purps_cd) -> str | None:
    return COMMERCIAL_PREFIX.get(s(purps_cd)[:2])


def dong_codes_for(sigungu_name: str) -> list[tuple[str, str, str]]:
    """(법정동코드10, sigunguCd5, bjdongCd5) — Tier1 산출물에서 해당 자치구."""
    seen: dict[str, str] = {}
    with TIER1_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["시군구명"] != sigungu_name:
                continue
            code = row["법정동코드"]
            if code and code not in seen:
                addr = row["대지위치"].split()
                seen[code] = addr[-1] if addr else ""
    return sorted((c, c[:5], c[5:10], nm) for c, nm in seen.items())


def main() -> int:
    sigungu_name = arg("--sigungu", "송파구")
    limit_dong = i(arg("--limit-dong")) if "--limit-dong" in sys.argv else None
    with_units = "--with-units" in sys.argv
    ops = OPS_CORE + (OPS_UNITS if with_units else [])
    dongs = dong_codes_for(sigungu_name)
    if limit_dong:
        dongs = dongs[:limit_dong]
    if not dongs:
        print(f"FAIL: {TIER1_CSV} 에서 {sigungu_name} 법정동을 찾지 못함")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gu = sigungu_name

    print(f"{sigungu_name}: 법정동 {len(dongs)}개  units={with_units}  ({budget_status(ops)})", flush=True)

    basis: dict[str, dict] = {}          # mgmPmsrgstPk -> building basic
    pklot: dict[str, dict] = {}          # mgmPmsrgstPk -> parking
    floors: list[dict] = []              # commercial floor rows
    ho_meta: dict[str, dict] = {}        # mgmHoDetlPk -> {building, hoNm, flr, pngtyp}
    ho_area: dict[str, dict] = defaultdict(lambda: {"expos": 0.0, "pub": 0.0, "purps": "", "purps_cd": ""})
    pnu_to_permits: dict[str, set] = defaultdict(set)   # PNU -> {mgmPmsrgstPk}

    for code10, sg, bj, nm in dongs:
        got = {op: fetch_all(op, sigungu_cd=sg, bjdong_cd=bj) for op in ops}
        for op in OPS_UNITS:
            got.setdefault(op, [])
        print(f"  {code10} {nm:8} " + " ".join(f"{op.replace('getAp','').replace('Info','')}={len(v)}"
                                               for op, v in got.items()), flush=True)

        # 대지위치(대표·관련지번) → PNU ↔ 허가 링크
        for r in got.get("getApPlatPlcInfo", []):
            pk = s(r.get("mgmPmsrgstPk")) or s(r.get("mgmPlatPlcPk"))
            if not pk:
                continue
            for p in (pnu_of(r), reljibun_pnu(sg, bj, s(r.get("platGbCd")), r.get("relJibunNm"))):
                if p:
                    pnu_to_permits[p].add(pk)

        for r in got["getApBasisOulnInfo"]:
            pk = s(r.get("mgmPmsrgstPk"))
            if not pk:
                continue
            basis[pk] = {
                "mgmPmsrgstPk": pk, "PNU": pnu_of(r), "대지위치": s(r.get("platPlc")),
                "건물명": s(r.get("bldNm")), "주용도코드": s(r.get("mainPurpsCd")),
                "주용도": s(r.get("mainPurpsCdNm")), "지역": s(r.get("jiyukCdNm")),
                "대지면적_㎡": n(r.get("platArea")), "건축면적_㎡": n(r.get("archArea")),
                "연면적_㎡": n(r.get("totArea")), "건폐율_pct": n(r.get("bcRat")),
                "용적률_pct": n(r.get("vlRat")), "호수": i(r.get("hoCnt")),
                "가구수": i(r.get("fmlyCnt")), "세대수": i(r.get("hhldCnt")),
                "총주차대수": i(r.get("totPkngCnt")), "허가구분": s(r.get("archGbCdNm")),
                "건축허가일": ymd(r.get("archPmsDay")), "사용승인일": ymd(r.get("useAprDay")),
                "생성일": ymd(r.get("crtnDay")),
            }
            if pnu_of(r):
                pnu_to_permits[pnu_of(r)].add(pk)

        for r in got["getApPklotInfo"]:
            pk = s(r.get("mgmPmsrgstPk"))
            if pk:
                pklot[pk] = {
                    "옥내자주식_대수": i(r.get("indrAutoUtcnt")), "옥외자주식_대수": i(r.get("oudrAutoUtcnt")),
                    "옥내기계식_대수": i(r.get("indrMechUtcnt")), "옥외기계식_대수": i(r.get("oudrMechUtcnt")),
                    "인근_대수": (i(r.get("neigAutoUtcnt")) or 0) + (i(r.get("neigMechUtcnt")) or 0),
                    "면제_대수": i(r.get("exmptUtcnt")),
                }

        for r in got["getApFlrOulnInfo"]:
            grp = commercial_group(r.get("mainPurpsCd"))
            if not grp:
                continue
            floors.append({
                "mgmPmsrgstPk": s(r.get("mgmPmsrgstPk")), "PNU": pnu_of(r),
                "대지위치": s(r.get("platPlc")), "층구분": s(r.get("flrGbCdNm")),
                "층번호": i(r.get("flrNo")), "층면적_㎡": n(r.get("flrArea")),
                "용도코드": s(r.get("mainPurpsCd")), "용도": s(r.get("mainPurpsCdNm")),
                "용도군": grp, "구조": s(r.get("strctCdNm")),
            })
            fpk, fpnu = s(r.get("mgmPmsrgstPk")), pnu_of(r)
            if fpk and fpnu:
                pnu_to_permits[fpnu].add(fpk)

        for r in got["getApHoOulnInfo"]:
            hpk = s(r.get("mgmHoDetlPk"))
            if hpk:
                ho_meta[hpk] = {
                    "mgmHoDetlPk": hpk, "mgmPmsrgstPk": s(r.get("mgmPmsrgstPk")),
                    "대지위치": s(r.get("platPlc")), "호명칭": s(r.get("hoNm")),
                    "호번호": s(r.get("hoNo")), "층구분": s(r.get("flrGbCdNm")),
                    "층번호": i(r.get("flrNo")), "평형구분": s(r.get("pngtypGbNm")),
                }

        for r in got["getApHoExposPubuseAreaInfo"]:
            hpk = s(r.get("mgmHoDetlPk"))
            area = n(r.get("area")) or 0.0
            if not hpk:
                continue
            slot = ho_area[hpk]
            if s(r.get("exposPubuseGbCd")) == "1":
                slot["expos"] += area
                if not slot["purps"]:
                    slot["purps"] = s(r.get("mainPurpsCdNm"))
                    slot["purps_cd"] = s(r.get("mainPurpsCd"))
            else:
                slot["pub"] += area

    # ── 호실: 상업 용도 전유면적이 있는 것만 ─────────────────────────
    units = []
    for hpk, meta in ho_meta.items():
        a = ho_area.get(hpk)
        grp = commercial_group(a["purps_cd"]) if a and a.get("purps_cd") else None
        if not grp:
            continue
        units.append({**meta, "전유면적_㎡": round(a["expos"], 2), "공용면적_㎡": round(a["pub"], 2),
                      "용도코드": a.get("purps_cd", ""), "용도": a["purps"], "용도군": grp})

    # ── 쓰기 ─────────────────────────────────────────────────────────
    def write(name: str, rows: list[dict], cols: list[str]):
        p = OUT_DIR / name
        with p.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"  → {p.relative_to(ROOT)}  {len(rows)}행  {p.stat().st_size/1024:.0f}KB")
        return len(rows)

    basis_rows = []
    for pk, b in basis.items():
        basis_rows.append({**b, **{k: v for k, v in pklot.get(pk, {}).items()}})
    n_basis = write(f"건물기본_{gu}.csv", basis_rows, [
        "mgmPmsrgstPk", "PNU", "대지위치", "건물명", "주용도코드", "주용도", "지역",
        "대지면적_㎡", "건축면적_㎡", "연면적_㎡", "건폐율_pct", "용적률_pct",
        "호수", "가구수", "세대수", "총주차대수",
        "옥내자주식_대수", "옥외자주식_대수", "옥내기계식_대수", "옥외기계식_대수", "인근_대수", "면제_대수",
        "허가구분", "건축허가일", "사용승인일", "생성일"])
    n_flr = write(f"층별용도_{gu}.csv", floors, [
        "mgmPmsrgstPk", "PNU", "대지위치", "층구분", "층번호", "층면적_㎡",
        "용도코드", "용도", "용도군", "구조"])
    n_ho = 0
    if with_units:
        n_ho = write(f"호실_{gu}.csv", units, [
            "mgmHoDetlPk", "mgmPmsrgstPk", "대지위치", "호명칭", "호번호", "층구분", "층번호",
            "평형구분", "전유면적_㎡", "공용면적_㎡", "용도코드", "용도", "용도군"])

    # ── Tier1(context.commercial_building) ↔ 허가 링크 ───────────────
    fetched_codes = {c for c, _, _, _ in dongs}
    tier1_buildings: dict[str, dict] = {}
    with TIER1_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["법정동코드"] in fetched_codes and row["PNU"]:
                tier1_buildings.setdefault(row["PNU"], row)
    tier1_pnu = set(tier1_buildings)
    # 본번(bun)만 일치하는 필지가 유일하면 secondary 매칭 허용
    permit_bun: dict[str, set] = defaultdict(set)
    for p, pks in pnu_to_permits.items():
        permit_bun[p[:15]].update(pks)      # sigungu5+bjdong5+filji1+bun4

    link_rows = []
    matched_exact = matched_bun = 0
    for pnu, brow in tier1_buildings.items():
        pks = pnu_to_permits.get(pnu)
        kind = "지번"
        if not pks and pnu[11:15] != "0000" and len(permit_bun.get(pnu[:15], ())) == 1:
            pks = permit_bun[pnu[:15]]
            kind = "본번"
        if not pks:
            continue
        if kind == "지번":
            matched_exact += 1
        else:
            matched_bun += 1
        for pk in sorted(pks):
            link_rows.append({"PNU": pnu, "대지위치": brow["대지위치"], "용도군": brow["용도군"],
                              "mgmPmsrgstPk": pk, "매칭": kind})
    n_link = write(f"건물링크_{gu}.csv", link_rows, ["PNU", "대지위치", "용도군", "mgmPmsrgstPk", "매칭"])
    matched = {r["PNU"] for r in link_rows}
    permit_pnu = set(pnu_to_permits)

    manifest = {
        "source": {"name": "국토교통부 건축HUB 건축인허가정보 서비스 (1613000/ArchPmsHubService)",
                   "endpoints": ops, "note": "허가 시점 데이터 — 전산화 이전 오래된 건물 누락 가능"},
        "generated_at": datetime.date.today().isoformat(),
        "scope": {"sigungu": sigungu_name, "bjdong_count": len(dongs)},
        "counts": {"building_basic": n_basis, "commercial_floors": n_flr,
                   "commercial_units": (n_ho if with_units else None), "parking_records": len(pklot)},
        "tier1_coverage": {
            "tier1_commercial_pnu": len(tier1_pnu), "permit_pnu": len(permit_pnu),
            "matched_pnu": len(matched), "matched_by_jibun": matched_exact, "matched_by_bonbun": matched_bun,
            "link_rows": n_link,
            "match_rate_vs_tier1": round(len(matched) / len(tier1_pnu), 3) if tier1_pnu else None,
        },
        "commercial_filter": COMMERCIAL_PREFIX,
        "budget": budget_status(ops),
        "limitations": [
            "건축인허가(허가 시점) — 건축물대장 현행 상태 아님. 오래된 근생 건물 레코드 없음(tier1_coverage 확인)",
            "호실 전유면적은 허가 시점 값. 현재 분할·합병 반영 안 될 수 있음",
            "임대료·보증금·권리금·공실·매물 여부는 여전히 없음(이 데이터로 생성 불가)",
        ],
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== {sigungu_name} ({'core+units' if with_units else 'core'}) ===", flush=True)
    print(f"건물기본 {n_basis} · 상업층 {n_flr} · 상업호실 {n_ho} · 주차 {len(pklot)} · 링크 {n_link}행")
    print(f"Tier1 상업건물 {len(tier1_pnu)} 중 인허가 매칭 {len(matched)} "
          f"(지번 {matched_exact} + 본번 {matched_bun}, {manifest['tier1_coverage']['match_rate_vs_tier1']})")
    print(f"예산: {budget_status(ops)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
