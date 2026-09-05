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

실행:
  .venv/bin/python3 scripts/ingest_building_register.py --sigungu 송파구
  .venv/bin/python3 scripts/ingest_building_register.py --all [--skip-floors] [--limit-gu N]
      서울 25개 자치구를 법정동 수 오름차순으로 순회. getBrFlrOulnInfo(층별개요)는
      법정동당 최대 22만+건이라 일일 무료 한도(기능당 10,000회)로 서울 전체가
      하루에 안 끝난다 — 같은 명령을 재실행하면 완료된 (자치구,법정동)은 캐시로
      건너뛰고 이어간다. --skip-floors 로 표제부(주소·주차·승강기)만 먼저 전체 수집 가능.
  .venv/bin/python3 scripts/ingest_building_register.py --targeted [--limit N]
      전량 대신 **필지 단위 선별 호출**. 서울 전체 표제부 수집(--all --skip-floors) 이후,
      Tier1(GIS) 상업건물 중 건축물대장 표제부가 상업임을 확인해주지 못한 지번만
      getBrFlrOulnInfo를 sigunguCd+bjdongCd+bun+ji로 필지 단위로 좁혀 호출한다
      (법정동 전체 호출보다 수십 배 적은 콜 수). 층별용도가 이미 전량 수집된 법정동
      (예: 송파구)은 자동으로 제외한다. 산출: 층별용도_선별.csv (기존 층별용도_*.csv와
      같은 스키마라 DB 로더가 그대로 인식).
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


def all_sigungu_by_size():
    """서울 25개 자치구를 Tier1 법정동 수 오름차순으로 — 작은 구부터 끝내서 하루 예산 안에
    더 많은 구를 완결시킨다."""
    counts: dict[str, set] = defaultdict(set)
    with TIER1_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["시군구명"] and row["법정동코드"]:
                counts[row["시군구명"]].add(row["법정동코드"])
    return sorted(counts, key=lambda g: len(counts[g]))


def write(name, rows, cols):
    p = OUT_DIR / name
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  → {p.relative_to(ROOT)}  {len(rows)}행  {p.stat().st_size/1024:.0f}KB", flush=True)
    return len(rows)


