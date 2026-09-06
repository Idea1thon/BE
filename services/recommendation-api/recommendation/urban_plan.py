"""도시계획·정비사업 추진단계 표준화 로더 — 이슈 #29.

`data/도시계획사업/` 두 파일을 FC-51(정비·재개발 사업)·FC-52(대규모 개발·역세권)
근거로 연결한다. 전부 `context_notes` 버킷 — `fit_tier`·정렬·`data_confidence`에 미반영
(candidate-selection-spec.md §202·§428, regional-characteristics-profile.md FC-51/52).

- `도시계획사업_상권겹침.csv`: 서울 도시계획포털 UQ120 폴리곤을 상권 폴리곤과 PIP한
  스냅샷. `상권_코드`로 이미 공간 결합됨(`겹침_상권비율` %). `추진단계_구분`(초기·조합·
  인가·착공·완료 5단계) 보유.
- `정비사업조합_목록.csv`: 정비사업 정보몽땅 사업장 목록. **좌표 없음 → 자치구 grain**
  (대표지번 지오코딩은 후속 과제). `진행단계`(원본 라벨 ~24종) 보유.

**추진단계 3그룹 표준화 (계획 / 추진 / 착공이후):**
- 계획      = 구상·지정·계획수립·안전진단·추진위 단계. 불확실성 큼.
- 추진      = 조합설립·사업시행인가·관리처분인가 등 행정 절차 진행. 물리적 변화 전.
- 착공이후  = 착공·철거·준공·이전고시·입주. 물리적 변화 발생/진행.
"예정"·"확정" 단정 금지 — 현재 추진단계 그룹만 서술한다(§428).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DIR = "data/도시계획사업"
_OVERLAP_FILE = "도시계획사업_상권겹침.csv"
_ASSOC_FILE = "정비사업조합_목록.csv"

# 제2차 서울 도시철도망 구축계획(2020-11-17). 자치구 grain — 미개통·정거장 위치 미확정.
_SUBWAY_DIR = "data/도시철도역사"
_SUBWAY_SGG_FILE = "도시철도망계획_자치구.csv"
_SUBWAY_LINE_FILE = "도시철도망계획_노선.csv"
_SUBWAY_PLAN_PERIOD = "2020-11-17"  # 관보 고시일

# 겹침_상권비율(%) 이 값 이상이면 "이 상권의 개발 이슈"로 전면 서술, 미만은 건수만.
MIN_OVERLAP_RATIO = 5.0

STAGE_GROUPS = ("계획", "추진", "착공이후")

# 도시계획사업_상권겹침.csv 의 `추진단계_구분`(5단계) → 3그룹
_STAGE_5_TO_3 = {
    "초기": "계획",
    "조합": "추진",
    "인가": "추진",
    "착공": "착공이후",
    "완료": "착공이후",
}

# 정비사업조합 `진행단계` 원본 라벨(~24종) → 3그룹. 키워드 우선순위(뒤에서 앞으로 강함).
_ASSOC_KEYWORDS = [
    ("계획", ("정비계획", "정비구역지정", "안전진단", "추진위", "창립총회", "조합규약", "모집", "지구단위계획", "심의", "사업계획승인")),
    ("추진", ("조합설립인가", "사업시행인가", "관리처분")),
    ("착공이후", ("착공", "철거", "준공", "이전고시", "분양", "입주", "사용검수")),
]
# 조합 해산·청산은 대개 준공 후 종료(일부 무산 포함) — 착공이후 + 별도 note.
_ASSOC_DISSOLVED = ("해산", "청산")


def _stage_group_from_assoc(raw: str) -> tuple[str, str | None]:
    """정비사업 진행단계 원본 → (3그룹, 특기사항 or None)."""
    s = (raw or "").strip()
    if not s:
        return "계획", "진행단계 미상(빈값) — 계획 단계로 보수적 분류"
    if any(k in s for k in _ASSOC_DISSOLVED):
        return "착공이후", "조합 해산·청산 — 대개 준공 후 종료(일부 무산 포함), 사업 완료 단정 불가"
    for group, keys in reversed(_ASSOC_KEYWORDS):  # 강한 신호(착공이후)부터
        if any(k in s for k in keys):
            return group, None
    return "계획", f"미분류 진행단계 '{s}' — 계획 단계로 보수적 분류"


def _stage_group_from_urban(gubun: str, raw_stage: str) -> tuple[str, str | None]:
    g = (gubun or "").strip()
    if g in _STAGE_5_TO_3:
        return _STAGE_5_TO_3[g], None
    # 추진단계_구분 이 비었거나 꼬리 라벨(미상 등) → 원본 추진단계로 폴백
    return _stage_group_from_assoc(raw_stage)


def _read_csv(path: Path) -> list[dict[str, str]]:
    for encoding in ("utf-8-sig", "cp949", "utf-8"):
        try:
            with path.open(encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"CSV 인코딩을 읽지 못함: {path}")


def _num(value: Any) -> float | None:
    if value is None or str(value).strip() in ("", "-", "N/A"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


@dataclass
class PlanProject:
    name: str
    category: str          # 사업유형(세부) 또는 사업구분
    category_major: str     # 사업유형_대분류 (urban 만)
    stage_raw: str          # 원본 추진단계/진행단계
    stage_group: str        # 계획 | 추진 | 착공이후
    stage_note: str | None
    overlap_ratio: float | None  # 겹침_상권비율 (%), urban 만
    sigungu: str
    source: str             # 'urban_project' | 'redevelopment_association'


@dataclass
class PlannedSubwayLine:
    name: str
    line_type: str        # 신설 | 연장 (운행개선은 제외)
    endpoints: str        # "기점~종점"
    length_km: float | None
    sigungus: list[str]   # 경유 자치구
    status: str           # 상태_2026
    period: str           # 계획기간


@dataclass
class PlanData:
    by_trdar: dict[str, list[PlanProject]] = field(default_factory=dict)      # 상권_코드 → [PlanProject]
    by_sigungu: dict[str, list[PlanProject]] = field(default_factory=dict)    # 자치구_코드 → [PlanProject] (정비사업조합)
    subway_by_sigungu: dict[str, list[str]] = field(default_factory=dict)     # 자치구명 → [계획 노선명(신설/연장만)]
    subway_lines: dict[str, PlannedSubwayLine] = field(default_factory=dict)  # 노선명 → 상세
    observed_at: str = ""
    source_paths: dict[str, str] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)


def _assemble_subway(line_rows: list[dict[str, str]], sgg_rows: list[dict[str, str]]
                     ) -> tuple[dict[str, list[str]], dict[str, PlannedSubwayLine]]:
    """이미 파싱된 행 dict → (자치구명 → 계획 노선 목록, 노선명 → 상세). 파일·DB 공통 코어.

    운행개선(급행·직결)은 신역세권이 아니므로 제외한다(프로파일 §432).
    """
    by_sgg: dict[str, list[str]] = {}
    lines: dict[str, PlannedSubwayLine] = {}
    for r in line_rows:
        name = str(r.get("노선명", "")).strip()
        ltype = str(r.get("노선유형", "")).strip()
        if not name or ltype not in ("신설", "연장"):
            continue
        lines[name] = PlannedSubwayLine(
            name=name, line_type=ltype,
            endpoints=f"{str(r.get('기점', '')).strip()}~{str(r.get('종점', '')).strip()}",
            length_km=_num(r.get("규모_km")),
            sigungus=[s.strip() for s in str(r.get("경유_자치구", "")).split(";") if s.strip()],
            status=str(r.get("상태_2026", "")).strip() or "계획(미개통·정거장 위치 미확정)",
            period=str(r.get("계획기간", "")).strip(),
        )
    for r in sgg_rows:
        sgg = str(r.get("자치구", "")).strip()
        if not sgg:
            continue
        planned: list[str] = []
        for entry in str(r.get("전체_계획노선_목록", "")).split(";"):
            entry = entry.strip()
            if not entry or "(운행개선)" in entry:
                continue
            planned.append(entry)
        if planned:
            by_sgg[sgg] = sorted(dict.fromkeys(planned))  # 중복 제거·결정론
    return by_sgg, lines


def _load_subway_plan(root: Path) -> tuple[dict[str, list[str]], dict[str, PlannedSubwayLine]]:
    """파일 소스: `도시철도망계획_{노선,자치구}.csv`."""
    try:
        line_rows = _read_csv(root / _SUBWAY_DIR / _SUBWAY_LINE_FILE)
    except (OSError, RuntimeError):
        return {}, {}
    try:
        sgg_rows = _read_csv(root / _SUBWAY_DIR / _SUBWAY_SGG_FILE)
    except (OSError, RuntimeError):
        sgg_rows = []
    return _assemble_subway(line_rows, sgg_rows)


def _load_subway_plan_from_db(query) -> tuple[dict[str, list[str]], dict[str, PlannedSubwayLine]]:
    """DB 소스: `context.subway_network_plan`(kind·key·attributes jsonb). 미적재 시 빈 dict."""
    import json
    try:
        rows = query("SELECT kind, attributes FROM context.subway_network_plan")
    except Exception:  # noqa: BLE001 — 테이블 미적재
        return {}, {}
    line_rows = [json.loads(r["attributes"]) for r in rows if r.get("kind") == "line"]
    sgg_rows = [json.loads(r["attributes"]) for r in rows if r.get("kind") == "sigungu"]
    return _assemble_subway(line_rows, sgg_rows)


def _assemble(urban_rows: list[dict[str, str]], assoc_rows: list[dict[str, str]],
              *, observed_at: str = "", source_paths: dict[str, str] | None = None,
              subway_by_sigungu: dict[str, list[str]] | None = None,
              subway_lines: dict[str, PlannedSubwayLine] | None = None) -> PlanData:
    by_trdar: dict[str, list[PlanProject]] = {}
    for r in urban_rows:
        code = str(r.get("상권_코드", "")).strip()
        if not code:
            continue
        group, note = _stage_group_from_urban(r.get("추진단계_구분", ""), r.get("추진단계", ""))
        by_trdar.setdefault(code, []).append(PlanProject(
            name=str(r.get("사업장명", "")).strip(),
            category=str(r.get("사업유형", "")).strip(),
            category_major=str(r.get("사업유형_대분류", "")).strip(),
            stage_raw=str(r.get("추진단계", "")).strip(),
            stage_group=group, stage_note=note,
            overlap_ratio=_num(r.get("겹침_상권비율")),
            sigungu=str(r.get("자치구", "")).strip(),
            source="urban_project",
        ))
    for lst in by_trdar.values():
        # 결정론: 겹침비율 내림차순, 동률은 사업장명 오름차순.
        lst.sort(key=lambda p: (-(p.overlap_ratio or 0.0), p.name))

    by_sigungu: dict[str, list[PlanProject]] = {}
    for r in assoc_rows:
        sgg = str(r.get("자치구_코드", "")).strip()
        if not sgg:
            continue
        group, note = _stage_group_from_assoc(r.get("진행단계", ""))
        by_sigungu.setdefault(sgg, []).append(PlanProject(
            name=str(r.get("사업장명", "")).strip(),
            category=str(r.get("사업구분", "")).strip(),
            category_major="정비사업조합",
            stage_raw=str(r.get("진행단계", "")).strip(),
            stage_group=group, stage_note=note,
            overlap_ratio=None,
            sigungu=str(r.get("자치구", "")).strip(),
            source="redevelopment_association",
        ))
    return PlanData(
        by_trdar=by_trdar, by_sigungu=by_sigungu,
        subway_by_sigungu=subway_by_sigungu or {}, subway_lines=subway_lines or {},
        observed_at=observed_at,
        source_paths={
            "urban_overlap": f"{_DIR}/{_OVERLAP_FILE}",
            "redev_association": f"{_DIR}/{_ASSOC_FILE}",
            "subway_plan_line": f"{_SUBWAY_DIR}/{_SUBWAY_LINE_FILE}",
            "subway_plan_sigungu": f"{_SUBWAY_DIR}/{_SUBWAY_SGG_FILE}",
            **(source_paths or {}),
        },
        coverage={
            "urban_trdar": len(by_trdar),
            "urban_projects": sum(len(v) for v in by_trdar.values()),
            "redev_sigungu": len(by_sigungu),
            "redev_projects": sum(len(v) for v in by_sigungu.values()),
            "subway_plan_sigungu": len(subway_by_sigungu or {}),
            "subway_plan_lines": len(subway_lines or {}),
        },
    )


def load_from_files(root: Path) -> PlanData:
    urban = _read_csv(root / _DIR / _OVERLAP_FILE)
    assoc = _read_csv(root / _DIR / _ASSOC_FILE)
    created = {str(r["생성일"]).strip() for r in urban if str(r.get("생성일", "")).strip()}
    observed = max(created) if created else ""  # F44: 행 순서 무관, 최신 생성일
    subway_sgg, subway_lines = _load_subway_plan(root)
    return _assemble(urban, assoc, observed_at=observed,
                     subway_by_sigungu=subway_sgg, subway_lines=subway_lines)


def load_from_db(query, root: Path) -> PlanData | None:
    """query: DbSource._query. `context.plan_snapshot` + `context.subway_network_plan`.

    계획 도시철도는 `context.subway_network_plan` 우선, 미적재 시 `root` 아래 파일 폴백.
    plan_snapshot·subway 둘 다 없으면 None.
    """
    rows = query(
        "SELECT plan_type, spatial_unit_type, spatial_unit_code, project_name, project_category, "
        "progress_stage, overlap_ratio, source_attributes "
        "FROM context.plan_snapshot WHERE plan_type IN ('urban_project_overlap', 'redevelopment_association')"
    )
    subway_sgg, subway_lines = _load_subway_plan_from_db(query)
    if not subway_sgg:  # 미적재 → 파일 폴백
        subway_sgg, subway_lines = _load_subway_plan(root)
    # F50: plan_snapshot이 비어도 계획 도시철도는 살린다. 둘 다 없을 때만 None.
    if not rows and not subway_sgg:
        return None
    import json

    urban_rows: list[dict[str, str]] = []
    assoc_rows: list[dict[str, str]] = []
    created: set[str] = set()
    for r in rows:
        attrs = json.loads(r["source_attributes"]) if r.get("source_attributes") else {}
        if r["plan_type"] == "urban_project_overlap":
            urban_rows.append(attrs)
            if str(attrs.get("생성일", "")).strip():
                created.add(str(attrs["생성일"]).strip())
        else:
            assoc_rows.append(attrs)
    # F44: 행 순서에 의존하지 않고 가장 최근 생성일을 스냅샷 기준으로.
    observed = max(created) if created else ""
    return _assemble(urban_rows, assoc_rows, observed_at=observed,
                     subway_by_sigungu=subway_sgg, subway_lines=subway_lines)


@dataclass
class PlanContext:
    context_notes: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    missing: list[dict[str, str]] = field(default_factory=list)
    dimension_features: list[str] = field(default_factory=list)
    grain_notes: dict[str, str] = field(default_factory=dict)
    freshness: dict[str, Any] = field(default_factory=dict)


def _stage_breakdown(projects: list[PlanProject]) -> dict[str, int]:
    out = {g: 0 for g in STAGE_GROUPS}
    for p in projects:
        out[p.stage_group] = out.get(p.stage_group, 0) + 1
    return out


def _ev(feature_id: str, metric_name: str, value: Any, unit: str, *, spatial_grain: str,
        grain_is_proxy: bool, observed_end_period: str, source_path: str,
        interpretation: str, limitation: str, proxy_note: str | None = None,
        normalization: list[str] | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "metric_name": metric_name, "value": value, "unit": unit,
        "source_type": "observed", "comparison_scope": "none",
        "period": observed_end_period or "스냅샷", "spatial_grain": spatial_grain,
        "grain_is_proxy": grain_is_proxy, "source_path": source_path,
        "quarter_file_source": source_path,
        "normalization": normalization or ["plan_stage_3group_crosswalk", "uq120_polygon_pip"],
        "interpretation": interpretation, "limitation": limitation,
        "feature_id": feature_id, "observed_end_period": observed_end_period or "스냅샷",
        "update_cycle": "snapshot",
    }
    if grain_is_proxy:
        item["proxy_note"] = proxy_note or "자치구 grain 대리(좌표 없음)"
    return item


def context_for_candidate(
    plan: PlanData | None,
    *,
    host_code: str | None,
    sigungu_code: str | None,
    sigungu_name: str | None,
    min_overlap_ratio: float = MIN_OVERLAP_RATIO,
) -> PlanContext:
    """후보 1건에 붙일 FC-51(정비·재개발)·FC-52(대규모 개발) 근거 — 전부 context_notes."""
    ctx = PlanContext()
    if plan is None:
        ctx.missing.append({"feature": "FC-51", "reason": "도시계획사업(#29) 미연결 — context.plan_snapshot 미적재. --source files 또는 ingest 필요"})
        ctx.missing.append({"feature": "FC-52", "reason": "도시계획사업(#29) 미연결"})
        return ctx
    obs = plan.observed_at or "스냅샷"

    # ---- FC-51: 상권 겹침 정비·재개발 사업 ----
    projects = list(plan.by_trdar.get(host_code or "", []))
    if projects:
        prominent = [p for p in projects if (p.overlap_ratio or 0) >= min_overlap_ratio]
        brk = _stage_breakdown(projects)
        # F42: 겹침 ≥ 임계값 사업만 전면 서술. 미만은 건수만(스펙 §0-10).
        top = prominent[:4]
        top_txt = "; ".join(
            f"{p.name or p.category}({p.category}·{p.stage_group}"
            + (f", 겹침{round(p.overlap_ratio, 1)}%" if p.overlap_ratio is not None else "")
            + ")"
            for p in top
        )
        # F41: 해산·청산 등 stage_note를 전체 projects에서 수집해 노출(top 밖이어도).
        dissolved = sum(1 for p in projects if p.stage_note and "해산" in (p.stage_note + p.stage_raw))
        note_extra = ""
        notes = [p.stage_note for p in projects if p.stage_note]
        if notes:
            note_extra = " · " + " / ".join(dict.fromkeys(notes))
        list_txt = f"{top_txt}. " if top_txt else f"(겹침≥{min_overlap_ratio:g}% 사업 없음 — 건수만). "
        ctx.context_notes.append(
            f"이 상권과 겹치는 공식 도시계획·정비사업 {len(projects)}건 "
            f"(겹침≥{min_overlap_ratio:g}% {len(prominent)}건 · 단계: 계획 {brk['계획']}·추진 {brk['추진']}·착공이후 {brk['착공이후']}) — "
            f"{list_txt}{note_extra}"
            f"UQ120 폴리곤 PIP 스냅샷({obs}), 발표일 시계열 아님. '예정'·'확정' 단정 금지 — 현재 추진단계 그룹만. 판정·정렬 미반영 (FC-51)"
        )
        ctx.dimension_features.append("FC-51")
        ctx.grain_notes["FC-51"] = "상권 grain(UQ120 폴리곤 PIP 공간결합) · 추진단계 5단계→3그룹(계획/추진/착공이후) 크로스워크 · context_notes 버킷(신호 없음)"
        ctx.evidence.append(_ev(
            "FC-51", "상권겹침_도시계획정비사업", len(projects), "건",
            spatial_grain="상권", grain_is_proxy=False, observed_end_period=obs,
            source_path=plan.source_paths["urban_overlap"],
            interpretation=f"상권 겹침 도시계획·정비사업 {len(projects)}건 (겹침≥{min_overlap_ratio:g}% {len(prominent)}건) — 단계 계획 {brk['계획']}·추진 {brk['추진']}·착공이후 {brk['착공이후']}"
            + (f", 그 중 해산·청산 {dissolved}건" if dissolved else ""),
            limitation="추진단계 스냅샷이며 사건일 시계열 아님. 겹침비율은 상권 폴리곤 대비 사업 폴리곤 면적 비율. 조합 해산·청산은 준공/무산 구분 불가. '예정'·'확정' 단정 금지. fit_tier 판정·정렬 미반영",
        ))
        ctx.freshness["urban_plan"] = {
            "observed_end_period": obs, "periods_behind_latest": 0,
            "update_cadence": "snapshot", "is_partial_latest": False,
        }
    elif plan.by_trdar:
        ctx.missing.append({"feature": "FC-51", "reason": "이 상권과 겹치는 UQ120 도시계획사업 폴리곤 없음(폴리곤 밖 사업은 미매칭)"})

    # ---- FC-52: 대규모 개발·역세권 (도시계획사업 대분류) ----
    major_hit = sorted({p.category_major for p in projects if p.category_major in
                        ("재정비촉진사업", "역세권사업", "국토부사업")})
    if major_hit:
        maj_projects = [p for p in projects if p.category_major in major_hit]
        ctx.context_notes.append(
            f"대규모 개발 유형 사업 포함: {', '.join(major_hit)} ({len(maj_projects)}건) — 상권 겹침 도시계획사업 중 대분류 기준. "
            f"정거장·구역 경계·개통 미확정, '예정역'·'확정' 표현 금지. 판정·정렬 미반영 (FC-52)"
        )
        ctx.dimension_features.append("FC-52")
        ctx.grain_notes["FC-52"] = "상권 grain · 도시계획사업 대분류(재정비촉진·역세권·국토부) · context_notes 버킷"
        # F45: FC-52도 구조화 evidence를 남긴다.
        ctx.evidence.append(_ev(
            "FC-52", "상권겹침_대규모개발유형", len(maj_projects), "건",
            spatial_grain="상권", grain_is_proxy=False, observed_end_period=obs,
            source_path=plan.source_paths["urban_overlap"],
            interpretation=f"상권 겹침 도시계획사업 중 대규모 개발 대분류({', '.join(major_hit)}) {len(maj_projects)}건",
            limitation="사업 대분류 스냅샷이며 정거장·구역 경계·개통일 미확정. '예정역'·'확정' 표현 금지. fit_tier 판정·정렬 미반영",
        ))

    # ---- FC-52: 계획 도시철도 (제2차 서울 도시철도망 구축계획, 자치구 grain) ----
    planned_lines = plan.subway_by_sigungu.get(sigungu_name or "", [])
    if planned_lines:
        def _fmt(entry: str) -> str:
            lo = plan.subway_lines.get(_line_key(entry))
            if not lo:
                return entry  # "노선명(구분)" 원문
            length = f", {lo.length_km:g}km" if lo.length_km else ""
            return f"{lo.name}({lo.line_type}, {lo.endpoints}{length})"
        detail = "; ".join(_fmt(ln) for ln in planned_lines)
        ctx.context_notes.append(
            f"{sigungu_name} 계획 도시철도 {len(planned_lines)}개 노선: {detail} — "
            f"**제2차 서울 도시철도망 구축계획({_SUBWAY_PLAN_PERIOD}) 자치구 grain**. 2020년 계획이며 "
            f"미개통·정거장 위치·개통일 미확정. 운행개선(급행·직결)은 신역세권이 아니라 제외됨. "
            f"이 후보 지점이 노선·정거장에서 가깝다는 뜻이 아니다. '예정역'·'확정' 표현 금지, 판정·정렬 미반영 (FC-52 계획도시철도)"
        )
        if "FC-52" not in ctx.dimension_features:
            ctx.dimension_features.append("FC-52")
        ctx.grain_notes.setdefault("FC-52-subway", "자치구 grain · 제2차 서울 도시철도망 구축계획(2020) 신설·연장 노선(운행개선 제외) · 정거장 미확정 · context_notes 버킷")
        ctx.evidence.append(_ev(
            "FC-52", "자치구_계획도시철도_노선수", len(planned_lines), "개",
            spatial_grain="자치구", grain_is_proxy=True, observed_end_period=_SUBWAY_PLAN_PERIOD,
            source_path=plan.source_paths["subway_plan_sigungu"],
            interpretation=f"{sigungu_name} 경유 계획 도시철도 신설·연장 {len(planned_lines)}개 노선: {', '.join(planned_lines)}",
            limitation="2020년 관보 계획 · 자치구 grain · 미개통 · 정거장 위치·개통일 미확정 · 운행개선 제외. 후보 지점의 역세권 편입을 뜻하지 않음. '예정역'·'확정' 금지. fit_tier 판정·정렬 미반영",
            proxy_note="자치구 grain(정거장 위치 미확정)",
            normalization=["subway_network_plan_sigungu_filter"],  # F49: 폴리곤 PIP 아님 — 자치구 CSV 필터·집계
        ))
    elif plan.subway_by_sigungu:
        ctx.missing.append({"feature": "FC-52", "reason": f"{sigungu_name or sigungu_code}는 제2차 서울 도시철도망 구축계획(2020)에 신설·연장 계획 노선 없음"})
    else:
        ctx.missing.append({"feature": "FC-52", "reason": "계획 도시철도(도시철도망계획_*.csv) 데이터 미로드"})

    # ---- FC-51 보조: 자치구 정비사업조합 (좌표 없음 → 자치구 대리) ----
    assoc = list(plan.by_sigungu.get(sigungu_code or "", []))
    if assoc:
        brk = _stage_breakdown(assoc)
        kinds = ", ".join(f"{k} {v}" for k, v in _count_by(assoc, lambda p: p.category).items() if v)
        # F41: 해산·청산 조합 수를 착공이후 안에서 분리 표기.
        assoc_dissolved = sum(1 for p in assoc if p.stage_note and "해산" in (p.stage_note + p.stage_raw))
        dissolved_txt = (f" — 착공이후 {brk['착공이후']}건 중 {assoc_dissolved}건은 조합 해산·청산(준공/무산 구분 불가, 사업 완료 단정 불가)"
                         if assoc_dissolved else "")
        ctx.context_notes.append(
            f"{sigungu_name or sigungu_code} 정비사업조합 {len(assoc)}건 (단계: 계획 {brk['계획']}·추진 {brk['추진']}·착공이후 {brk['착공이후']}; {kinds}){dissolved_txt} — "
            f"**자치구 grain 대리**(정비사업 정보몽땅 목록, 좌표 없어 상권 매칭 불가). 이 후보 상권의 사업이라는 보장 없음. 판정·정렬 미반영 (FC-51 보조)"
        )
        if "FC-51" not in ctx.dimension_features:
            ctx.dimension_features.append("FC-51")
        ctx.grain_notes.setdefault("FC-51-assoc", "자치구 grain 대리 · 정비사업조합 목록(좌표 없음) · 진행단계 원본→3그룹 크로스워크 · context_notes 버킷")
        ctx.evidence.append(_ev(
            "FC-51", "자치구_정비사업조합", len(assoc), "건",
            spatial_grain="자치구", grain_is_proxy=True, observed_end_period=obs,
            source_path=plan.source_paths["redev_association"],
            interpretation=f"{sigungu_name or sigungu_code} 정비사업조합 {len(assoc)}건 — 단계 계획 {brk['계획']}·추진 {brk['추진']}·착공이후 {brk['착공이후']}"
            + (f" (착공이후 중 해산·청산 {assoc_dissolved}건)" if assoc_dissolved else ""),
            limitation="좌표 없어 자치구 단위로만 집계 — 이 후보 상권의 사업이라는 보장 없음. 조합 해산·청산은 준공/무산 구분 불가. 대표지번 지오코딩은 후속 과제. fit_tier 판정·정렬 미반영",
            proxy_note="자치구 grain 대리(좌표 없음)",
        ))

    if not projects and not assoc:
        ctx.missing.append({"feature": "FC-51", "reason": f"이 상권·자치구({sigungu_name or sigungu_code})와 매칭되는 도시계획·정비사업 없음"})
    return ctx


def _line_key(entry: str) -> str:
    """'난곡선(신설)' → '난곡선' (노선 상세 dict 조회 키)."""
    return entry.split("(", 1)[0].strip()


def _count_by(items, key):
    out: dict[str, int] = {}
    for it in items:
        k = key(it) or "미상"
        out[k] = out.get(k, 0) + 1
    # 결정론: 건수 내림차순, 동수는 이름 오름차순 (파일·DB 행 순서 무관).
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))