def run_gu(gu: str, ops: list[str], with_units: bool, limit: int | None = None) -> int:
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
    budget_exhausted = False

    for code10, sg, bj, nm in dongs:
        got = {}
        failed = False
        for op in ops:
            try:
                got[op] = fetch_all(SERVICE, op, sigungu_cd=sg, bjdong_cd=bj)
            except SystemExit as exc:
                print(f"  {code10} {nm:8} ⏸ 일일 예산 도달, 이번 실행 중단 — {exc}", flush=True)
                budget_exhausted = True
                failed = True
                break
            except RuntimeError as exc:
                print(f"  {code10} {nm:8} ⚠ {op} 실패, 이 법정동 건너뜀 — {exc}", flush=True)
                failed_dongs.append(f"{code10}:{op}")
                failed = True
                break
        if failed:
            if budget_exhausted:
                break
            continue
        for op in OPS_CORE + OPS_UNITS:
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
            # titles에 없는 pk(표제부에서 부속건축물로 제외됐거나 페이지 누락)는 링크에 안 넣는다 —
            # 건물링크가 building_register를 FK 참조하므로 존재하지 않는 pk를 참조하면 안 됨.
            if pk and pk in titles and pnu_of(r):
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
    flr_path = OUT_DIR / f"층별용도_{gu}.csv"
    if "getBrFlrOulnInfo" in ops:
        n_flr = write(f"층별용도_{gu}.csv", floors, [
            "mgmBldrgstPk", "PNU", "지번주소", "도로명주소", "층구분", "층번호", "층번호명", "층면적_㎡",
            "용도코드", "용도", "상세용도", "용도군", "구조"])
    else:
        # --skip-floors 등으로 이번 실행에 층별개요를 안 가져왔으면 이전에 수집해둔
        # 파일을 빈 파일로 덮어쓰지 않는다.
        if flr_path.is_file():
            with flr_path.open(encoding="utf-8-sig") as f:
                n_flr = sum(1 for _ in f) - 1
            print(f"  (층별용도_{gu}.csv 기존 파일 유지, {n_flr}행 — 이번 실행은 층별개요 미수집)", flush=True)
        else:
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
    multi_disambiguated = multi_unresolved = 0
    for pnu, brow in t1.items():
        pks, kind = pnu_to_pk.get(pnu), "지번"
        if not pks and pnu[11:15] != "0000" and len(bun_pk.get(pnu[:15], ())) == 1:
            pks, kind = bun_pk[pnu[:15]], "본번"
        if not pks:
            continue
        n_candidates = len(pks)
        unresolved = False
        if n_candidates > 1:
            # 한 지번(필지)에 건축물대장이 여러 개(아파트+관리동/상가동 등) — Tier1이
            # 상업으로 분류한 이 PNU엔 상업 주용도 표제부를 우선 채택한다. 상업 후보가
            # 하나도 없으면(진짜 애매) 원래 후보 전체를 남겨 미해결로 표시한다.
            comm = {pk for pk in pks if titles.get(pk, {}).get("용도군")}
            if comm:
                if len(comm) < n_candidates:
                    multi_disambiguated += 1
                pks = comm
            else:
                multi_unresolved += 1
                unresolved = True
        if kind == "지번":
            m_jibun += 1
        else:
            m_bonbun += 1
        for pk in sorted(pks):
            t = titles.get(pk, {})
            link.append({"PNU": pnu, "지번주소": brow["대지위치"], "용도군_tier1": brow["용도군"],
                         "mgmBldrgstPk": pk, "도로명주소": t.get("도로명주소", ""),
                         "대장_주용도": t.get("주용도", ""), "매칭": kind,
                         "원후보수": n_candidates, "다중후보_미해결": unresolved})
    n_link = write(f"건물링크_{gu}.csv", link, [
        "PNU", "지번주소", "도로명주소", "용도군_tier1", "대장_주용도", "mgmBldrgstPk", "매칭",
        "원후보수", "다중후보_미해결"])
    matched = {r["PNU"] for r in link}

    manifest = {
        "source": {"name": "국토교통부 건축HUB 건축물대장정보 서비스 (1613000/BldRgstHubService)",
                   "endpoints": ops, "note": "현행 등록 상태 — 건축인허가보다 커버리지 높음"},
        "generated_at": datetime.date.today().isoformat(),
        "scope": {"sigungu": gu, "bjdong_count": len(dongs), "failed_dongs": failed_dongs,
                  "budget_exhausted": budget_exhausted, "complete": not failed_dongs and not budget_exhausted},
        "counts": {"title": n_title, "commercial_titles": len(commercial_pks),
                   "commercial_floors": n_flr, "buildings_with_commercial_floor": len(with_commercial_floor),
                   "expos_units": n_exp if with_units else None},
        "tier1_link": {"tier1_commercial_pnu": len(t1), "matched_pnu": len(matched),
                       "by_jibun": m_jibun, "by_bonbun": m_bonbun,
                       "multi_candidate_disambiguated": multi_disambiguated,
                       "multi_candidate_unresolved": multi_unresolved,
                       "match_rate": round(len(matched) / len(t1), 3) if t1 else None},
        "commercial_filter": COMMERCIAL_PREFIX,
        "budget": budget_status(SERVICE, ops),
        "limitations": [
            "표제부 주용도 기준 상업 필터 + 층별개요로 주상복합 저층상가 일부 포착",
            "대형 필지(아파트단지 등)는 bun-본번 집계라 PNU 정확매칭 안 될 수 있음",
            f"한 지번에 건축물대장 여러 개인 경우({multi_disambiguated + multi_unresolved}건) 상업 주용도를 "
            f"우선 채택(정제 {multi_disambiguated}건). 상업 후보가 전혀 없는 {multi_unresolved}건은 "
            "다중후보_미해결=True로 표시 — 그 지번의 상업 속성은 대장으로 확인 불가",
            "임대료·보증금·권리금·공실·매물 여부는 없음 — 이 데이터로 생성 불가",
        ],
    }
    (OUT_DIR / f"manifest_{gu}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== {gu} 건축물대장 API ({'core+units' if with_units else 'core'}) ===", flush=True)
    print(f"표제부 {n_title} (상업주용도 {len(commercial_pks)}) · 상업층 {n_flr} "
          f"(주상복합 포함 건물 {len(with_commercial_floor)}) · 전유부 {n_exp} · 링크 {n_link}행")
    print(f"Tier1 상업건물 {len(t1)} 중 대장 매칭 {len(matched)} "
          f"(지번 {m_jibun} + 본번 {m_bonbun}, {manifest['tier1_link']['match_rate']})")
    if multi_disambiguated or multi_unresolved:
        print(f"  다중후보 지번: 정제 {multi_disambiguated} / 미해결 {multi_unresolved}")
    print(f"예산: {budget_status(SERVICE, ops)}")
    if failed_dongs:
        print(f"⚠ 미완료(오류): {failed_dongs} — 재실행하면 완료된 법정동은 캐시로 건너뛰고 이어감")
    if budget_exhausted:
        print(f"⏸ {gu}: 일일 예산 도달로 {len(dongs)}개 법정동 중 일부만 처리 — 내일(또는 한도 리셋 후) 재실행하면 이어감")
        return 2
    return 1 if failed_dongs else 0


def _use_group_by_pk() -> dict[str, str]:
    """표제부_*.csv 전체에서 mgmBldrgstPk -> 용도군('' 이면 비상업)."""
    m: dict[str, str] = {}
    for f in sorted(OUT_DIR.glob("표제부_*.csv")):
        with f.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                m[row["mgmBldrgstPk"]] = row["용도군"]
    return m


def _fully_covered_dong_codes() -> set[str]:
    """이미 층별개요를 법정동 전체 수집한 자치구의 법정동코드 — 선별 대상에서 제외."""
    covered = set()
    for f in sorted(OUT_DIR.glob("층별용도_*.csv")):
        gu = f.stem.replace("층별용도_", "")
        if gu == "선별":
            continue
        with f.open(encoding="utf-8-sig") as fh:
            if sum(1 for _ in fh) > 1:  # 헤더 외 데이터 있음 = 그 구는 전량 수집됨
                covered.update(c for c, _, _, _ in dong_codes_for(gu))
    return covered


def targeted_candidates() -> list[tuple[str, str, str, str, str]]:
    """(PNU, sigunguCd, bjdongCd, bun, ji) — Tier1 상업건물 중 건축물대장으로 상업 확인 안 된 지번.
    이미 층별개요를 전량 수집한 자치구(예: 송파구)는 제외한다."""
    use_group = _use_group_by_pk()
    confirmed_pnu: set[str] = set()
    for f in sorted(OUT_DIR.glob("건물링크_*.csv")):
        with f.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                if use_group.get(row["mgmBldrgstPk"]):
                    confirmed_pnu.add(row["PNU"])

    covered_dongs = _fully_covered_dong_codes()
    seen: dict[str, tuple[str, str, str, str, str]] = {}
    with TIER1_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            pnu = row["PNU"]
            if not pnu or pnu in confirmed_pnu or pnu in seen:
                continue
            if row["법정동코드"] in covered_dongs:
                continue
            if len(pnu) != 19:
                continue
            sg, bj, bun, ji = pnu[0:5], pnu[5:10], pnu[11:15], pnu[15:19]
            seen[pnu] = (pnu, sg, bj, bun, ji)
    return list(seen.values())


def run_targeted(limit: int | None = None) -> int:
    targets = targeted_candidates()
    if limit:
        targets = targets[:limit]
    print(f"선별 대상 지번: {len(targets)}개  ({budget_status(SERVICE, ['getBrFlrOulnInfo'])})", flush=True)

    floors: list[dict] = []
    failed_pnus: list[str] = []
    budget_exhausted = False
    for idx, (pnu, sg, bj, bun, ji) in enumerate(targets, 1):
        try:
            rows = fetch_all(SERVICE, "getBrFlrOulnInfo", sigungu_cd=sg, bjdong_cd=bj,
                             extra={"bun": bun, "ji": ji})
        except SystemExit as exc:
            print(f"  ⏸ 일일 예산 도달 ({idx-1}/{len(targets)} 처리) — {exc}", flush=True)
            budget_exhausted = True
            break
        except RuntimeError as exc:
            failed_pnus.append(pnu)
            continue
        for r in rows:
            grp = cgroup(r.get("mainPurpsCd"))
            if not grp:
                continue
            floors.append({
                "mgmBldrgstPk": s(r.get("mgmBldrgstPk")), "PNU": pnu_of(r) or pnu,
                "지번주소": s(r.get("platPlc")), "도로명주소": s(r.get("newPlatPlc")),
                "층구분": s(r.get("flrGbCdNm")), "층번호": i(r.get("flrNo")),
                "층번호명": s(r.get("flrNoNm")), "층면적_㎡": n(r.get("area")),
                "용도코드": s(r.get("mainPurpsCd")), "용도": s(r.get("mainPurpsCdNm")),
                "상세용도": s(r.get("etcPurps")), "용도군": grp, "구조": s(r.get("strctCdNm")),
            })
        if idx % 500 == 0:
            print(f"  {idx}/{len(targets)} 처리, 상업층 발견 {len(floors)}건, 실패 {len(failed_pnus)}건", flush=True)

    n_flr = write("층별용도_선별.csv", floors, [
        "mgmBldrgstPk", "PNU", "지번주소", "도로명주소", "층구분", "층번호", "층번호명", "층면적_㎡",
        "용도코드", "용도", "상세용도", "용도군", "구조"])

    manifest = {
        "method": "targeted (필지 단위, sigunguCd+bjdongCd+bun+ji)",
        "generated_at": datetime.date.today().isoformat(),
        "candidates_total": len(targets), "candidates_processed": idx if targets else 0,
        "candidates_failed": len(failed_pnus), "budget_exhausted": budget_exhausted,
        "commercial_floor_rows_found": n_flr,
        "buildings_confirmed_commercial": len({r["mgmBldrgstPk"] for r in floors}),
        "budget": budget_status(SERVICE, ["getBrFlrOulnInfo"]),
    }
    (OUT_DIR / "manifest_선별.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== 선별 방식 층별개요 ===")
    print(f"대상 {len(targets)} · 처리 {manifest['candidates_processed']} · 실패 {len(failed_pnus)} "
          f"· 상업층 발견 {n_flr}행 ({manifest['buildings_confirmed_commercial']}개 건물 상업 확인)")
    print(f"예산: {budget_status(SERVICE, ['getBrFlrOulnInfo'])}")
    if budget_exhausted:
        print("⏸ 일일 예산 도달 — 같은 명령 재실행하면 캐시로 이어감(단, 결과 파일은 이번 실행분만 반영되므로 "
              "재실행 후 다시 합쳐야 함 — manifest_선별.json.candidates_processed 확인)")
        return 2
    if failed_pnus:
        print(f"⚠ 실패 {len(failed_pnus)}건(재시도 소진) — 재실행 시 캐시 없는 것만 재시도됨")
        return 1
    return 0


def main() -> int:
    if "--targeted" in sys.argv:
        limit = i(arg("--limit")) if "--limit" in sys.argv else None
        return run_targeted(limit)

    with_units = "--with-units" in sys.argv
    skip_floors = "--skip-floors" in sys.argv
    limit = i(arg("--limit-dong")) if "--limit-dong" in sys.argv else None
    ops = (["getBrTitleInfo"] if skip_floors else OPS_CORE) + (OPS_UNITS if with_units else [])

    if "--all" in sys.argv:
        gus = all_sigungu_by_size()
        limit_gu = i(arg("--limit-gu")) if "--limit-gu" in sys.argv else None
        if limit_gu:
            gus = gus[:limit_gu]
        print(f"서울 전체 {len(gus)}개 자치구 순회 (작은 구부터). ops={ops}", flush=True)
        done, exhausted = [], False
        for gu in gus:
            rc = run_gu(gu, ops, with_units, limit)
            if rc == 2:
                exhausted = True
                break
            done.append(gu)
        remaining = [g for g in gus if g not in done]
        print(f"\n=== 서울 전체 진행상황 ===")
        print(f"완료: {len(done)}/{len(gus)}개 자치구")
        if remaining:
            print(f"남음: {remaining}")
        if exhausted:
            print("일일 예산 도달로 중단 — 같은 명령(--all) 재실행하면 완료 자치구는 캐시로 건너뛰고 이어감")
        return 0 if not remaining else 2

    gu = arg("--sigungu", "송파구")
    rc = run_gu(gu, ops, with_units, limit)
    return 0 if rc in (0, 2) else rc


if __name__ == "__main__":
    sys.exit(main())
