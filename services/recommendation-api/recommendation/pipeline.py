"""증거 중심 서울 입지 추천 MVP 파이프라인.

이 모듈은 ``sample_jamsil_coffee.py``의 잠실·커피 하드코딩을 일반화한
재사용 가능한 실행 입구다. 성공확률이나 운영용 Top-K 모델을 만들지 않고,
지역·자연어 입력 → LLM 입력 해석·분석 계획·검색 도구 선택 → 계약 검증
→ 허용된 읽기 전용 DB 검색 도구 실행 → 데이터 분석 → 지점 seed
→ host 상권/행정동 배경값 → 반경 지표 → 후보·Evidence 검증
→ LLM 설명(실패 시 템플릿) 순서로 실행한다.

실행 예:
  .venv/bin/python3 -m recommendation.pipeline \
    --sido 서울특별시 --sigungu 송파구 --dong 잠실동 \
    --industry-code CS100010 \
    --special-condition-text '월세 300만원 이하, 20평 이상, 주차 가능' \
    --include-poi

출력:
  output/recommendation_runs/<run-slug>/{request,candidates,explanations,
  coverage-summary,run-manifest}.json + run-notes.md
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import re
import statistics
import tempfile
import unicodedata
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable

import shapefile
from . import population
from . import urban_plan
from .llm_explanation import explain_candidates
from .llm_input_planner import parse_conditions, plan_input
from .llm_runtime import LLMRuntimeError, reset_call_budget
from .paths import SERVICE_ROOT, find_project_root
from .rag_tools import execute_retrieval_requests
from .query_context import build_query_context
from .question_contract import build_question_contract, retrieval_requests_for_contract
from shapely import wkb as shapely_wkb
from shapely.geometry import Point, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree


ROOT = find_project_root(__file__)
DEFAULT_QUARTER = "20261"
DEFAULT_RENT_QUARTER = "20262"
DEFAULT_DEDUP_M = 80
DEFAULT_RADIUS_M = 500
DEFAULT_BUS_RADIUS_M = 250
POI_CONTEXT_RADIUS_M = (250, 500)
DEFAULT_HOST_MAX_M = 300
SUPPORTED_INDUSTRIES = {f"CS100{i:03d}" for i in range(1, 11)}
SUPPORTED_INDUSTRY_NAMES = {
    "CS100001": "한식", "CS100002": "중식", "CS100003": "일식", "CS100004": "양식",
    "CS100005": "제과점", "CS100006": "패스트푸드", "CS100007": "치킨", "CS100008": "분식",
    "CS100009": "호프-간이주점", "CS100010": "커피-음료",
}
# data/네이버트렌드/업종_검색트렌드_월.csv 의 업종명 (SUPPORTED_INDUSTRY_NAMES 와 다름).
# DB 소스는 metric_snapshot 에 업종명이 없어 이 표로 매핑한다.
NAVER_INDUSTRY_NAMES = {
    "CS100001": "한식음식점", "CS100002": "중식음식점", "CS100003": "일식음식점", "CS100004": "양식음식점",
    "CS100005": "제과점", "CS100006": "패스트푸드점", "CS100007": "치킨전문점", "CS100008": "분식전문점",
    "CS100009": "호프-간이주점", "CS100010": "커피-음료",
}
EH_LABEL_RISK = {"LH": 0.0, "HH": 0.5, "LL": 0.6, "HL": 1.0}
EH_GRADES = ("양호", "보통", "주의", "경계")
EH_CUTS = {"상권": (41, 51, 62), "행정동": (42, 52, 61)}
KAKAO_CATEGORY_LABELS = {"FD6": "음식점", "CE7": "카페"}

EN2KO = {
    "stdr_yyqu_cd": "기준_년분기_코드",
    "trdar_cd": "상권_코드",
    "adstrd_cd": "행정동_코드",
    "svc_induty_cd": "서비스_업종_코드",
    "stor_co": "전체_점포_수",
    "frc_stor_co": "프랜차이즈_점포_수",
    "opbiz_rt": "개업_율",
    "clsbiz_rt": "폐업_률",
    "opbiz_stor_co": "개업_점포_수",
    "clsbiz_stor_co": "폐업_점포_수",
    "thsmon_selng_amt": "당월_매출_금액",
    "thsmon_selng_co": "당월_매출_건수",
    "점포_수": "전체_점포_수",
}


class PipelineError(ValueError):
    """입력·공간·결합 계약을 만족하지 못한 경우."""


class PipelineInputError(PipelineError):
    """호출자가 수정할 수 있는 요청·입력 계약 오류."""


class PipelineDependencyError(PipelineError):
    """DB·LLM·원천 데이터 등 외부 의존성 장애."""


class PipelineInternalError(PipelineError):
    """서비스 내부 산출물·검증 계약 위반."""


@dataclass(frozen=True)
class RecommendationRequest:
    sido: str
    sigungu: str
    dong: str | None
    industry_code: str | None
    special_condition_text: str = ""
    quarter: str = DEFAULT_QUARTER
    sigungu_code: str | None = None
    admin_dong_code: str | None = None


@dataclass
class LayerRecord:
    code: str
    name: str
    area: float
    raw: dict[str, Any]


@dataclass(frozen=True)
class PoiContext:
    """완결 manifest를 가진 Kakao rect 관측 스냅샷.

    이 객체는 후보 seed가 아니라 지점 반경의 관측 맥락이다. 즉 POI 수를
    성공·수요·공실의 대리값이나 fit_tier 정렬 근거로 쓰지 않는다.
    """

    points: list[tuple[Point, dict[str, str]]]
    csv_path: Path
    manifest_path: Path
    retrieved_at: str
    categories: tuple[str, ...]


@dataclass(frozen=True)
class NewsCatalog:
    """정규화된 뉴스 메타데이터 snapshot.

    기사 본문이 아니라 정규화된 관측 레코드의 지역·주제 태그와 기사량만
    후보에 붙인다. 뉴스는 공식 도시계획사업의 대체물이 아니며 fit_tier·정렬
    입력으로 사용하지 않는다.
    """

    records: list[dict[str, Any]]
    jsonl_path: Path
    manifest_path: Path
    query_label: str
    query_metadata_verified: bool
    period_start: str
    period_end: str
    raw_row_count: int
    normalized_row_count: int
    excluded_counts: dict[str, int]
    dedupe_counts: dict[str, int]
    source_name: str = "bigkinds"
    retrieved_at_utc: str | None = None
    queries: tuple[str, ...] = ()
    topic_match_count: int = 0


class ShapeLayer:
    def __init__(self, path: Path, code_field: str, name_field: str, area_field: str = "RELM_AR"):
        sf = shapefile.Reader(str(path), encoding="utf-8")
        fields = [field[0] for field in sf.fields[1:]]
        self.geoms = []
        self.records: list[LayerRecord] = []
        for sr in sf.iterShapeRecords():
            raw = dict(zip(fields, sr.record))
            geom = shape(sr.shape.__geo_interface__)
            self.geoms.append(geom)
            try:
                area = float(raw.get(area_field) or 0)
            except (TypeError, ValueError):
                area = 0.0
            self.records.append(LayerRecord(
                code=str(raw.get(code_field, "")).strip(),
                name=str(raw.get(name_field, "")).strip(),
                area=area,
                raw=raw,
            ))
        self.tree = STRtree(self.geoms)

    @classmethod
    def from_records(cls, records: list[LayerRecord], geoms: list[Any]) -> "ShapeLayer":
        """이미 만들어진 (records, geoms)로 레이어를 구성한다 — DB 소스용.

        shapefile 생성자와 동일한 불변식: ``records[i]`` ↔ ``geoms[i]`` 정렬.
        """
        layer = cls.__new__(cls)
        layer.records = list(records)
        layer.geoms = list(geoms)
        layer.tree = STRtree(layer.geoms)
        return layer

    def query_indices(self, geometry: Any) -> list[int]:
        return [int(i) for i in self.tree.query(geometry)]

    def covering(self, point: Point) -> list[tuple[int, LayerRecord]]:
        return [(i, self.records[i]) for i in self.query_indices(point) if self.geoms[i].covers(point)]

    def nearest(self, point: Point) -> tuple[int, LayerRecord, float]:
        index = int(self.tree.nearest(point))
        return index, self.records[index], self.geoms[index].distance(point)


def choose_most_specific(hits: list[tuple[int, LayerRecord]]) -> LayerRecord | None:
    """겹치는 폴리곤 중 가장 작은 유효 면적을 고르고, 코드로 tie-break한다.

    STRtree의 query 순서는 계약된 업무 우선순위가 아니므로 ``hits[0]``을
    사용하면 동일 지점의 결과가 실행마다 달라질 수 있다. 작은 면적을 우선하면
    중첩된 상권에서 더 구체적인 경계를 선택하고, 면적이 없는 레코드는 뒤로
    보낸다.
    """
    if not hits:
        return None
    return min(
        (record for _, record in hits),
        key=lambda record: (record.area <= 0, record.area if record.area > 0 else float("inf"), record.code),
    )


def nfc(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()


def find_file(directory: Path, needle: str, suffix: str = ".csv") -> Path:
    directory = Path(directory)
    try:
        matches = [
            p for p in directory.iterdir()
            if p.is_file() and p.suffix.lower() == suffix.lower()
            and nfc(needle) in nfc(p.name)
        ]
    except OSError as exc:
        raise PipelineDependencyError(f"원천 데이터 디렉터리를 읽을 수 없음: {directory}") from exc
    if not matches:
        raise PipelineDependencyError(f"파일을 찾을 수 없음: {directory}/{needle}{suffix}")
    if len(matches) > 1:
        # 파일명에 동일 토큰이 반복되는 경우에는 가장 짧은 정규 파일명을 우선한다.
        matches.sort(key=lambda p: (len(nfc(p.name)), nfc(p.name)))
    return matches[0]


def find_shape(directory: Path, needle: str) -> Path:
    try:
        matches = [p for p in Path(directory).iterdir() if p.suffix.lower() == ".shp" and nfc(needle) in nfc(p.name)]
    except OSError as exc:
        raise PipelineDependencyError(f"공간 데이터 디렉터리를 읽을 수 없음: {directory}") from exc
    if not matches:
        raise PipelineDependencyError(f"SHP를 찾을 수 없음: {directory}/{needle}")
    return sorted(matches, key=lambda p: len(nfc(p.name)))[0]


def read_csv(path: Path) -> list[dict[str, str]]:
    errors: list[str] = []
    for encoding in ("utf-8-sig", "cp949", "utf-8"):
        try:
            with path.open(encoding=encoding, newline="") as handle:
                rows = list(csv.DictReader(handle))
            return [{EN2KO.get(k, k): v for k, v in row.items()} for row in rows]
        except UnicodeDecodeError as exc:
            errors.append(f"{encoding}: {exc}")
        except OSError as exc:
            raise PipelineDependencyError(f"CSV를 읽을 수 없음: {path}") from exc
    raise PipelineDependencyError(f"CSV 인코딩을 읽지 못함: {path}; {errors}")


def num(row: dict[str, Any] | None, key: str) -> float | None:
    if not row:
        return None
    value = row.get(key)
    if value is None or str(value).strip() in ("", "-", "N/A"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def as_int(value: float | None) -> int | None:
    return None if value is None else int(round(value))


def pg_bool(value: Any) -> bool:
    """serving_db 계열이 COPY ... CSV로 반환하는 postgres boolean('t'/'f' 문자열) 안전 변환.

    ``bool("f")``는 파이썬에서 True다(비어있지 않은 문자열) — 이 변환을 명시하지
    않으면 층별용도 미확인 건물까지 확인된 것으로 처리된다(PR #14 리뷰 P1).
    """
    return str(value).strip().lower() in ("t", "true", "1")


def previous_quarter(quarter: str) -> str:
    year, qtr = int(quarter[:4]), int(quarter[4])
    if qtr == 1:
        return f"{year - 1}4"
    return f"{year}{qtr - 1}"


def pct(values: list[float], value: float | None) -> float | None:
    if not values or value is None:
        return None
    return round(100 * bisect_left(values, value) / len(values), 1)


def safe_slug(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z가-힣_-]+", "-", value).strip("-")
    return value or "run"


def normalize_admin_dong_name(value: str) -> str:
    """Normalize catalog text to the shapefile's 행정동 spelling.

    Some exported Seoul CSV snapshots replace the middle dot in names such
    as ``상계3·4동`` with ``?``. The shapefile is the spatial source of truth,
    so the API catalog and resolver use the same canonical spelling.
    """
    return unicodedata.normalize("NFC", str(value or "").strip()).replace("?", "·")


def load_layers() -> tuple[ShapeLayer, ShapeLayer, ShapeLayer, dict[str, str]]:
    trdar = ShapeLayer(find_shape(ROOT / "data/영역/상권", "영역-상권"), "TRDAR_CD", "TRDAR_CD_N")
    hinterland = ShapeLayer(find_shape(ROOT / "data/영역/상권배후지", "영역-상권배후지"), "ALLEY_TRDA", "ALLEY_TR_1")
    dong = ShapeLayer(find_shape(ROOT / "data/영역/행정동", "영역-행정동"), "ADSTRD_CD", "ADSTRD_NM")
    sigungu_by_prefix = {
        str(rec.raw.get("SIGNGU_CD", "")).strip(): str(rec.raw.get("SIGNGU_CD_", "")).strip()
        for rec in trdar.records
        if rec.raw.get("SIGNGU_CD")
    }
    return trdar, hinterland, dong, sigungu_by_prefix


LEGAL_DONG_ALIASES: dict[str, tuple[str, ...]] = {
    # 현재 데이터에는 행정동만 있으므로 확인된 법정동 분해만 명시한다.
    "잠실동": ("잠실2동", "잠실3동", "잠실7동"),
}


def resolve_region(
    request: RecommendationRequest,
    dong_layer: ShapeLayer,
    sigungu_by_prefix: dict[str, str],
) -> tuple[list[LayerRecord], Any, Any]:
    if request.sido not in ("서울특별시", "서울"):
        raise PipelineInputError("현재 데이터 계약은 서울특별시만 지원합니다.")
    all_sigungus = {name for name in sigungu_by_prefix.values() if name}
    gu_code = getattr(request, 'sigungu_code', None)
    dong_code = getattr(request, 'admin_dong_code', None)
    if dong_code and (not re.fullmatch(r'[0-9]{8}', dong_code) or (gu_code and not dong_code.startswith(gu_code))):
        raise PipelineInputError('행정동 코드 형식 또는 시군구 연결이 올바르지 않습니다.')
    gu_code = gu_code or (dong_code[:5] if dong_code else None)
    if gu_code and (not re.fullmatch(r'[0-9]{5}', gu_code) or gu_code not in sigungu_by_prefix):
        raise PipelineInputError('시군구 코드를 확인할 수 없습니다.')
    if not gu_code and request.sigungu not in all_sigungus:
        raise PipelineInputError(f"시군구를 확인할 수 없습니다: {request.sigungu}")
    in_gu = [r for r in dong_layer.records if (r.code[:5] == gu_code if gu_code
             else sigungu_by_prefix.get(r.code[:5]) == request.sigungu)]
    if not in_gu:
        raise PipelineDependencyError(f"행정동 데이터에서 시군구가 비어 있습니다: {request.sigungu}")

    if dong_code:
        selected = [r for r in in_gu if r.code == dong_code]
        if not selected:
            raise PipelineInputError('행정동 코드를 확인할 수 없습니다.')
    elif request.dong:
        requested_dong = normalize_admin_dong_name(request.dong)
        exact = [r for r in in_gu if normalize_admin_dong_name(r.name) == requested_dong]
        selected = exact or [r for r in in_gu if r.name in LEGAL_DONG_ALIASES.get(request.dong, ())]
        if not selected:
            choices = ", ".join(r.name for r in in_gu[:20])
            raise PipelineInputError(f"행정동을 확인할 수 없습니다: {request.dong}. 후보 예: {choices}")
    else:
        selected = in_gu
    target_geoms = [dong_layer.geoms[dong_layer.records.index(rec)] for rec in selected]
    target_poly = unary_union(target_geoms)
    # 후보는 선택 범위(행정동을 골랐으면 그 행정동, 아니면 시군구 전체) 폴리곤 안으로만
    # 한정한다. 예전에는 300m 버퍼를 둬서 인접 행정동·시군구 건물이 후보로 끌려왔다
    # (서교동 요청에 대흥동·서강동 건물, 역삼1동 요청에 서초구 서초동 건물).
    return selected, target_poly, target_poly


def index_rows(rows: Iterable[dict[str, str]], keys: tuple[str, ...], filters: dict[str, str]) -> dict[tuple[str, ...], dict[str, str]]:
    indexed: dict[tuple[str, ...], dict[str, str]] = {}
    for row in rows:
        if any(row.get(k) != v for k, v in filters.items()):
            continue
        key = tuple(str(row.get(k, "")).strip() for k in keys)
        if any(not part for part in key):
            continue
        if key in indexed:
            raise PipelineInternalError(f"결합 키 중복: keys={keys}, key={key}")
        indexed[key] = row
    return indexed


def load_scope_index(folder: str, token: str, quarter: str, scope_key: str, industry: str | None = None) -> tuple[dict[str, dict[str, str]], Path]:
    path = find_file(ROOT / folder, token)
    rows = read_csv(path)
    filters = {"기준_년분기_코드": quarter}
    keys = (scope_key, "서비스_업종_코드") if industry else (scope_key,)
    if industry:
        filters["서비스_업종_코드"] = industry
    raw = index_rows(rows, keys, filters)
    return {key[0]: row for key, row in raw.items()}, path


def aggregate_environment(rows: Iterable[dict[str, str]], quarter: str, scope_key: str) -> dict[str, dict[str, float | None]]:
    agg: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0.0, "open": 0.0, "close": 0.0})
    for row in rows:
        if row.get("기준_년분기_코드") != quarter or not row.get(scope_key):
            continue
        key = row[scope_key]
        agg[key]["n"] += num(row, "전체_점포_수") or 0
        agg[key]["open"] += num(row, "개업_점포_수") or 0
        agg[key]["close"] += num(row, "폐업_점포_수") or 0
    return {
        key: {
            "open_r": 100 * values["open"] / values["n"] if values["n"] else None,
            "close_r": 100 * values["close"] / values["n"] if values["n"] else None,
            "n_now": values["n"],
        }
        for key, values in agg.items()
        if values["n"]
    }


def build_environment(current_rows: Iterable[dict[str, str]], previous_rows: Iterable[dict[str, str]], quarter: str, scope_key: str) -> tuple[dict[str, dict[str, float | None]], dict[str, list[float]]]:
    current = aggregate_environment(current_rows, quarter, scope_key)
    previous_q = previous_quarter(quarter)
    previous: dict[str, float] = defaultdict(float)
    for row in previous_rows:
        if row.get("기준_년분기_코드") == previous_q and row.get(scope_key):
            previous[row[scope_key]] += num(row, "전체_점포_수") or 0
    for key, item in current.items():
        base = previous.get(key, 0)
        item["delta"] = (item["n_now"] - base) / base if base else None
    dist = {
        "close": sorted(v["close_r"] for v in current.values() if v["close_r"] is not None),
        "open": sorted(v["open_r"] for v in current.values() if v["open_r"] is not None),
        "delta": sorted(v["delta"] for v in current.values() if v["delta"] is not None),
    }
    return current, dist


def entry_health(scope: str, record: dict[str, float | None] | None, dist: dict[str, list[float]], label: str | None) -> tuple[str, float | None, dict[str, float | None]]:
    if not record:
        return "정보없음", None, {"폐업률분위": None, "개업률분위": None, "점포증감률분위": None, "라벨리스크": None}
    close_p = pct(dist["close"], record.get("close_r"))
    open_p = pct(dist["open"], record.get("open_r"))
    delta_p = pct(dist["delta"], record.get("delta"))
    label_risk = EH_LABEL_RISK.get(label, 0.5) if label else 0.5
    parts = [p for p in (close_p, 100 - open_p if open_p is not None else None, 100 - delta_p if delta_p is not None else None) if p is not None]
    parts.append(label_risk * 100)
    risk = round(sum(parts) / len(parts), 1)
    c1, c2, c3 = EH_CUTS[scope]
    grade = EH_GRADES[0] if risk < c1 else EH_GRADES[1] if risk < c2 else EH_GRADES[2] if risk < c3 else EH_GRADES[3]
    return grade, risk, {"폐업률분위": close_p, "개업률분위": open_p, "점포증감률분위": delta_p, "라벨리스크": label_risk}


def load_generated_seeds(path: Path, target_buffer: Any) -> list[dict[str, Any]]:
    """generate_gridpoint_evidence.py의 seeds.json + gridpoint_evidence.jsonl을 읽어 격자 합성 좌표 seed로.

    gridpoint_evidence.jsonl이 있으면 각 seed에 그 레코드(metrics·정의·출처)를 붙여
    build_candidate가 반경 인허가 맥락을 후보 evidence로 연결할 수 있게 한다.
    """
    # 상대·절대 경로 모두 지원 — ROOT 기준으로 해석해 relative_to(ROOT)가 항상 동작하게 한다.
    p = Path(path)
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    base = p if p.is_dir() else p.parent
    seeds_file = p / "seeds.json" if p.is_dir() else p
    payload = json.loads(seeds_file.read_text(encoding="utf-8"))

    ev_file = base / "gridpoint_evidence.jsonl"
    ev_by_id: dict[str, dict[str, Any]] = {}
    if ev_file.is_file():
        for line in ev_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                ev_by_id[rec.get("evidence_id", "")] = rec

    def _rel(fp: Path) -> str:
        try:
            return str(fp.relative_to(ROOT))
        except ValueError:
            return str(fp)

    ev_rel = _rel(ev_file) if ev_file.is_file() else None
    seeds_rel = _rel(seeds_file)

    out: list[dict[str, Any]] = []
    for s in payload.get("seeds", []):
        try:
            point = Point(float(s["x"]), float(s["y"]))
        except (TypeError, ValueError, KeyError):
            continue
        if not target_buffer.covers(point):
            continue
        out.append({
            "kind": "생성지점", "id": s["anchor_id"], "name": s["anchor_id"], "pt": point,
            "households": None, "line": None, "transfer": None,
            "grid_id": s.get("grid_id"), "evidence_id": s.get("evidence_id"),
            "gen_evidence": ev_by_id.get(s.get("evidence_id", "")),
            "gen_evidence_path": ev_rel,
            "source_path": seeds_rel,
        })
    return out


def _apt_seed(row: dict[str, str], point: Point, source_path: str) -> dict[str, Any]:
    return {"kind": "아파트단지", "id": row.get("단지코드", ""), "name": row.get("단지명", ""), "pt": point,
            "households": as_int(num(row, "세대수")), "line": None, "transfer": None, "source_path": source_path}


def _station_seed(row: dict[str, str], point: Point, source_path: str) -> dict[str, Any]:
    return {"kind": "역", "id": row.get("역번호", ""), "name": row.get("역사명", ""), "pt": point,
            "households": None, "line": row.get("노선명"), "transfer": row.get("환승역") == "Y", "source_path": source_path}


def _poi_seed(row: dict[str, str], point: Point, source_path: str) -> dict[str, Any]:
    return {"kind": "카카오POI", "id": row.get("poi_id", ""), "name": row.get("place_name", ""), "pt": point,
            "households": None, "line": None, "transfer": None,
            "category_name": row.get("category_name"), "source_path": source_path}


def _building_seed(row: dict[str, str], point: Point, source_path: str) -> dict[str, Any]:
    """건축물대장 파생 CSV의 개별 건물을 후보 지점 seed로 만든다.

    건물 centroid는 실제 임대 가능 호실이나 점포 주소가 아니다. 따라서 이
    seed는 좌표 기준점으로만 사용하고, build_candidate에서
    ``매물·공실·호실 아님`` caveat를 강제한다(2026-09-05: tier 캡은 걷지 않음
    — footprint가 실재 공공데이터 기반이라 루트 scripts/recommendation_pipeline.py와
    정책 통일, PR #14 리뷰 반영).

    Tier2(건축HUB 표제부·층별용도) 보강 필드(도로명주소·건물명·상업층 확인여부)는
    호출부(load_seeds/_building_rows)가 PNU 단위로 미리 조인해 row에 얹어준다 —
    한 PNU에 실재 건물이 여럿이면(다중후보) 호출부가 이미 결측 처리했으므로
    여기서는 있는 값만 그대로 받는다.
    """
    building_id = nfc(row.get("건물관리번호"))
    lot_address = nfc(row.get("대지위치"))
    use_group = nfc(row.get("용도군"))
    road_address = nfc(row.get("도로명주소")) or None
    building_name = nfc(row.get("건물명")) or None
    name = building_name or road_address or lot_address or f"상가건물 {building_id[-8:]}"
    return {
        "kind": "상가건물", "id": building_id, "name": name, "pt": point,
        "households": None, "line": None, "transfer": None,
        "building_pk": building_id, "pnu": nfc(row.get("PNU")),
        "lot_address": lot_address, "lot_number": nfc(row.get("지번")),
        "use_code": nfc(row.get("용도코드")), "use_name": nfc(row.get("용도명")),
        "use_group": use_group, "floors_above": as_int(num(row, "지상층수")),
        "floors_below": as_int(num(row, "지하층수")),
        "building_area_m2": num(row, "건축면적_㎡"),
        "gross_floor_area_m2": num(row, "연면적_㎡"),
        "building_age_years": as_int(num(row, "건물연식_년")),
        "area_join_type": nfc(row.get("상권_결합")) or "미결합",
        "host_area_code": nfc(row.get("상권_코드")),
        "admin_dong_code": nfc(row.get("행정동_코드")),
        "admin_dong_name": nfc(row.get("행정동_명")),
        "road_address": road_address, "building_name": building_name,
        "has_confirmed_commercial_floor": bool(row.get("_has_confirmed_commercial_floor")),
        "snapshot": "20260809",
        "source_paths": {source_path},
        "source_path": source_path,
    }


BUILDING_SEED_CAP = 40  # spec §1-0/§1-1.6 상가건물 밀도 캡 — 아파트·역보다 자릿수가 다른 밀도라 필요


def _farthest_point_sample(pts: list[Point], k: int) -> list[int]:
    """지리적으로 최대한 퍼진 k개 인덱스를 그리디로 고른다."""
    if len(pts) <= k:
        return list(range(len(pts)))
    cx = sum(p.x for p in pts) / len(pts)
    cy = sum(p.y for p in pts) / len(pts)
    center = Point(cx, cy)
    chosen = [min(range(len(pts)), key=lambda i: pts[i].distance(center))]
    mind = [pts[i].distance(pts[chosen[0]]) for i in range(len(pts))]
    while len(chosen) < k:
        nxt = max(range(len(pts)), key=lambda i: mind[i] if i not in chosen else -1)
        chosen.append(nxt)
        for i in range(len(pts)):
            d = pts[i].distance(pts[nxt])
            if d < mind[i]:
                mind[i] = d
    return sorted(chosen)


def _fps_cap(seeds: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(seeds) <= limit:
        return seeds
    pool = sorted(seeds, key=lambda s: s.get("gross_floor_area_m2") or 0, reverse=True)[: max(limit * 3, limit)]
    idxs = _farthest_point_sample([s["pt"] for s in pool], limit)
    return [pool[i] for i in idxs]


def _cap_building_seeds(building_seeds: list[dict[str, Any]], limit: int = BUILDING_SEED_CAP) -> list[dict[str, Any]]:
    """상가건물 밀도 캡: (a) Tier2 층별용도 확인 건물은 limit 이내 전부 포함
    (b) 남은 슬롯만 미확인 건물의 연면적 상위 풀에서 (c) 최원점 표본추출.

    2026-09-05 PR #14 리뷰: seed_mode="buildings"(기본)가 target_buffer 안의
    건물을 캡 없이 전부 반환해, 밀집 지역(역삼1동 실측 2,342개)에서 응답
    지연·타임아웃 위험이 있었다. 루트 scripts/recommendation_pipeline.py와
    동일 알고리즘.
    """
    raw_size = len(building_seeds)
    if raw_size > limit:
        confirmed = [s for s in building_seeds if s.get("has_confirmed_commercial_floor")]
        unconfirmed = [s for s in building_seeds if not s.get("has_confirmed_commercial_floor")]
        if len(confirmed) >= limit:
            building_seeds = _fps_cap(confirmed, limit)
        else:
            building_seeds = confirmed + _fps_cap(unconfirmed, limit - len(confirmed))
    for s in building_seeds:
        s["_raw_building_pool_size"] = raw_size
    return building_seeds


def merge_seeds(seeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """80m 병합 + 우선순위 정렬. 입력 순서와 무관하게 결정적."""
    priority = {"역": 0, "아파트단지": 1, "카카오POI": 2, "상가건물": 3, "생성지점": 4}
    merged: list[dict[str, Any]] = []
    for seed in sorted(seeds, key=lambda s: (priority[s["kind"]], s["name"], s["id"])):
        hit = next((item for item in merged if item["pt"].distance(seed["pt"]) <= DEFAULT_DEDUP_M), None)
        if hit:
            if seed["name"] and seed["name"] not in hit["also"] and seed["name"] != hit["name"]:
                hit["also"].append(f"{seed['name']}({seed['kind']})")
            hit["source_paths"].add(seed["source_path"])
            if seed["kind"] == "역" and hit["kind"] == "역" and seed.get("line"):
                hit["lines"].add(seed["line"])
        else:
            item = dict(seed)
            item["also"] = []
            item["lines"] = {seed["line"]} if seed.get("line") else set()
            item["source_paths"] = {seed["source_path"]}
            merged.append(item)
    return merged


def _poi_seeds_from_files(target_buffer: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for poi_path in sorted((ROOT / "data/카카오POI").glob("*.csv")):
        src = str(poi_path.relative_to(ROOT))
        for row in read_csv(poi_path):
            try:
                point = Point(float(row["x_5181"]), float(row["y_5181"]))
            except (TypeError, ValueError, KeyError):
                continue
            if target_buffer.covers(point):
                out.append(_poi_seed(row, point, src))
    return out


def _enrich_tier1_rows_with_tier2(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Tier1 건물 행에 Tier2(건축HUB 표제부·층별용도) 도로명주소·건물명·상업층 확인여부를 얹는다.

    한 PNU에 실재 건물이 여럿이면(다중후보) 임의로 승격하지 않는다 — PNU당
    distinct mgmBldrgstPk가 정확히 1개일 때만 enrichment를 채택한다(PR #14
    리뷰 P2, 루트 scripts/recommendation_pipeline.py와 동일 규칙). Tier2는
    자치구별 파일이라 filtered rows에 실제 등장한 시군구명만 로드한다.
    """
    api_dir = ROOT / "data/건축물대장/api"
    gus = {nfc(r.get("시군구명")) for r in rows if r.get("시군구명")}

    link_by_pnu: dict[str, dict[str, str]] = {}
    name_by_pk: dict[str, str] = {}
    floor_confirmed_pks: set[str] = set()
    for gu in gus:
        link_rows_by_pnu: dict[str, list[dict[str, str]]] = defaultdict(list)
        link_path = api_dir / f"건물링크_{gu}.csv"
        if link_path.is_file():
            for row in read_csv(link_path):
                pnu = row.get("PNU")
                if pnu and row.get("mgmBldrgstPk"):
                    link_rows_by_pnu[pnu].append(row)
        for pnu, link_rows in link_rows_by_pnu.items():
            if len({r["mgmBldrgstPk"] for r in link_rows}) == 1:
                link_by_pnu[pnu] = link_rows[0]

        title_path = api_dir / f"표제부_{gu}.csv"
        if title_path.is_file():
            for row in read_csv(title_path):
                pk = row.get("mgmBldrgstPk")
                if pk and row.get("건물명"):
                    name_by_pk[pk] = row["건물명"]

        for floor_filename in (f"층별용도_{gu}.csv", "층별용도_선별.csv"):
            floor_path = api_dir / floor_filename
            if floor_path.is_file():
                for row in read_csv(floor_path):
                    pk = row.get("mgmBldrgstPk")
                    if pk:
                        floor_confirmed_pks.add(pk)

    for row in rows:
        pnu = row.get("PNU")
        link = link_by_pnu.get(pnu) if pnu else None
        mgm_pk = link.get("mgmBldrgstPk") if link else None
        row["도로명주소"] = (link.get("도로명주소") if link else None) or ""
        row["건물명"] = (name_by_pk.get(mgm_pk) if mgm_pk else None) or ""
        row["_has_confirmed_commercial_floor"] = bool(mgm_pk and mgm_pk in floor_confirmed_pks)
    return rows


def load_building_seeds(target_buffer: Any, rows: Iterable[dict[str, str]], source_path: str) -> list[dict[str, Any]]:
    """유효한 건물 centroid를 공간 버퍼 안에서 결정적으로 반환한다."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        building_id = nfc(row.get("건물관리번호"))
        if not building_id or building_id in seen:
            continue
        try:
            point = Point(float(row["x_5181"]), float(row["y_5181"]))
        except (TypeError, ValueError, KeyError):
            continue
        if not target_buffer.covers(point):
            continue
        seen.add(building_id)
        out.append(_building_seed(row, point, source_path))
    return _cap_building_seeds(sorted(out, key=lambda seed: (seed["name"], seed["id"])))


def load_seeds(
    target_buffer: Any,
    include_poi: bool,
    generated_path: Path | None = None,
    seed_mode: str = "buildings",
) -> list[dict[str, Any]]:
    if seed_mode not in {"anchors", "buildings", "hybrid"}:
        raise PipelineInputError("seed_mode는 anchors, buildings, hybrid 중 하나여야 합니다.")
    if seed_mode == "buildings":
        building_path = find_file(ROOT / "data/건축물대장", "상가건물_서울")
        building_src = str(building_path.relative_to(ROOT))
        rows_in_buffer = []
        for row in read_csv(building_path):
            try:
                point = Point(float(row["x_5181"]), float(row["y_5181"]))
            except (TypeError, ValueError, KeyError):
                continue
            if target_buffer.covers(point):
                rows_in_buffer.append(row)
        return load_building_seeds(target_buffer, _enrich_tier1_rows_with_tier2(rows_in_buffer), building_src)

    seeds: list[dict[str, Any]] = []
    apt_path = find_file(ROOT / "data/공동주택", "아파트단지_서울")
    apt_src = str(apt_path.relative_to(ROOT))
    for row in read_csv(apt_path):
        if row.get("geocode_신뢰도") not in ("high", "medium") or not row.get("X_5181"):
            continue
        try:
            point = Point(float(row["X_5181"]), float(row["Y_5181"]))
        except (TypeError, ValueError):
            continue
        if target_buffer.covers(point):
            seeds.append(_apt_seed(row, point, apt_src))

    station_path = find_file(ROOT / "data/도시철도역사", "역사정보_서울")
    station_src = str(station_path.relative_to(ROOT))
    for row in read_csv(station_path):
        try:
            point = Point(float(row["X_5181"]), float(row["Y_5181"]))
        except (TypeError, ValueError, KeyError):
            continue
        if target_buffer.covers(point):
            seeds.append(_station_seed(row, point, station_src))

    if include_poi:
        seeds.extend(_poi_seeds_from_files(target_buffer))
    if generated_path is not None:
        seeds.extend(load_generated_seeds(generated_path, target_buffer))
    if seed_mode == "hybrid":
        # 80m merge는 건물별 seed를 합쳐 버리므로 building seed는 개별성을
        # 보존하고, 기존 역·단지·POI·생성점만 레거시 규칙으로 병합한다.
        building_path = find_file(ROOT / "data/건축물대장", "상가건물_서울")
        building_src = str(building_path.relative_to(ROOT))
        rows_in_buffer = []
        for row in read_csv(building_path):
            try:
                point = Point(float(row["x_5181"]), float(row["y_5181"]))
            except (TypeError, ValueError, KeyError):
                continue
            if target_buffer.covers(point):
                rows_in_buffer.append(row)
        buildings = load_building_seeds(target_buffer, _enrich_tier1_rows_with_tier2(rows_in_buffer), building_src)
        return merge_seeds(seeds) + buildings
    return merge_seeds(seeds)


def relative_path(path: Path | str) -> str:
    return str(Path(path).relative_to(ROOT)) if isinstance(path, Path) else str(path)


def atomic_write_text(path: Path, text: str) -> None:
    """Replace a text artifact only after it has been fully written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def evidence(
    name: str, value: Any, unit: str, source_type: str, scope: str, period: str, grain: str,
    proxy: bool, source_path: str, quarter_file_source: str, normalization: list[str],
    interpretation: str, limitation: str, *, percentile: float | None = None,
    missing_reason: str | None = None, proxy_note: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "metric_name": name, "value": value, "unit": unit, "source_type": source_type,
        "comparison_scope": scope, "period": period, "spatial_grain": grain,
        "grain_is_proxy": proxy, "source_path": source_path,
        "quarter_file_source": quarter_file_source, "normalization": normalization,
        "interpretation": interpretation, "limitation": limitation,
    }
    if percentile is not None:
        item["seoul_percentile"] = percentile
    if value is None:
        item["missing_reason"] = missing_reason or "결측"
    if proxy:
        item["proxy_note"] = proxy_note or "다른 공간 단위의 배경값 대리"
    return item


def parse_trdar_crosswalk() -> dict[str, dict[str, str]]:
    path = ROOT / "output/crosswalks/crosswalk_rone_trdar.csv"
    if not path.exists():
        return {}
    rows = read_csv(path)
    return {
        row["TRDAR_CD"]: row
        for row in rows
        if row.get("TRDAR_CD") and row.get("join_eligible") == "yes"
    }


def parse_rone_rent() -> tuple[dict[str, dict[str, str]], dict[str, str], Path | None]:
    path = ROOT / "data/임대료/R-ONE_임대동향_분기.csv"
    if not path.exists():
        return {}, {}, None
    rows = read_csv(path)
    area_rows: dict[str, dict[str, str]] = {}
    seoul_rows: dict[str, str] = {}
    for row in rows:
        if row.get("상가유형") != "소규모상가" or row.get("지표") != "임대가격지수":
            continue
        quarter = row.get("기준_년분기_코드", "")
        if row.get("grain") == "상권" and row.get("R_ONE_상권"):
            old = area_rows.get(row["R_ONE_상권"])
            if old is None or quarter > old.get("기준_년분기_코드", ""):
                area_rows[row["R_ONE_상권"]] = row
        elif row.get("grain") == "서울전체":
            if quarter > seoul_rows.get("quarter", ""):
                seoul_rows = {"quarter": quarter, "value": row.get("값", "")}
    return area_rows, seoul_rows, path


def parse_rone_vacancy() -> tuple[dict[str, dict[str, str]], dict[str, str], Path | None]:
    """FC-21 공실률. scripts/ingest_vacancy_rate.py 산출(R-ONE Open API 실시간 이식).

    parse_rone_rent()과 같은 store_type(소규모상가)·grain 규칙을 쓴다 — 임대료·공실률을
    같은 상가유형으로 맞춰야 같은 후보에 대해 서로 다른 상가유형 값을 섞지 않는다.
    """
    path = ROOT / "data/임대료/R-ONE_공실률_분기.csv"
    if not path.exists():
        return {}, {}, None
    rows = read_csv(path)
    area_rows: dict[str, dict[str, str]] = {}
    seoul_rows: dict[str, str] = {}
    for row in rows:
        if row.get("상가유형") != "소규모상가" or row.get("지표") != "공실률":
            continue
        quarter = row.get("기준_년분기_코드", "")
        if row.get("grain") == "상권" and row.get("R_ONE_상권"):
            old = area_rows.get(row["R_ONE_상권"])
            if old is None or quarter > old.get("기준_년분기_코드", ""):
                area_rows[row["R_ONE_상권"]] = row
        elif row.get("grain") == "서울전체":
            if quarter > seoul_rows.get("quarter", ""):
                seoul_rows = {"quarter": quarter, "value": row.get("값", "")}
    return area_rows, seoul_rows, path


def load_naver_industry_attention(industry_code: str) -> tuple[dict[str, Any] | None, dict[str, Path]]:
    """FC-42 업종의 현재 검색 관심도만 읽는다.

    검색 관심도는 후보의 품질·성공확률·순위를 만드는 값이 아니다. 최근 3개월
    원계열 평균과 최신월을 현재 관심도 맥락으로 표시하고, 계절 보정 기울기는
    비계절성 추세를 점검하는 분석 산출물로만 보존한다.
    """
    trend_path = ROOT / "data/네이버트렌드/업종_검색트렌드_월.csv"
    seasonality_path = ROOT / "output/feature_validation/naver_seasonality.csv"
    if not trend_path.is_file():
        return None, {}

    rows = [row for row in read_csv(trend_path) if row.get("업종코드") == industry_code]
    points: list[tuple[str, float]] = []
    industry_name = ""
    for row in rows:
        industry_name = row.get("업종명", industry_name)
        ym = nfc(row.get("기준_년월"))
        rel = num(row, "rel_index")
        if re.fullmatch(r"[0-9]{6}", ym) and rel is not None:
            points.append((ym, rel))
    if not points:
        return None, {}

    season: dict[str, str] = {}
    if seasonality_path.is_file():
        for row in read_csv(seasonality_path):
            if row.get("grain") == "업종" and row.get("key") == industry_name:
                season = row
                break
    # seasonality 파일은 grain=업종 행이 없어 현재 산출에 기여하지 않는다 → provenance 에서 제외
    # (DB 모드와 근거 출처를 일치시키기 위함).
    return _naver_attention_compute(points, industry_name, industry_code, season), {"naver_trend": trend_path}


def _naver_attention_compute(
    points: list[tuple[str, float]], industry_name: str, industry_code: str, season: dict[str, str],
) -> dict[str, Any]:
    points = sorted(points)
    latest_ym, latest_rel = points[-1]
    recent3 = [value for _, value in points[-3:]]
    all_values = [value for _, value in points]

    peak_month = int(season["peak_month"]) if season.get("peak_month", "").isdigit() else None
    trough_month = int(season["trough_month"]) if season.get("trough_month", "").isdigit() else None
    latest_month = int(latest_ym[4:6])
    surge_active = season.get("surge_active", "").lower() == "true"
    recent3_mean = round(statistics.mean(recent3), 3)
    all_mean = round(statistics.mean(all_values), 3)
    # 현재 관심도 배수는 전년 동월 대비(YoY, 이상치 월 제외) — analyze_naver_seasonality.py 산출.
    # '최근 3개월 ÷ 전체 5년 평균'은 장기 우상향 계열에서 항상 >1이 되어 오해를 준다.
    yoy_lift = num(season, "yoy_clean_recent_mean")
    if surge_active:
        status = "미검증 급등"
    elif peak_month == latest_month:
        status = "계절성 피크월"
    elif trough_month == latest_month:
        status = "계절성 저점월"
    elif yoy_lift is not None and yoy_lift >= 1.2:
        status = "전년 동월 대비 관심도 상승"
    elif yoy_lift is not None and yoy_lift <= 0.85:
        status = "전년 동월 대비 관심도 하락"
    elif yoy_lift is not None:
        status = "전년 동월 대비 관심도 유사"
    else:
        status = "YoY 비교 불가"

    return {
        "industry_code": industry_code,
        "industry_name": industry_name,
        "latest_ym": latest_ym,
        "latest_rel_index": round(latest_rel, 3),
        "recent3_rel_mean": recent3_mean,
        "all_rel_mean": all_mean,
        "yoy_clean_recent_mean": round(yoy_lift, 3) if yoy_lift is not None else None,
        "peak_month": peak_month,
        "trough_month": trough_month,
        "seasonal_amp": num(season, "seasonal_amp"),
        "surge_active": surge_active,
        "status": status,
    }


# 수동 뉴스 snapshot이 이 일수를 넘겨 관측되면 최신성 낮음으로 표시한다(재수집 신호).
NEWS_SNAPSHOT_STALE_DAYS = 45


def news_snapshot_age(observed_end_period: str | None) -> tuple[int | None, bool]:
    """관측 종료일(YYYY-MM-DD 또는 YYYY-MM)로부터 오늘까지 경과일과 stale 여부.

    수동 뉴스 snapshot은 '최신' 기준을 알 수 없어 ``periods_behind_latest``가
    항상 0이다. 대신 관측 종료일 기준 경과일로 오래된 snapshot을 표시한다.
    """
    text = (observed_end_period or "").strip()
    end: dt.date | None = None
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            end = dt.datetime.strptime(text, fmt).date()
            break
        except ValueError:
            end = None
    if end is None:
        return None, False
    age = (dt.date.today() - end).days
    return age, age > NEWS_SNAPSHOT_STALE_DAYS


def load_bigkinds_news() -> tuple[NewsCatalog | None, dict[str, Path]]:
    """정규화된 빅카인즈 뉴스 snapshot을 정적으로 로드한다.

    ``scripts/ingest_bigkinds_news.py``가 만든 JSONL만 읽는다. 원문 XLSX나
    기사 본문을 추천 실행 시점에 다시 읽지 않아, 추천 산출물에는 제목·URL이
    아니라 지역별 집계 근거만 연결된다.
    """
    news_dir = ROOT / "data/뉴스"
    paths = sorted(news_dir.glob("bigkinds_news_*.jsonl")) if news_dir.is_dir() else []
    if not paths:
        return None, {}
    jsonl_path = paths[-1]
    manifest_path = jsonl_path.with_name(f"{jsonl_path.stem}_manifest.json")
    if not manifest_path.is_file():
        return None, {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    period = manifest.get("search_period") or {}
    return NewsCatalog(
        records=records,
        jsonl_path=jsonl_path,
        manifest_path=manifest_path,
        query_label=str(manifest.get("query_label") or "빅카인즈 뉴스"),
        query_metadata_verified=bool(manifest.get("query_metadata_verified", False)),
        period_start=str(period.get("start") or "미상"),
        period_end=str(period.get("end") or "미상"),
        raw_row_count=int(manifest.get("raw_row_count") or 0),
        normalized_row_count=int(manifest.get("normalized_row_count") or len(records)),
        excluded_counts={str(k): int(v) for k, v in (manifest.get("excluded_counts") or {}).items()},
        dedupe_counts={str(k): int(v) for k, v in (manifest.get("dedupe_counts") or {}).items()},
        source_name="bigkinds",
        retrieved_at_utc=str(manifest["ingested_at_utc"]) if manifest.get("ingested_at_utc") else None,
        queries=(str(manifest.get("query_label") or "빅카인즈 뉴스"),),
        topic_match_count=int(manifest.get("topic_match_count") or sum(1 for record in records if record.get("topic_match"))),
    ), {"bigkinds_news": jsonl_path, "bigkinds_news_manifest": manifest_path}


def load_naver_news_snapshot() -> tuple[NewsCatalog | None, dict[str, Path]]:
    """사용자가 수동으로 저장한 현재 시점 네이버 뉴스 snapshot만 읽는다.

    추천 실행 중 네이버 API를 호출하거나 최신 결과를 자동으로 갱신하지 않는다.
    ``scripts/ingest_naver_news_snapshot.py``를 직접 실행해 파일을 교체한 경우에만
    다음 추천 실행에서 새 snapshot이 사용된다.
    """
    news_dir = ROOT / "data/뉴스"
    jsonl_path = news_dir / "naver_news_snapshot.jsonl"
    manifest_path = news_dir / "naver_news_snapshot_manifest.json"
    if not jsonl_path.is_file() or not manifest_path.is_file():
        return None, {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    retrieved_at = str(manifest.get("retrieved_at_utc") or "")
    queries = tuple(str(item) for item in (manifest.get("query_labels") or []) if str(item).strip())
    period = retrieved_at[:10] if retrieved_at else "미상"
    raw_count = int(manifest.get("raw_result_count") or 0)
    normalized_count = int(manifest.get("normalized_row_count") or len(records))
    deduped_count = int(manifest.get("deduped_count") or max(raw_count - normalized_count, 0))
    return NewsCatalog(
        records=records,
        jsonl_path=jsonl_path,
        manifest_path=manifest_path,
        query_label=" | ".join(queries) or "네이버 뉴스",
        query_metadata_verified=bool(manifest.get("query_metadata_verified", False)),
        period_start=period,
        period_end=period,
        raw_row_count=raw_count,
        normalized_row_count=normalized_count,
        excluded_counts={},
        dedupe_counts={"url_exact": deduped_count},
        source_name="naver_news",
        retrieved_at_utc=retrieved_at or None,
        queries=queries,
        topic_match_count=int(manifest.get("topic_match_count") or sum(1 for record in records if record.get("topic_match"))),
    ), {"naver_news_snapshot": jsonl_path, "naver_news_snapshot_manifest": manifest_path}


def load_news_catalogs() -> tuple[list[NewsCatalog], dict[str, Path]]:
    """정적 뉴스 snapshot을 모두 로드한다(API 호출·예약 갱신 없음)."""
    catalogs: list[NewsCatalog] = []
    paths: dict[str, Path] = {}
    for loader in (load_bigkinds_news, load_naver_news_snapshot):
        catalog, loaded_paths = loader()
        if catalog is not None:
            catalogs.append(catalog)
            paths.update(loaded_paths)
    return catalogs, paths


def news_dong_aliases(name: str | None) -> set[str]:
    """공식 행정동명과 통용명(예: 잠실2동↔잠실동)을 함께 비교한다."""
    name = nfc(name)
    aliases = {name} if name else set()
    if re.search(r"\d+동$", name):
        aliases.add(re.sub(r"\d+동$", "동", name))
    if name.endswith("본동"):
        aliases.add(name[:-2] + "동")
    return aliases


def news_context_for_candidate(catalog: NewsCatalog, sigungu: str,
                               admin_dong: str | None) -> dict[str, Any]:
    """주제 한정 뉴스 snapshot 안에서 후보의 자치구·행정동에 매칭되는 기사량을 센다.

    ``topic_match``는 snapshot 자체가 이미 시설·개발·정비 주제 검색 결과이면
    거의 전량 통과한다(``topic_match_rate``로 노출). 따라서 이 수는 '주제로
    걸러낸 부분집합'이 아니라 '주제 한정 snapshot 안에서 지역이 매칭된 기사 수'다.
    """
    sigungu = nfc(sigungu)
    dong_aliases = news_dong_aliases(admin_dong)
    snapshot_total = len(catalog.records)
    topic_records = [record for record in catalog.records if record.get("topic_match", False)]
    sigungu_records = [
        record for record in topic_records
        if bool(record.get("seoul_scope", False)) and sigungu in set(record.get("sigungu_tags") or [])
    ]
    dong_records = [
        record for record in sigungu_records
        if dong_aliases and dong_aliases.intersection(set(record.get("dong_tags") or []))
    ]
    exact_dong_records = [
        record for record in sigungu_records
        if admin_dong and admin_dong in set(record.get("dong_tags") or [])
    ]
    topics = Counter(tag for record in sigungu_records for tag in record.get("topic_tags", []))
    return {
        "snapshot_total": snapshot_total,
        "topic_count": len(topic_records),
        "topic_match_rate": round(len(topic_records) / snapshot_total, 3) if snapshot_total else None,
        "sigungu_count": len(sigungu_records),
        "dong_count": len(dong_records),
        "dong_exact_count": len(exact_dong_records),
        "dong_match_basis": "exact_official_name" if len(exact_dong_records) else "common_name_alias_or_none",
        "sigungu": sigungu,
        "dong": admin_dong,
        "dong_aliases": sorted(dong_aliases),
        "topic_counts": dict(topics.most_common()),
    }


def _points_from_rows(rows: Iterable[dict[str, str]], *, require_geocode: bool = False) -> list[tuple[Point, dict[str, str]]]:
    out: list[tuple[Point, dict[str, str]]] = []
    for row in rows:
        if require_geocode and row.get("geocode_신뢰도") not in ("high", "medium"):
            continue
        try:
            out.append((Point(float(row["X_5181"]), float(row["Y_5181"])), row))
        except (TypeError, ValueError, KeyError):
            pass
    return out


def load_radius_points() -> tuple[list[tuple[Point, dict[str, str]]], list[tuple[Point, dict[str, str]]], list[tuple[Point, dict[str, str]]], dict[str, Path]]:
    stations_path = find_file(ROOT / "data/도시철도역사", "역사정보_서울")
    bus_path = find_file(ROOT / "data/버스정류장", "버스정류소_서울")
    apt_path = find_file(ROOT / "data/공동주택", "아파트단지_서울")
    stations = _points_from_rows(read_csv(stations_path))
    buses = _points_from_rows(read_csv(bus_path))
    apts = _points_from_rows(read_csv(apt_path), require_geocode=True)
    return stations, buses, apts, {
        "transit": stations_path, "bus": bus_path, "apartment": apt_path,
    }


def load_completed_poi_context(request: RecommendationRequest) -> PoiContext | None:
    """요청 영역과 정확히 일치하는 완결 Kakao POI context만 읽는다.

    루트 ``data/카카오POI/*.csv``는 후보 seed 입력이고, ``context/``는
    반경 관측 입력이다. 부분 수집·다른 영역·CSV 행수 불일치 결과는 조용히
    섞지 않고 제외한다.
    """
    context_dir = ROOT / "data/카카오POI/context"
    if not context_dir.exists():
        return None
    accepted: list[tuple[str, Path, Path, dict[str, Any]]] = []
    for manifest_path in context_dir.glob("*_manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        selection = manifest.get("selection", {})
        coverage = manifest.get("coverage", {})
        if (
            manifest.get("run_status") != "complete"
            or coverage.get("status") != "complete_requested_queries"
            or selection.get("sido") != request.sido
            or selection.get("sigungu") != request.sigungu
            or selection.get("requested_dong") != request.dong
        ):
            continue
        output_csv = Path(str(manifest.get("output_csv", "")))
        csv_path = output_csv if output_csv.is_absolute() else ROOT / output_csv
        if not csv_path.is_file() or csv_path.parent != context_dir:
            continue
        accepted.append((str(manifest.get("retrieved_at_utc") or ""), csv_path, manifest_path, manifest))
    if not accepted:
        return None
    _, csv_path, manifest_path, manifest = max(accepted, key=lambda item: item[0])
    rows = read_csv(csv_path)
    if len(rows) != manifest.get("deduplicated_rows"):
        return None
    points: list[tuple[Point, dict[str, str]]] = []
    for row in rows:
        if not row.get("poi_id"):
            return None
        try:
            points.append((Point(float(row["x_5181"]), float(row["y_5181"])), row))
        except (TypeError, ValueError, KeyError):
            return None
    categories = tuple(sorted(str(value) for value in manifest.get("query_contract", {}).get("categories", []) if value))
    return PoiContext(
        points=points,
        csv_path=csv_path,
        manifest_path=manifest_path,
        retrieved_at=str(manifest.get("retrieved_at_utc") or "미상"),
        categories=categories,
    )


def spatial_near(points: list[tuple[Point, dict[str, str]]], center: Point, radius: float) -> list[tuple[float, dict[str, str]]]:
    nearby = [
        (round(point.distance(center), 1), row)
        for point, row in points
        if point.distance(center) <= radius
    ]
    # 거리 동률 시 dict를 비교하려고 하지 않도록 거리만 정렬 키로 사용한다.
    return sorted(nearby, key=lambda item: item[0])


def choose_background(
    host: LayerRecord | None, dong: LayerRecord | None, trdar_rows: dict[str, dict[str, str]],
    dong_rows: dict[str, dict[str, str]],
    trdar_area: dict[str, float],
    dong_area: dict[str, float],
) -> tuple[str | None, str | None, dict[str, str] | None, float | None]:
    if host and host.code in trdar_rows:
        return "상권", host.code, trdar_rows[host.code], trdar_area.get(host.code)
    if dong and dong.code in dong_rows:
        return "행정동", dong.code, dong_rows[dong.code], dong_area.get(dong.code)
    return None, None, None, None


def build_candidate(
    request: RecommendationRequest, conditions: dict[str, Any], seed: dict[str, Any],
    trdar_layer: ShapeLayer, hinterland_layer: ShapeLayer, dong_layer: ShapeLayer,
    sigungu_by_prefix: dict[str, str], target_sigungu: str,
    trdar_rows: dict[str, dict[str, str]], sales_trdar: dict[str, dict[str, str]], flow_trdar: dict[str, dict[str, str]], change_trdar: dict[str, dict[str, str]],
    store_dong: dict[str, dict[str, str]], sales_dong: dict[str, dict[str, str]], flow_dong: dict[str, dict[str, str]], change_dong: dict[str, dict[str, str]],
    trdar_area: dict[str, float], dong_area: dict[str, float],
    eh_trdar: dict[str, dict[str, float | None]], eh_trdar_dist: dict[str, list[float]], eh_dong: dict[str, dict[str, float | None]], eh_dong_dist: dict[str, list[float]],
    stations: list[tuple[Point, dict[str, str]]], buses: list[tuple[Point, dict[str, str]]], apts: list[tuple[Point, dict[str, str]]],
    poi_context: PoiContext | None, include_poi_context: bool,
    naver_industry_attention: dict[str, Any] | None,
    news_catalogs: list[NewsCatalog],
    source_paths: dict[str, Path], seoul_flow_density: list[float], seoul_sales_pp: list[float],
    crosswalk: dict[str, dict[str, str]], rone_areas: dict[str, dict[str, str]], rone_seoul: dict[str, str], rone_path: Path | None,
    rone_vac_areas: dict[str, dict[str, str]], rone_vac_seoul: dict[str, str], rone_vac_path: Path | None,
    pop: "population.PopulationData | None" = None, flow_dong_total_seoul: list[float] | None = None,
    plan: "urban_plan.PlanData | None" = None,
) -> dict[str, Any]:
    point = seed["pt"]
    synthetic = seed["kind"] == "생성지점"
    is_building = seed["kind"] == "상가건물"
    dong_hits = dong_layer.covering(point)
    dong_rec = choose_most_specific(dong_hits)
    host_hits = trdar_layer.covering(point)
    host = choose_most_specific(host_hits)
    host_relation = "포함" if host else None
    host_distance = 0.0 if host else None
    if host is None:
        _, nearest, distance = trdar_layer.nearest(point)
        nearest_sigungu = str(nearest.raw.get("SIGNGU_CD_", "")).strip()
        if distance <= DEFAULT_HOST_MAX_M and nearest_sigungu == target_sigungu:
            host, host_relation, host_distance = nearest, "최근접", round(distance, 1)

    hinterland = [rec for _, rec in hinterland_layer.covering(point)]
    nearby_stations = spatial_near(stations, point, DEFAULT_RADIUS_M)
    nearby_buses = spatial_near(buses, point, DEFAULT_BUS_RADIUS_M)
    nearby_apts = [item for item in spatial_near(apts, point, DEFAULT_RADIUS_M) if item[1].get("단지코드") != seed["id"]]
    nearby_poi = {
        radius: spatial_near(poi_context.points, point, radius) if poi_context else []
        for radius in POI_CONTEXT_RADIUS_M
    }
    poi_counts = {
        radius: Counter(row.get("category_group_code", "") for _, row in nearby_poi[radius])
        for radius in POI_CONTEXT_RADIUS_M
    }
    households = sum(as_int(num(row, "세대수")) or 0 for _, row in nearby_apts)
    apartment_count = len(nearby_apts)
    if seed["kind"] == "아파트단지" and seed.get("households"):
        households += int(seed["households"])
        apartment_count += 1

    f_scope = f_key = f_row = f_area = None
    u_scope = u_key = u_row = u_area = None
    # 상권을 우선하고, 상권 업종 행이 없으면 행정동으로 내린다.
    if host and host.code in trdar_rows:
        u_scope, u_key, u_row, u_area = "상권", host.code, trdar_rows[host.code], trdar_area.get(host.code)
    elif dong_rec and dong_rec.code in store_dong:
        u_scope, u_key, u_row, u_area = "행정동", dong_rec.code, store_dong[dong_rec.code], dong_area.get(dong_rec.code)
    elif host:
        u_scope, u_key, u_row, u_area = "상권", host.code, None, trdar_area.get(host.code)
    else:
        u_scope = u_key = u_row = u_area = None

    if host and host.code in flow_trdar:
        f_scope, f_key, f_row, f_area = "상권", host.code, flow_trdar[host.code], trdar_area.get(host.code)
    elif dong_rec and dong_rec.code in flow_dong:
        f_scope, f_key, f_row, f_area = "행정동", dong_rec.code, flow_dong[dong_rec.code], dong_area.get(dong_rec.code)
    else:
        f_scope = f_key = f_row = f_area = None
    if host and host.code in trdar_rows:
        u_scope, u_key, u_row, u_area = "상권", host.code, trdar_rows[host.code], trdar_area.get(host.code)
    elif dong_rec and dong_rec.code in store_dong:
        u_scope, u_key, u_row, u_area = "행정동", dong_rec.code, store_dong[dong_rec.code], dong_area.get(dong_rec.code)
    else:
        u_scope = u_key = u_row = u_area = None

    flow_total = num(f_row, "총_유동인구_수")
    flow_density = flow_total / f_area if flow_total is not None and f_area else None
    store_count = num(u_row, "전체_점포_수")
    franchise_count = num(u_row, "프랜차이즈_점포_수")
    sales_row = (sales_trdar.get(u_key) if u_scope == "상권" else sales_dong.get(u_key)) if u_key else None
    sales = num(sales_row, "당월_매출_금액")  # 상권분석 추정매출은 분기 합계(컬럼명 '당월'은 레거시)
    sales_per_store = sales / store_count if sales is not None and store_count and store_count > 0 else None
    # 값 수준 QA(2026-09-02, qa_value_levels.py): 추정매출 극소값(≈₩10만/분기 미만, 상권×업종 30셀)은
    # 카드매출 표본 부족 아티팩트 → 신호로 쓰지 않고 결측 취급.
    sales_unreliable = sales_per_store is not None and sales_per_store < 300_000
    if sales_unreliable:
        sales_per_store = None
    franchise_ratio = franchise_count / store_count if franchise_count is not None and store_count else None
    greenfield = store_count is None or store_count == 0

    if host and host.code in change_trdar:
        change_row, change_scope = change_trdar[host.code], "상권"
    elif dong_rec and dong_rec.code in change_dong:
        change_row, change_scope = change_dong[dong_rec.code], "행정동"
    else:
        change_row, change_scope = None, None
    label = change_row.get("상권_변화_지표") if change_row else None
    if host and host.code in eh_trdar:
        eh_scope, eh_rec, eh_dist = "상권", eh_trdar[host.code], eh_trdar_dist
    elif dong_rec and dong_rec.code in eh_dong:
        eh_scope, eh_rec, eh_dist = "행정동", eh_dong[dong_rec.code], eh_dong_dist
    else:
        eh_scope = eh_rec = eh_dist = None
    grade, risk, eh_inputs = entry_health(eh_scope, eh_rec, eh_dist, label) if eh_scope else ("정보없음", None, {})

    # 인구 3종(FC-03·04·05·06a·06b) — 전부 context_notes 버킷 (candidate-selection-spec.md §4-2).
    pop_ctx = population.context_for_candidate(
        pop,
        host_code=host.code if host else None,
        dong_code=dong_rec.code if dong_rec else None,
        dong_sigungu=sigungu_by_prefix.get(dong_rec.code[:5]) if dong_rec else None,
        target_sigungu=target_sigungu,
        flow_dong_total=num(flow_dong.get(dong_rec.code), "총_유동인구_수") if dong_rec else None,
        flow_dong_seoul=flow_dong_total_seoul or [],
        quarter=request.quarter,
    )

    # 도시계획·정비사업 추진단계(FC-51·52) — 전부 context_notes 버킷 (candidate-selection-spec.md §202·§428).
    plan_ctx = urban_plan.context_for_candidate(
        plan,
        host_code=host.code if host else None,
        sigungu_code=dong_rec.code[:5] if dong_rec else None,
        sigungu_name=target_sigungu,
    )

    flow_p = pct(seoul_flow_density, flow_density)
    sales_p = pct(seoul_sales_pp, sales_per_store)
    area_p = pct(sorted(trdar_area.values()), f_area) if f_area else None
    # FC 신호 등급표(regional-characteristics-profile.md §9-1, feature-evidential-value.md §9):
    #   reasons        = fit_tier 판정·정렬 반영. 범주 신호 + 약한 배경 신호만. 약한 배경 신호는 단독 추천 승격 금지.
    #   counter        = fit_tier 판정 반영. 범주 신호(FC-10 등급·FC-11 라벨) · 하드조건 미충족 · 핵심 결측.
    #   context_notes  = 판정·정렬 미반영. '신호 없음(서술만)' FC(FC-01·30 등) + '모멘텀' FC(FC-32).
    reasons: list[str] = []
    counter: list[str] = []
    context_notes: list[str] = []

    gen_ev = seed.get("gen_evidence") if synthetic else None
    if synthetic:
        context_notes.append(
            f"이 후보는 프로젝트가 격자({seed.get('grid_id')})로 생성한 좌표(GEN-PT). "
            f"실제 매물·점포가 아니며 근거는 공개 데이터 파생 맥락({seed.get('evidence_id')}). "
            f"precision=지점(생성), listing_url·address_point 없음"
        )
        if gen_ev:
            gm = gen_ev.get("metrics", {})
            brk = "·".join(
                f"{name} {gm.get(f'active_{code}_license_count_500m', 0)}"
                for code, name in SUPPORTED_INDUSTRY_NAMES.items()
                if gm.get(f"active_{code}_license_count_500m")
            )
            context_notes.append(
                f"반경 500m 영업 중 음식점 인허가 {gm.get('active_food_license_count_500m', 0)}개 "
                f"(업종별: {brk or '없음'}) — 합성 격자 좌표 기준 경쟁 규모이며 매물 수·공실·수요·성공 아님, 판정·정렬 미반영"
            )
    if is_building:
        context_notes.append(
            f"건축물대장 주용도 '{seed.get('use_group') or '미상'}' 건물 centroid 기준. "
            "실제 개별 호실·점포·공실·임대료·주차를 확인한 매물이 아니므로 건물 인근 탐색용 seed로만 사용"
        )

    # 신호 없음 — 서술만: FC-01 유동밀도, FC-30 동종 점포밀도, FC-07 역거리
    if flow_p is not None:
        caveat = " · 면적 큰 상권이라 밀도 저평가 가능" if area_p is not None and area_p >= 97 else ""
        context_notes.append(f"유동밀도 {f_scope or '미상'} 배경 서울 {flow_p}%{caveat} — 검증상 폐업/생존과 무연관, 판정·정렬 근거 아님 (FC-01)")
    if store_count is not None and u_scope:
        context_notes.append(f"{request.industry_code} 동종 점포 {as_int(store_count)}개 ({u_scope} 배경) — 경쟁 규모이며 폐업/생존과 무연관 (FC-30)")
    if nearby_stations and nearby_stations[0][0] <= 300 and seed["kind"] != "역":
        context_notes.append(f"최근접 도시철도역 {nearby_stations[0][1].get('역사명')} {nearby_stations[0][0]}m — 검증상 품질과 무연관 (FC-07 역거리)")

    # 모멘텀 (품질 아님): FC-32 프랜차이즈 비율
    if franchise_ratio is not None and store_count and store_count >= 5:
        context_notes.append(f"프랜차이즈 비율 {round(franchise_ratio * 100, 1)}% ({u_scope} 배경) — 최근 체인 확장 정도(모멘텀)이며 신규 독립점 유불리로 단정 불가 (FC-32)")

    # FC-42 업종 검색 관심도: 현재 관심도 맥락만 제공한다.
    # 원계열의 계절성 피크는 실제 관심일 수 있으므로 숨기지 않되, 계절 보정
    # 기울기나 단일 급등을 지속 성장·입지 품질로 승격하지 않는다.
    if naver_industry_attention:
        attention = naver_industry_attention
        phase = ""
        if attention.get("peak_month") == int(attention["latest_ym"][4:6]):
            phase = " · 계절성 피크월"
        elif attention.get("trough_month") == int(attention["latest_ym"][4:6]):
            phase = " · 계절성 저점월"
        surge_note = " (지속 추세 판단 보류)" if attention["surge_active"] else ""
        yoy = attention["yoy_clean_recent_mean"]
        yoy_txt = f"전년 동월 대비 {yoy}배(계절 정합)" if yoy is not None else "전년 동월 비교 불가"
        context_notes.append(
            f"{attention['industry_name']} 검색 관심도 최신 {attention['latest_ym']} rel_index {attention['latest_rel_index']}, "
            f"최근 3개월 원계열 평균 {attention['recent3_rel_mean']} "
            f"({yoy_txt}) — {attention['status']}{phase}{surge_note}. "
            f"후보 지점 검색량·매출이 아니며 fit_tier·정렬 미반영 (FC-42)"
        )

    # 약한 배경 신호 — 보조 근거, 단독 추천 승격 금지, data_confidence 하향: FC-08 배후주거, FC-31 매출규모, FC-07 bus_n
    if households >= 3000:
        reasons.append(f"반경 500m 아파트 {households}세대 — 약한 배경 신호 (FC-08)")
    if sales_p is not None and sales_p >= 70:
        reasons.append(f"{u_scope} 배경 업종 점포당매출 서울 상위 {round(100 - sales_p, 1)}% — 약한 배경 신호, 과거 실적이며 신규 성공 아님 (FC-31)")
    if len(nearby_buses) >= 8:
        reasons.append(f"반경 250m 버스정류소 {len(nearby_buses)}개 — 약한 배경 신호 (FC-07 bus_n)")
    weak_only = bool(reasons)  # 현재 검증된 강한 긍정 신호는 없음 → 긍정 근거가 있으면 전부 약한 배경 신호

    # 범주 신호 (반대근거): FC-10 등급, FC-11 라벨
    if grade in ("주의", "경계"):
        tag = "차단" if grade == "경계" else "정보"
        counter.append(f"지역 배경 진입 리스크 {grade}[{tag}] (entry_health_v1={risk}) — 반대근거 1항목")
    if label in ("HH", "HL"):
        counter.append(f"상권변화 {(change_row or {}).get('상권_변화_지표_명', label)} — 신규 진입 상대적 불리 (FC-11 범주 신호)")

    # 하드조건 미충족 / 핵심 데이터 결측
    # 매출 미제공(≠ 결측): 추정매출은 카드거래 표본이 임계치 미만인 상권×업종을 아예 추정하지 않는다
    # → "데이터 없음"이 아니라 "이 상권에서 이 업종 시장이 작을 수 있음"이라는 신호. fallback은 넣지 않는다(결정 2026-09-02).
    sales_thin_market = sales_per_store is None and not sales_unreliable and (store_count or 0) > 0 and u_scope == "상권"
    if sales_unreliable:
        counter.append(f"{request.industry_code} 추정매출 극소값(₩10만/분기 미만) — 카드 표본 1~2건, 시장 검증 불가")
    elif sales_thin_market:
        counter.append(f"{request.industry_code} 추정매출 미제공 상권 — 카드거래 표본이 추정 임계치 미만(소규모 시장 가능성), 매출 검증 불가")
    elif sales_per_store is None:
        counter.append(f"{request.industry_code} 점포당매출 근거 없음 — {u_scope or '상권·행정동'} 데이터 부족")
    elif sales_p is not None and sales_p < 15:
        counter.append(f"{u_scope} 배경 업종 점포당매출 서울 하위 {sales_p}% — 시장 매출 규모 매우 작음")

    mapping = crosswalk.get(host.code) if host else None
    rone_row = rone_areas.get(mapping.get("R_ONE_상권")) if mapping else None
    city_rent = num({"값": rone_seoul.get("value")} if rone_seoul else None, "값")
    area_rent = num(rone_row, "값") if rone_row else None
    rent_value = area_rent if area_rent is not None else city_rent
    rent_period = rone_row.get("기준_년분기_코드") if rone_row else (rone_seoul.get("quarter") if rone_seoul else DEFAULT_RENT_QUARTER)
    rent_specific = area_rent is not None and mapping is not None
    if rent_specific:
        rent_reason = f"R-ONE {mapping['R_ONE_상권']} 임대가격지수 사용(상권분석 target proxy)"
    else:
        rent_reason = "R-ONE 상권별 자동 매핑 불가 → 서울전체 임대가격지수 proxy 사용"
        counter.append("비용(임대료): R-ONE 상권별 매핑 미허용 — 서울전체 지수 proxy 사용")

    # rent_specific·vacancy_specific은 같은 mapping(host 상권 → R-ONE 상권)을 쓰므로 항상 같이
    # True/False다 — R-ONE이 임대가격지수·공실률을 같은 상권 목록으로 조사하기 때문이다.
    rone_vac_row = rone_vac_areas.get(mapping.get("R_ONE_상권")) if mapping else None
    city_vacancy = num({"값": rone_vac_seoul.get("value")} if rone_vac_seoul else None, "값")
    area_vacancy = num(rone_vac_row, "값") if rone_vac_row else None
    vacancy_value = area_vacancy if area_vacancy is not None else city_vacancy
    vacancy_period = (rone_vac_row.get("기준_년분기_코드") if rone_vac_row
                      else (rone_vac_seoul.get("quarter") if rone_vac_seoul else None)) or DEFAULT_RENT_QUARTER
    vacancy_specific = area_vacancy is not None and mapping is not None
    if vacancy_value is None:
        vacancy_reason = "R-ONE 공실률 자료 없음"
    elif vacancy_specific:
        vacancy_reason = f"R-ONE {mapping['R_ONE_상권']} 공실률 사용(상권분석 target proxy)"
    else:
        vacancy_reason = "R-ONE 상권별 자동 매핑 불가 → 서울전체 공실률 proxy 사용"

    missing: list[dict[str, str]] = []
    if sales_per_store is None:
        if sales_unreliable:
            reason = f"{request.industry_code} 추정매출 극소값(₩10만/분기 미만) — 카드 표본 1~2건 아티팩트, 결측 취급"
        elif sales_thin_market:
            reason = (f"{request.industry_code} 추정매출 미제공 — 이 상권의 이 업종 카드거래가 추정 임계치 미만. "
                      f"소규모 시장 신호이므로 다른 grain 매출로 대체하지 않음(결정 2026-09-02, value-level-qa.md)")
        else:
            reason = f"{request.industry_code} {u_scope or '상권·행정동'} 점포·추정매출 데이터 없음"
        missing.append({"feature": "FC-31", "reason": reason})
    if not rent_specific:
        missing.append({"feature": "FC-20", "reason": "host 상권이 R-ONE crosswalk join_eligible 대상이 아니어서 상권별 임대료 자동 결합 불가"})
    if vacancy_value is None:
        missing.append({"feature": "FC-21", "reason": "R-ONE 공실률 자료 없음"})
    elif not vacancy_specific:
        missing.append({"feature": "FC-21", "reason": "host 상권이 R-ONE crosswalk join_eligible 대상이 아니어서 상권별 공실률 자동 결합 불가 — 서울전체 proxy 사용"})
    if include_poi_context and poi_context is None:
        missing.append({"feature": "observed_poi_context", "reason": "요청 영역과 일치하는 complete_requested_queries Kakao rect snapshot 없음"})
    missing.extend({"feature": f"unsupported.{i + 1}", "reason": reason} for i, reason in enumerate(conditions["unsupported_conditions"]))
    if host is None:
        missing.append({"feature": "host_commercial_area", "reason": "지점이 상권 내부·300m 최근접 조건 밖 — 행정동 배경값 사용"})

    blocking = any("불리" in item or "리스크 경계" in item for item in counter)
    hard_fail = bool(conditions["unsupported_conditions"]) or sales_per_store is None
    if blocking and len(counter) >= 3:
        tier = "주의"
    elif hard_fail or blocking or len(reasons) < 2:
        # 약한 배경 신호는 단독 판정 금지 — 최소 2개 이상 겹쳐야 추천 (sample_jamsil_coffee._tier와 동일 기준).
        tier = "조건부 검토"
    else:
        tier = "추천"

    # 합성 격자 좌표는 실제 임대 가능 호실을 확인할 수 없다 → '추천' 상한을 '조건부 검토'로 캡한다.
    if synthetic and tier == "추천":
        tier = "조건부 검토"
        counter.append("합성 격자 좌표 — 실제 임대 가능 상가·호실 미확인이므로 추천 상한은 조건부")
    if is_building:
        # 상가건물은 footprint가 실재 공공데이터 기반(synthetic_anchor=false)이라
        # 격자 생성지점과 달리 tier 캡을 걸지 않는다 — 대신 "매물 아님"을 항상
        # context_notes[0]에 강제 고지한다(등급·정렬 미반영, 루트 scripts/recommendation_pipeline.py
        # 와 정책 통일, PR #14 리뷰 반영).
        context_notes.insert(0, "실재 상업용 건물(건축물대장 기반)이며 임대 가능 특정 호실·공실 확인 안 됨(매물 아님)")

    nearby_anchors = []
    for distance, row in nearby_stations[:4]:
        if str(row.get("역번호")) == str(seed["id"]):
            continue
        nearby_anchors.append({"name": row.get("역사명", ""), "type": "역", "distance_m": distance,
                               "line": row.get("노선명"), "transfer": row.get("환승역") == "Y", "source_type": "observed"})
    for distance, row in nearby_apts[:4]:
        nearby_anchors.append({"name": row.get("단지명", ""), "type": "아파트단지", "distance_m": distance,
                               "households": as_int(num(row, "세대수")), "source_type": "observed"})

    anchor_type = seed["kind"]
    candidate_type = {"아파트단지": "아파트단지_인근", "역": "역_인근", "카카오POI": "카카오POI_인근",
                      "생성지점": "생성지점_격자", "상가건물": "상가건물_인근"}[anchor_type]
    candidate_id = str(seed["id"]) if synthetic else \
        f"{ {'아파트단지': 'APT', '역': 'STN', '카카오POI': 'POI', '상가건물': 'BLDG'}[anchor_type] }-{seed['id']}"
    proxy_scope = f_scope in ("상권", "행정동")
    source_list = sorted({relative_path(p) for p in source_paths.values()} | seed["source_paths"])
    if rone_path:
        source_list.append(relative_path(rone_path))
    if rone_vac_path:
        source_list.append(relative_path(rone_vac_path))
    if poi_context:
        source_list.extend([relative_path(poi_context.csv_path), relative_path(poi_context.manifest_path)])
    if synthetic and gen_ev:
        source_list.append("data/인허가/음식점_인허가_서울.csv")
        if seed.get("gen_evidence_path"):
            source_list.append(seed["gen_evidence_path"])
    source_list = sorted(set(source_list))

    flow_source = source_paths["flow_dong"] if f_scope == "행정동" else source_paths["flow"]
    store_source = source_paths["store_dong"] if u_scope == "행정동" else source_paths["store"]
    sales_source = source_paths["sales_dong"] if u_scope == "행정동" else source_paths["sales"]
    change_source = source_paths["change_dong"] if change_scope == "행정동" else source_paths["change"]

    ev: list[dict[str, Any]] = []
    ev.append(evidence("유동밀도", round(flow_density, 4) if flow_density is not None else None, "명/㎡·분기", "derived", "seoul_quantile", request.quarter,
                       f_scope or "상권", True, relative_path(flow_source), relative_path(flow_source), ["flow_per_area_normalize"],
                       f"{f_scope or '미상'} 배경 유동밀도 서울 {flow_p or '미검증'}%", "지점 고유값 아님(배경값) · 검증상 폐업/생존 무연관이라 fit_tier 판정·정렬 근거 아님(FC-01) · 총량 순위 근거 금지",
                       percentile=flow_p, missing_reason="상권·행정동 유동인구 결측", proxy_note=f"{f_scope or '미상'} 배경값"))
    ev.append(evidence("반경500m_도시철도역수", len(nearby_stations), "개소", "derived", "none", "2026-08", "지점", False,
                       relative_path(source_paths["transit"]), "스냅샷", ["nearest_station_distance", "transit_count_by_radius"],
                       f"지점 반경 500m 내 도시철도역 {len(nearby_stations)}개", "개통 스냅샷이며 출구 좌표·배차 정보 없음"))
    ev.append(evidence("반경250m_버스정류소수", len(nearby_buses), "개소", "derived", "none", "2026-08", "지점", False,
                       relative_path(source_paths["bus"]), "스냅샷", ["nearest_bus_stop_distance", "transit_count_by_radius"],
                       f"지점 반경 250m 내 버스정류소 {len(nearby_buses)}개", "노선·배차 없음"))
    ev.append(evidence("반경500m_아파트_세대수", households, "세대", "derived", "none", "2026-08", "지점", False,
                       relative_path(source_paths["apartment"]), "스냅샷", ["households_by_radius", "apt_name_geocode_vworld"],
                       f"지점 반경 500m 내 아파트 {apartment_count}단지 {households}세대", "K-apt 의무관리 위주이며 소형 빌라·연립 누락, 좌표 89.7%"))
    if is_building:
        building_source = seed["source_path"]
        building_period = seed.get("snapshot") or "20260809"
        ev.append(evidence(
            "건물_용도군", seed.get("use_group") or None, "건축물대장 용도군", "derived", "none",
            building_period, "지점", False, building_source, building_source,
            ["building_use_group_from_primary_use"],
            f"건물 centroid의 건축물대장 주용도군: {seed.get('use_group') or '미상'}",
            "건물 주용도 파생값이며 층별 용도·전유부 호실·실제 점포·공실을 뜻하지 않음",
            missing_reason="건축물대장 주용도군 결측" if not seed.get("use_group") else None,
        ))
        ev.append(evidence(
            "건물_연면적", seed.get("gross_floor_area_m2"), "㎡", "observed", "none",
            building_period, "지점", False, building_source, building_source,
            ["building_gross_floor_area_m2"],
            f"건축물대장 연면적 {seed.get('gross_floor_area_m2') or '미상'}㎡",
            "건물 전체 연면적이며 임대 가능한 상가 면적·호실 면적이 아님",
            missing_reason="연면적 결측" if seed.get("gross_floor_area_m2") is None else None,
        ))
    if poi_context:
        snapshot_date = poi_context.retrieved_at[:10]
        for radius in POI_CONTEXT_RADIUS_M:
            for category in poi_context.categories:
                category_label = KAKAO_CATEGORY_LABELS.get(category, category)
                count = poi_counts[radius][category]
                ev.append(evidence(
                    f"반경{radius}m_관측_{category}_{category_label}수", count, "개소", "derived", "none", snapshot_date, "지점", False,
                    relative_path(poi_context.csv_path), relative_path(poi_context.manifest_path),
                    ["kakao_rect_grid", "point_in_polygon_area_assign", "kakao_place_id_dedup", "poi_radius_count"],
                    f"완결 Kakao {category} 관측 스냅샷에서 지점 반경 {radius}m {category_label} {count}개소",
                    f"{category} 공급 카테고리만의 관측 스냅샷이다. 업종 세분류·전체 상가·공실·매물·수요·성공 outcome을 뜻하지 않으며 fit_tier 판정·정렬에 사용하지 않는다.",
                ))
    ev.append(evidence(f"{request.industry_code}_점포수", as_int(store_count), "개소", "observed", "none", request.quarter,
                       u_scope or "행정동", proxy_scope, relative_path(store_source), relative_path(store_source), ["store_count_schema_map"],
                       f"{u_scope or '미상'} 배경 업종 점포수 {as_int(store_count)}", "업종 점포수는 경쟁 규모이지 성공확률이 아님",
                       missing_reason="상권·행정동 업종 점포 데이터 결측", proxy_note=f"{u_scope or '미상'} 배경값" if proxy_scope else None))
    ev.append(evidence(f"{request.industry_code}_점포당매출", round(sales_per_store, 2) if sales_per_store is not None else None, "원/점포·분기", "derived", "seoul_quantile", request.quarter,
                       u_scope or "행정동", proxy_scope, relative_path(sales_source), relative_path(sales_source), ["sales_count_column_fix"],
                       f"{u_scope or '미상'} 배경 점포당매출(분기) 서울 {sales_p or '미검증'}%", "매출 상위 = 신규 성공 아님 · 추정매출은 분기 합계",
                       percentile=sales_p,
                       missing_reason=("추정매출 극소값 — 카드 표본 1~2건" if sales_unreliable
                                       else "추정매출 미제공 — 카드거래 표본 임계치 미만(소규모 시장 신호), fallback 미적용" if sales_thin_market
                                       else "추정매출 데이터 없음" if sales_per_store is None else None),
                       proxy_note=f"{u_scope or '미상'} 배경값" if proxy_scope else None))
    ev.append(evidence("상권_변화_지표", label, "코드", "observed", "none", request.quarter, change_scope or "행정동", True,
                       relative_path(change_source), relative_path(change_source), [],
                       f"{change_scope or '미상'} 배경 변화 라벨 {label or '없음'}", "업종 없는 지역 배경 라벨이며 연속 gap 주력 피처 금지",
                       missing_reason="상권·행정동 변화지표 결측", proxy_note=f"{change_scope or '미상'} 배경값"))
    ev.append(evidence("R-ONE_임대가격지수", rent_value, "지수", "observed" if rent_value is not None else "derived", "none", rent_period,
                       "권역" if rent_specific else "서울시", True, relative_path(rone_path) if rone_path else "data/임대료/", relative_path(rone_path) if rone_path else "미검증",
                       ["rent_unpivot"], rent_reason, "R-ONE 조사권역·서울 지수 proxy이며 개별 매물 월세·공실이 아님",
                       missing_reason="R-ONE 임대료 자료 없음" if rent_value is None else None,
                       proxy_note="R-ONE 상권↔서울 상권분석 명칭 proxy" if rent_specific else "서울전체 지수 proxy"))
    ev.append(evidence("R-ONE_공실률", round(vacancy_value, 2) if vacancy_value is not None else None, "%",
                       "observed" if vacancy_value is not None else "derived", "none", vacancy_period,
                       "권역" if vacancy_specific else "서울시", True,
                       relative_path(rone_vac_path) if rone_vac_path else "data/임대료/",
                       relative_path(rone_vac_path) if rone_vac_path else "미검증",
                       ["rone_vacancy_api_ingest"], vacancy_reason,
                       "R-ONE 조사권역·서울 공실률 proxy이며 특정 주소의 현재 공실이 아님(비용/공급위험 배경 신호, 스코어링·정렬 미반영)",
                       missing_reason="R-ONE 공실률 자료 없음" if vacancy_value is None else None,
                       proxy_note="R-ONE 상권↔서울 상권분석 명칭 proxy" if vacancy_specific else "서울전체 proxy"))
    if naver_industry_attention:
        attention = naver_industry_attention
        ev.append(evidence(
            "FC-42_업종_현재검색관심도", attention["recent3_rel_mean"], "rel_index(최근3개월 원계열 평균)", "derived", "none",
            attention["latest_ym"], "서울시", True, relative_path(source_paths["naver_trend"]),
            relative_path(source_paths["naver_trend"]),
            ["rel_index_by_anchor", "recent_3m_mean_no_seasonal_adjustment", "yoy_clean_recent_mean", "seasonal_phase_label"],
            f"{attention['industry_name']} 최신월 {attention['latest_rel_index']}, 최근 3개월 평균 {attention['recent3_rel_mean']} "
            f"(전년 동월 대비 {attention['yoy_clean_recent_mean']}배·계절 정합, 상태={attention['status']})",
            "네이버 데이터랩 상대지수(절대 검색량 아님)이며 서울시 업종 전체 관심도다. "
            "계절성 피크는 현재 관심 맥락으로만 표시하고 계절 보정 기울기·단일 급등을 지속 성장이나 입지 품질로 해석하지 않으며 fit_tier·정렬에 사용하지 않는다.",
            proxy_note="후보 지점이 아닌 업종 전체 서울시 검색 관심도 proxy",
        ))

    # 격자 합성 좌표: gridpoint_evidence.jsonl의 반경 인허가 metrics를 후보 evidence로 연결한다.
    # 경쟁 규모 맥락(신호 없음, §9-1)이므로 fit_tier·정렬 미반영 — interpretation·limitation에 명시.
    if synthetic and gen_ev:
        gm = gen_ev.get("metrics", {})
        gd = gen_ev.get("metric_definitions", {})
        gen_period = gen_ev.get("generated_at", "2026-09")
        lic_path = "data/인허가/음식점_인허가_서울.csv"
        gen_src = seed.get("gen_evidence_path") or lic_path
        for key in (f"active_{request.industry_code}_license_count_500m", "active_food_license_count_500m"):
            if key in gm:
                ev.append(evidence(
                    key, gm[key], "개소", "derived", "none", gen_period, "지점", False,
                    lic_path, gen_src, ["radius_license_count_by_industry"],
                    gd.get(key, key),
                    "합성 격자 좌표 반경 500m 집계 — 영업 중 인허가 수(경쟁 규모)이며 매물 수·공실·수요·성공 아님. fit_tier·정렬 미반영",
                    proxy_note="합성 격자 좌표 기준 반경 집계",
                ))

    admin_name = dong_rec.name if dong_rec else None
    admin_code = dong_rec.code if dong_rec else None
    for news_catalog in news_catalogs:
        news_context = news_context_for_candidate(news_catalog, target_sigungu, admin_name or request.dong)
        source_label = "빅카인즈" if news_catalog.source_name == "bigkinds" else "네이버 뉴스"
        metric_prefix = "FC-51_뉴스" if news_catalog.source_name == "bigkinds" else "FC-51_네이버뉴스"
        news_period = (
            f"{news_catalog.period_start}~{news_catalog.period_end}"
            if news_catalog.source_name == "bigkinds"
            else f"snapshot@{news_catalog.retrieved_at_utc or news_catalog.period_end}"
        )
        news_source = relative_path(news_catalog.jsonl_path)
        news_manifest = relative_path(news_catalog.manifest_path)
        normalizations = (
            ["bigkinds_xlsx_export_parse", "news_exclusion_filter", "news_id_url_dedup"]
            if news_catalog.source_name == "bigkinds"
            else ["naver_news_api_snapshot", "news_url_exact_dedup"]
        ) + ["news_region_topic_match"]
        if admin_name:
            normalizations.append("dong_common_name_alias")
        topic_rate = news_context.get("topic_match_rate")
        topic_rate_txt = f"주제적합률 {round(topic_rate * 100)}%" if topic_rate is not None else "주제적합률 미상"
        topic_brief = ", ".join(f"{tag} {cnt}" for tag, cnt in list((news_context.get("topic_counts") or {}).items())[:4]) or "주제태그 없음"
        theme_note = (
            "snapshot이 이미 시설·개발·정비 주제 검색 결과라 topic 필터는 거의 전량 통과 — 이 수는 주제로 걸러낸 부분집합이 아니라 주제 한정 snapshot 안에서 지역 매칭된 기사 수"
            if (topic_rate or 0) >= 0.95
            else "topic 필터 적용 후 지역 매칭"
        )
        context_notes.append(
            f"{source_label} snapshot(시설·개발·정비 주제, {topic_rate_txt}) 중 {target_sigungu} 지역 매칭 {news_context['sigungu_count']}건"
            + (f", {admin_name} {news_context['dong_count']}건 [공식명 exact {news_context['dong_exact_count']}건, 통용명 포함 {news_context['dong_count']}건]" if admin_name else "")
            + f" · 주제태그 {topic_brief}"
            + f" ({news_period}, 검색어={news_catalog.query_label}, 검색 메타데이터={'확인' if news_catalog.query_metadata_verified else '파일명/CLI 추정'}) — {theme_note}. 보도량은 실제 사업 확정·추진단계·상권 성공이 아니며 공식 도시계획사업의 보조 맥락, fit_tier·정렬 미반영 (FC-51-news)"
        )
        region_basis = "제목·위치 지역명 매칭" if news_catalog.source_name == "bigkinds" else "제목 지역명 매칭"
        ev.append(evidence(
            f"{metric_prefix}_자치구기사량", news_context["sigungu_count"], "건", "derived", "none", news_period,
            "자치구", True, news_source, news_manifest, normalizations,
            f"{source_label} 시설·개발·정비 주제 snapshot({news_context.get('snapshot_total')}건, {topic_rate_txt}) 중 {target_sigungu} 매칭 {news_context['sigungu_count']}건({region_basis}; 주제태그 {topic_brief})",
            f"{source_label} snapshot은 시설·개발·정비 주제 검색 결과라 topic 필터가 거의 전량 통과({topic_rate_txt})한다 — 이 수는 주제로 걸러낸 부분집합이 아니라 주제 한정 snapshot 안에서 지역 매칭된 기사 수다. 보도량은 실제 사업 확정·추진단계·상권 성공이 아니며 서울도시계획사업·정비사업 데이터의 보조 맥락이다. fit_tier·정렬 미반영",
            proxy_note="후보 지점이 아닌 자치구 보도량 proxy",
        ))
        if admin_name:
            dong_limit = (
                "행정동명 언급은 사업구역의 정확한 위치·확정 상태가 아니며 통용명 alias는 여러 공식 행정동에 함께 잡힐 수 있다. "
                "보도량은 실제 수요·상권 성공을 의미하지 않는다. fit_tier·정렬 미반영"
            )
            if news_catalog.source_name == "bigkinds":
                dong_limit += " · 빅카인즈 위치 필드는 다중 지명 추출이라 기사 주제와 무관한 곁다리 행정동이 포함될 수 있어 제목 언급이 더 강한 신호"
            ev.append(evidence(
                f"{metric_prefix}_행정동기사량", news_context["dong_count"], "건", "derived", "none", news_period,
                "행정동", True, news_source, news_manifest, normalizations,
                f"{source_label} 시설·개발·정비 주제 snapshot 중 {admin_name} 매칭 {news_context['dong_count']}건(공식명 exact {news_context['dong_exact_count']}건, 통용명 alias 포함)",
                dong_limit,
                proxy_note="후보 지점이 아닌 행정동 보도량 proxy",
            ))
    anchor = {
        "name": seed["name"], "type": ("생성지점" if synthetic else anchor_type), "id": str(seed["id"]),
        "households": seed.get("households"),
        "line": "·".join(sorted(seed["lines"])) if seed.get("lines") else seed.get("line"),
    }
    if is_building:
        anchor.update({
            "lot_address": seed.get("lot_address") or None,
            "use_group": seed.get("use_group") or None,
            "gross_floor_area_m2": seed.get("gross_floor_area_m2"),
            "building_area_m2": seed.get("building_area_m2"),
            "floors_above": seed.get("floors_above"),
            "floors_below": seed.get("floors_below"),
            "building_age_years": seed.get("building_age_years"),
            "area_join_type": seed.get("area_join_type") or "미결합",
            "has_confirmed_commercial_floor": seed.get("has_confirmed_commercial_floor"),
        })
    building_address = None
    if is_building and (seed.get("road_address") or seed.get("lot_address")):
        building_address = {
            "value": seed.get("road_address") or seed.get("lot_address"),
            "source": "도로명주소(Tier2)" if seed.get("road_address") else "지번주소(Tier1)",
            "building_name": seed.get("building_name"),
            "limitation": "건물 주소이며 임대 가능 특정 호실 주소가 아님",
        }
    location = {
        "sido": "서울특별시", "sigungu": target_sigungu, "admin_dong": admin_name,
        "anchor": anchor,
        "place_name": (f"{seed['id']} (격자 생성 좌표 · 실제 매물·점포 아님)" if synthetic
                       else f"{seed['name']} 건물 인근" if is_building else f"{seed['name']} 인근"),
        "point": {"x": round(point.x, 2), "y": round(point.y, 2), "crs": "EPSG:5181"},
        "precision": "지점(생성)" if synthetic else "지점",
        "host_commercial_area": ({"code": host.code, "name": host.name, "relation": host_relation, "distance_m": host_distance} if host else None),
        "overlapping_units": {"commercial_area": [host.code] if host else [], "hinterland": [rec.code for rec in hinterland], "admin_dong": [admin_code] if admin_code else [], "sigungu": target_sigungu},
        "nearby_anchors": nearby_anchors, "address_point": None, "building_address": building_address,
    }
    confidence = "low" if sales_per_store is None or host is None else "medium" if proxy_scope or not rent_specific else "high"
    confidence_reasons = ["지점 반경 지표는 직접 계산"]
    if is_building:
        confidence_reasons.append("건물 centroid는 실제 임대 가능 호실·점포 위치가 아니며 주용도 기반 seed")
        if not seed.get("has_confirmed_commercial_floor"):
            confidence_reasons.append("Tier2 층별용도 미확인 — 표제부 주용도·연면적만으로 선정(상업 공간 실사용 미확인)")
    if weak_only:
        confidence_reasons.append("긍정 근거가 전부 '약한 배경 신호'(FC-08·31·07) — 검증된 품질 신호 아님")
    if proxy_scope:
        confidence_reasons.append(f"{f_scope} 배경값 사용(grain_is_proxy)")
    if not rent_specific:
        confidence_reasons.append("R-ONE 상권별 자동 매핑 미허용")
    if conditions["unsupported_conditions"]:
        confidence_reasons.append("특별조건을 매물 데이터로 검증하지 못함")
    # FC-06a/06b 상권 crosswalk 대리 시 data_confidence 1단계 하향 (candidate-selection-spec.md §4-2).
    # 표기 등급만 한 단계 내리고(high→medium, medium→low; low 유지) 정렬에는 반영하지
    # 않는다 — 인구 근거는 순위 불변(F36)이라 하향 전 등급을 정렬 키로 보존한다.
    sort_confidence = confidence
    if pop_ctx.confidence_downgrade:
        confidence_reasons.extend(pop_ctx.confidence_reasons)
        _conf_order = ("high", "medium", "low")
        confidence = _conf_order[min(_conf_order.index(confidence) + 1, len(_conf_order) - 1)]
    context_notes.extend(pop_ctx.context_notes)
    missing.extend(pop_ctx.missing)
    context_notes.extend(plan_ctx.context_notes)
    missing.extend(plan_ctx.missing)

    normalizations = [
        "encoding_detect", "eng_header_rename", "store_count_schema_map", "sales_count_column_fix",
        "year_file_quarter_assignment", "point_in_polygon_area_assign", "coord_reproject_4326_to_5181",
        "coord_reproject_5186_to_5181", "nearest_station_distance", "nearest_bus_stop_distance",
        "transit_count_by_radius", "households_by_radius", "flow_per_area_normalize", "rent_unpivot",
    ]
    if is_building:
        normalizations.extend(["building_use_group_from_primary_use", "building_gross_floor_area_m2"])
    if naver_industry_attention:
        normalizations.extend(["rel_index_by_anchor", "recent_3m_mean_no_seasonal_adjustment", "yoy_clean_recent_mean", "seasonal_phase_label"])
    if synthetic and gen_ev:
        normalizations.append("radius_license_count_by_industry")
    if news_catalogs:
        normalizations.extend(sorted({
            normalization
            for catalog in news_catalogs
            for normalization in (
                ["bigkinds_xlsx_export_parse", "news_exclusion_filter", "news_id_url_dedup"]
                if catalog.source_name == "bigkinds"
                else ["naver_news_api_snapshot", "news_url_exact_dedup"]
            ) + ["news_region_topic_match", "dong_common_name_alias"]
        }))
    coverage_payload = {
        "host_commercial_area": {"matched": 1 if host else 0, "expected": 1, "missing_reason": None if host else "상권 내부·300m 최근접 조건 밖"},
        "industry_store": {"matched": 1 if u_row else 0, "expected": 1, "missing_reason": None if u_row else "상권·행정동 업종 점포 데이터 결측"},
        "industry_sales": {"matched": 1 if sales_row else 0, "expected": 1, "missing_reason": None if sales_row else "상권·행정동 업종 추정매출 결측"},
        "flow": {"matched": 1 if f_row else 0, "expected": 1, "missing_reason": None if f_row else "상권·행정동 유동인구 결측"},
        "rent_specific": {"matched": 1 if rent_specific else 0, "expected": 1, "missing_reason": None if rent_specific else "R-ONE crosswalk review/unresolved 또는 서울 지수 fallback"},
    }
    freshness = {
        "flow": {"observed_end_period": request.quarter, "periods_behind_latest": 0, "update_cadence": "quarterly", "is_partial_latest": False},
        "store": {"observed_end_period": request.quarter, "periods_behind_latest": 0, "update_cadence": "quarterly", "is_partial_latest": False},
        "sales": {"observed_end_period": request.quarter, "periods_behind_latest": 0, "update_cadence": "quarterly", "is_partial_latest": False},
        "change": {"observed_end_period": request.quarter, "periods_behind_latest": 0, "update_cadence": "quarterly", "is_partial_latest": False},
        "transit": {"observed_end_period": "2026-08", "periods_behind_latest": 0, "update_cadence": "snapshot", "is_partial_latest": False},
        "apartment": {"observed_end_period": "2026-08", "periods_behind_latest": 0, "update_cadence": "snapshot", "is_partial_latest": False},
        "rent": {"observed_end_period": rent_period, "periods_behind_latest": 0, "update_cadence": "quarterly", "is_partial_latest": False},
    }
    if is_building:
        freshness["commercial_building"] = {
            "observed_end_period": seed.get("snapshot") or "20260809",
            "periods_behind_latest": 0,
            "update_cadence": "snapshot",
            "is_partial_latest": False,
        }
    if include_poi_context:
        coverage_payload["poi_context"] = {
            "matched": 1 if poi_context else 0,
            "expected": 1,
            "missing_reason": None if poi_context else "요청 영역과 일치하는 complete_requested_queries Kakao rect snapshot 없음",
        }
    coverage_payload["naver_industry_trend"] = {
        "matched": 1 if naver_industry_attention else 0,
        "expected": 1,
        "missing_reason": None if naver_industry_attention else "업종 검색트렌드 CSV 또는 계절성 분석 결과 없음",
    }
    for catalog in news_catalogs:
        coverage_key = "bigkinds_news" if catalog.source_name == "bigkinds" else "naver_news_snapshot"
        coverage_payload[coverage_key] = {
            "matched": 1,
            "expected": 1,
            "missing_reason": None,
        }
        news_age_days, news_is_stale = news_snapshot_age(catalog.period_end)
        freshness[coverage_key] = {
            "observed_end_period": catalog.period_end,
            "retrieved_at_utc": catalog.retrieved_at_utc,
            "snapshot_age_days": news_age_days,
            "is_stale": news_is_stale,
            "stale_threshold_days": NEWS_SNAPSHOT_STALE_DAYS,
            "periods_behind_latest": 0,
            "update_cadence": "manual_snapshot",
            "is_partial_latest": False,
        }
        if news_is_stale:
            source_label_ko = "빅카인즈" if catalog.source_name == "bigkinds" else "네이버"
            context_notes.append(
                f"{source_label_ko} 뉴스 snapshot이 관측 종료일 {catalog.period_end} 기준 {news_age_days}일 경과"
                f"(기준 {NEWS_SNAPSHOT_STALE_DAYS}일) — FC-51-news 보도량 맥락의 최신성 낮음, 새 snapshot 재수집 권장 (판정·정렬 미반영)"
            )
    if synthetic:
        coverage_payload["gridpoint_generated_evidence"] = {
            "matched": 1 if gen_ev else 0, "expected": 1,
            "missing_reason": None if gen_ev else "seeds.json 경로에 gridpoint_evidence.jsonl 없음 또는 evidence_id 불일치",
        }
        if gen_ev:
            freshness["gridpoint_license_context"] = {
                "observed_end_period": gen_ev.get("generated_at", "2026-09"),
                "periods_behind_latest": 0, "update_cadence": "snapshot", "is_partial_latest": False,
            }
        else:
            missing.append({"feature": "gridpoint_generated_evidence",
                            "reason": "합성 좌표 seed에 gridpoint_evidence.jsonl 미연결 — 반경 인허가 맥락 없음"})
    if poi_context:
        normalizations.extend(["kakao_rect_grid", "kakao_place_id_dedup", "poi_radius_count"])
        freshness["kakao_poi"] = {
            "observed_end_period": poi_context.retrieved_at[:10], "periods_behind_latest": 0,
            "update_cadence": "snapshot", "is_partial_latest": False,
        }
    if naver_industry_attention:
        freshness["naver_industry_trend"] = {
            "observed_end_period": naver_industry_attention["latest_ym"], "periods_behind_latest": 0,
            "update_cadence": "monthly", "is_partial_latest": False,
        }
    else:
        missing.append({"feature": "FC-42", "reason": "업종 검색 관심도 CSV 또는 계절성 분석 결과 없음"})
    if not news_catalogs:
        missing.append({"feature": "FC-51-news", "reason": "정규화된 빅카인즈 또는 수동 네이버 시설·개발 뉴스 snapshot 없음"})

    # 인구 3종(FC-03·04·05·06a·06b) evidence·정규화·최신성 병합 — 전부 context_notes 버킷.
    ev.extend(pop_ctx.evidence)
    for tag in pop_ctx.normalizations:
        if tag not in normalizations:
            normalizations.append(tag)
    freshness.update(pop_ctx.freshness)
    # 도시계획·정비사업(FC-51·52) evidence·최신성 병합 — 전부 context_notes 버킷 (#29).
    ev.extend(plan_ctx.evidence)
    freshness.update(plan_ctx.freshness)
    for tag in ("plan_stage_3group_crosswalk", "uq120_polygon_pip"):
        if plan_ctx.evidence and tag not in normalizations:
            normalizations.append(tag)
    demand_composition = {
        "features": pop_ctx.dimension_features + (["FC-08"] if households else []),
        "status": "partial" if pop_ctx.dimension_features else "inactive",
        "grain_notes": {
            **pop_ctx.grain_notes,
            **({"FC-08": f"지점 반경 500m 아파트 {households}세대 · 약한 배경 신호(FC-03 상주인구와 세트)"} if households else {}),
            **({"note": "인구 데이터 소스 미연결(--source db) — --source files 필요"} if pop is None else {}),
        },
    }

    return {
        "candidate_id": candidate_id, "candidate_type": candidate_type,
        "spatial_grain": "지점(생성)" if synthetic else "지점",
        **({"synthetic_anchor": True} if synthetic else {}),
        "region": {"sido": request.sido, "sigungu": request.sigungu, "dong": request.dong}, "location": location,
        "industry_code": request.industry_code, "fit_tier": tier, "fit_index": None, "score_version": None,
        "score_is_predictive": False, "greenfield": greenfield,
        "data_confidence": {"level": confidence, "reasons": confidence_reasons},
        # 정렬 전용(인구 하향 반영 전 등급). run_pipeline 이 정렬 직후 제거한다.
        "_sort_confidence": sort_confidence,
        "feature_build": {
            "build_passed": True, "merge_key_definition": "기준_년분기_코드 + 공간코드 + 서비스_업종_코드", "merge_key_dup_rate": 0,
            "quarters_used": {"flow": [request.quarter], "sales": [request.quarter], "store": [request.quarter], "change": [request.quarter], "rent": [rent_period] if re.fullmatch(r"[0-9]{5}", rent_period) else []},
            "normalizations_applied": normalizations,
            "grain_resolution": {"method": "path_and_code_membership", "conflicts": 0},
            "coverage": coverage_payload,
            "sources": source_list,
        },
        "dimension_evidence": {
            "현재수요": {"features": ["FC-01", "FC-07", "FC-31"], "status": "mixed", "grain_notes": {"FC-01": f"{f_scope or '없음'} 배경값 · 검증상 신호 없음 → context_notes만, 판정·정렬 미반영", "FC-07": "지점 반경 직접 계산 · bus_n만 약한 배경 신호, 역거리는 서술", "FC-31": f"{u_scope or '없음'}×업종 배경값 · 약한 배경 신호(과거 실적, 신규 성공 아님)"}},
            "수요구성": {**demand_composition, "note": "FC-03·04·05·06a·06b는 전부 context_notes 버킷(신호 없음) — 차원 status 계산만 참여, positive/negative·정렬 근거 금지 (candidate-selection-spec.md §4-2)"},
            "경쟁·시장수용": {"features": ["FC-30", "FC-31", "FC-32"], "status": "mixed", "grain_notes": {"all": f"{u_scope or '없음'}×업종 배경값", "observed_poi_context": "완결 Kakao 지점 반경 관측; 상세 맥락만 제공하며 판정·정렬에는 미사용" if poi_context else "Kakao POI context 미사용"}},
            "진입건전성": {"features": ["FC-10", "FC-11"], "status": "mixed", "entry_health_variant": "core", "entry_health_v1": {"version": "entry_health_v1", "grade": grade, "risk": risk, "formula": "0.25·폐업률분위 + 0.25·(100−개업률분위) + 0.25·(100−점포증감률분위) + 0.25·라벨리스크·100 (전 업종 통합)", "cuts": list(EH_CUTS[eh_scope]) if eh_scope else None, "cut_scope": eh_scope, "inputs": {**eh_inputs, "라벨": label}, "score_is_predictive": False, "used_in_판정": "반대근거 1항목", "ref": "docs/architecture/recommendation-fastapi.md#entry-health-v1"}},
            "미래신호": {
                "features": ["FC-42"] + (["FC-51-news"] if news_catalogs else []) + plan_ctx.dimension_features,
                "status": "partial" if (naver_industry_attention or news_catalogs or plan_ctx.dimension_features) else "inactive",
                "grain_notes": {
                    "FC-42": "업종 전체 서울시 검색 관심도. 최근 원계열·계절 국면만 context_notes/evidence에 기록하며 후보 판정·정렬에는 미반영",
                    **({"FC-51-news": "빅카인즈·네이버 뉴스 수동 snapshot(시설·개발·정비 주제 검색)에서 자치구·행정동으로 매칭한 기사 수. topic_match_rate ~1.0이면 주제로 걸러낸 부분집합이 아니라 주제 한정 snapshot 내 지역 매칭 수. 공식 도시계획사업의 상태·확정 여부가 아니며 fit_tier·정렬 미반영"} if news_catalogs else {}),
                    **plan_ctx.grain_notes,
                    **({"note_plan": "도시계획·정비사업 미연결(--source db 시 context.plan_snapshot 미적재) — --source files 필요"} if plan is None else {}),
                },
            },
        },
        "profile_ref": {"anchor_id": str(seed["id"]), "host_area": host.code if host else None, "as_of_quarter": {"flow": request.quarter, "sales": request.quarter, "store": request.quarter, "change": request.quarter, "rent": rent_period, **({"kakao_poi": poi_context.retrieved_at[:10]} if poi_context else {}), **pop_ctx.as_of}, "profile_confidence": {"level": confidence, "reasons": confidence_reasons}},
        "reasons": reasons, "counter_evidence": counter, "context_notes": context_notes, "missing_features": missing,
        "source_freshness": freshness,
        "evidence": ev, "listing_url": None,
    }


def validate_candidates(candidates: list[dict[str, Any]]) -> list[str]:
    schema_path = SERVICE_ROOT / "artifacts/20-method/rag-evidence-schema.json"
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return ["jsonschema 미설치"]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    validator = Draft202012Validator(schema)
    for candidate in candidates:
        for error in sorted(validator.iter_errors(candidate), key=lambda e: list(e.path)):
            errors.append(f"{candidate.get('candidate_id')}: {list(error.path)} {error.message}")
    return errors


def _preference_anchor_rank(candidate: dict[str, Any], preferences: dict[str, Any]) -> int:
    """Rank explicit anchor preferences without changing evidence-based tiers.

    A natural-language preference such as "역에서 장사하고 싶다" is a
    presentation preference, not proof that a location is good.  It therefore
    only affects deterministic ordering among candidates with the same tier.
    Missing or unsupported preference data leaves the normal ordering intact.
    """
    anchor_type = candidate.get("location", {}).get("anchor", {}).get("type")
    type_map = {"station": "역", "apartment": "아파트단지", "bus_stop": "버스정류장", "poi": "카카오POI"}
    requested = {
        type_map[item.get("anchor_type")]
        for item in preferences.get("location_preferences", [])
        if isinstance(item, dict) and item.get("mode", "prefer") == "prefer" and item.get("anchor_type") in type_map
    }
    if anchor_type in requested:
        return 0
    # 건축물대장 seed처럼 후보 자체가 역이 아닌 경우에도 "역에서
    # 장사하고 싶음"을 무시하지 않는다. 후보 주변 관측 anchor가 300m
    # 이내인 경우 같은 fit_tier 안에서 우선 노출한다. 이는 선호 정렬이지
    # 후보 등급·Evidence를 바꾸는 판정이 아니다.
    nearby = candidate.get("location", {}).get("nearby_anchors", [])
    if any(
        item.get("type") in requested and (item.get("distance_m") or float("inf")) <= 300
        for item in nearby if isinstance(item, dict)
    ):
        return 0
    return 1


def _order_by_preferences(candidates: list[dict[str, Any]], preferences: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep tier and evidence ordering, surfacing requested anchor types first."""
    if not preferences.get("location_preferences"):
        return candidates
    indexed = list(enumerate(candidates))
    return [candidate for _, candidate in sorted(indexed, key=lambda pair: (
        0 if pair[1].get("fit_tier") == "추천" else 1 if pair[1].get("fit_tier") == "조건부 검토" else 2,
        _preference_anchor_rank(pair[1], preferences),
        pair[0],
    ))]


# ─── 데이터 소스: 원천 파일 vs PostgreSQL ────────────────────────────
#
# 두 소스는 로더의 **반환 자료구조를 100% 동일하게** 유지한다. build_candidate
# 이하 판정·근거·스키마 로직은 소스를 구분하지 않는다. DB 소스는 스코어링에
# 들어가는 무거운 분기 팩트(점포·추정매출·유동인구·상권변화지표)와 영역
# 폴리곤을 DB에서 읽는다. seed·반경·뉴스·임대료·네이버·POI컨텍스트 등 경량
# 스냅샷·파생·보조 맥락도 DB에 보완 적재된 경우 DB에서 읽으며, 파일 모드는
# 동일한 자료구조를 원천 파일에서 만든다.

# (folder, token) → (dataset, grain). run_pipeline 의 load_scope_index 호출과 1:1.
_DB_SCOPE_MAP: dict[tuple[str, str], tuple[str, str]] = {
    ("data/점포/2026년", "점포-상권"): ("store", "commercial_area"),
    ("data/점포/2026년", "점포-행정동"): ("store", "admin_dong"),
    ("data/추정매출/2026", "추정매출-상권"): ("sales", "commercial_area"),
    ("data/추정매출/2026", "추정매출-행정동"): ("sales", "admin_dong"),
    ("data/길단위인구", "길단위인구-상권"): ("flow", "commercial_area"),
    ("data/길단위인구", "길단위인구-행정동"): ("flow", "admin_dong"),
    ("data/상권변화지표", "상권변화지표-상권"): ("change", "commercial_area"),
    ("data/상권변화지표", "상권변화지표-행정동"): ("change", "admin_dong"),
}


class FileSource:
    """원천 CSV·shapefile 소스 (--source files). 기존 로더 그대로."""

    mode = "files"

    def describe(self) -> dict[str, Any]:
        return {"mode": "files", "root": "data/"}

    def layers(self):
        return load_layers()

    def scope_index(self, folder: str, token: str, quarter: str, scope_key: str, industry: str | None = None):
        return load_scope_index(folder, token, quarter, scope_key, industry)

    def environment(self, quarter: str, scope_key: str):
        token = "점포-상권" if scope_key == "상권_코드" else "점포-행정동"
        current = read_csv(find_file(ROOT / "data/점포/2026년", token))
        previous = read_csv(find_file(ROOT / "data/점포/2025년", token))
        return build_environment(current, previous, quarter, scope_key)

    # seed·반경·네이버·뉴스·임대료·crosswalk — 원천 파일 그대로
    def seeds(self, target_buffer, include_poi, generated_path, seed_mode="buildings"):
        return load_seeds(target_buffer, include_poi, generated_path, seed_mode)

    def radius_points(self):
        return load_radius_points()

    def naver_attention(self, industry_code):
        return load_naver_industry_attention(industry_code)

    def news_catalogs(self, include_news):
        return load_news_catalogs() if include_news else ([], {})

    def rent(self):
        return parse_rone_rent()

    def vacancy(self):
        return parse_rone_vacancy()

    def crosswalk(self):
        return parse_trdar_crosswalk()

    def population(self, flow_dong=None):
        return population.load_population(ROOT, flow_dong)

    def urban_plan(self):
        return urban_plan.load_from_files(ROOT)

    def retrieve_requests(self, requests, selected_region, industry_code, quarter, *, target_areas=None):
        return {
            "mode": "files",
            "requested_count": len(requests),
            "executed_count": 0,
            "results": [],
            "skipped_reason": "controlled DB retrieval은 source=db에서만 실행됩니다.",
        }


class DbSource:
    """PostgreSQL/PostGIS 소스 (--source db, 기본).

    영역 폴리곤 = location.area, 분기 팩트 = location.{store,sales,flow}_quarter +
    context.metric_snapshot(상권변화지표), 지역 배경 = location.area_store_totals.
    provenance(source_path)는 원천 파일 경로를 그대로 노출한다 — DB 행의
    source_file_id 가 그 파일을 가리키며, 파일이 여전히 저장소에 있으므로
    candidates.json 의 근거 경로가 files 모드와 바이트 동일하게 유지된다.
    """

    mode = "db"

    def __init__(self):
        from . import serving_db

        self._db = serving_db
        try:
            self._server = serving_db.ping()
        except serving_db.ServingDbError as exc:
            raise PipelineDependencyError("추천 데이터베이스 연결에 실패했습니다.") from exc

    def _query(self, sql: str, *, use_cache: bool = True) -> list[dict[str, str]]:
        try:
            return self._db.query(sql, use_cache=use_cache)
        except self._db.ServingDbError as exc:
            raise PipelineDependencyError("추천 데이터베이스 조회에 실패했습니다.") from exc

    def _data_version(self) -> dict[str, str]:
        try:
            return self._db.data_version()
        except self._db.ServingDbError as exc:
            raise PipelineDependencyError("추천 데이터베이스 버전 정보를 읽지 못했습니다.") from exc

    def describe(self) -> dict[str, Any]:
        return {
            "mode": "db",
            "target": self._db.target(),
            "server": self._server,
            "db_data_version": self._data_version(),
        }

    # -- 영역 레이어 --------------------------------------------------
    def _layer(self, unit: str) -> ShapeLayer:
        rows = self._query(
            "SELECT spatial_unit_code AS code, coalesce(spatial_unit_name,'') AS name, "
            "coalesce(area_m2::text,'') AS area, coalesce(sigungu_code,'') AS sg_code, "
            "coalesce(sigungu_name,'') AS sg_name, encode(ST_AsBinary(geom),'hex') AS wkb "
            f"FROM location.area WHERE spatial_unit_type = '{unit}' AND geom IS NOT NULL "
            "ORDER BY spatial_unit_code"
        )
        records: list[LayerRecord] = []
        geoms: list[Any] = []
        for row in rows:
            geoms.append(shapely_wkb.loads(bytes.fromhex(row["wkb"])))
            try:
                area = float(row["area"] or 0)
            except (TypeError, ValueError):
                area = 0.0
            code = row["code"].strip()
            records.append(LayerRecord(
                code=code,
                name=(row["name"] or code).strip(),
                area=area,
                raw={"SIGNGU_CD": row["sg_code"], "SIGNGU_CD_": row["sg_name"]},
            ))
        return ShapeLayer.from_records(records, geoms)

    def layers(self):
        trdar = self._layer("commercial_area")
        hinterland = self._layer("hinterland")
        dong = self._layer("admin_dong")
        sigungu_by_prefix = {
            rec.raw.get("SIGNGU_CD", ""): rec.raw.get("SIGNGU_CD_", "")
            for rec in trdar.records
            if rec.raw.get("SIGNGU_CD")
        }
        return trdar, hinterland, dong, sigungu_by_prefix

    @staticmethod
    def _provenance(folder: str, token: str) -> Any:
        """근거 출처 경로. 원천 파일이 있으면 그 경로(파일 모드와 동일), 없으면 논리 참조."""
        try:
            return find_file(ROOT / folder, token)
        except (PipelineError, OSError):
            return f"{folder}/*{token}*.csv (PostgreSQL 적재본)"

    # -- 분기 팩트 인덱스 -------------------------------------------
    def scope_index(self, folder: str, token: str, quarter: str, scope_key: str, industry: str | None = None):
        dataset, grain = _DB_SCOPE_MAP[(folder, token)]
        path = self._provenance(folder, token)  # 원천 파일 있으면 그 경로, 없으면 논리 참조
        if not re.fullmatch(r"[0-9]{5}", quarter):
            raise PipelineInputError(f"분기 코드 형식 오류: {quarter}")
        if industry is not None and industry not in SUPPORTED_INDUSTRIES:
            raise PipelineInputError(f"지원하지 않는 업종 코드: {industry}")
        ind_clause = f" AND industry_code = '{industry}'" if industry else ""
        if dataset == "store":
            sql = (
                f'SELECT spatial_unit_code AS "{scope_key}", '
                'total_store_count AS "전체_점포_수", franchise_store_count AS "프랜차이즈_점포_수" '
                f"FROM location.store_quarter WHERE period = '{quarter}' "
                f"AND spatial_unit_type = '{grain}'{ind_clause}"
            )
        elif dataset == "sales":
            sql = (
                f'SELECT spatial_unit_code AS "{scope_key}", sales_amount AS "당월_매출_금액" '
                f"FROM location.sales_quarter WHERE period = '{quarter}' "
                f"AND spatial_unit_type = '{grain}'{ind_clause}"
            )
        elif dataset == "flow":
            sql = (
                f'SELECT spatial_unit_code AS "{scope_key}", flow_total AS "총_유동인구_수" '
                f"FROM location.flow_quarter WHERE period = '{quarter}' AND spatial_unit_type = '{grain}'"
            )
        else:  # change — metric_snapshot 피벗
            sql = (
                f'SELECT spatial_unit_code AS "{scope_key}", '
                "max(value_text) FILTER (WHERE metric_name = 'change_indicator_code') AS \"상권_변화_지표\", "
                "max(value_text) FILTER (WHERE metric_name = 'change_indicator_name') AS \"상권_변화_지표_명\" "
                "FROM context.metric_snapshot "
                "WHERE metric_name IN ('change_indicator_code','change_indicator_name') "
                f"AND period = '{quarter}' AND spatial_unit_type = '{grain}' "
                "GROUP BY spatial_unit_code"
            )
        indexed: dict[str, dict[str, str]] = {}
        for row in self._query(sql):
            key = (row.get(scope_key) or "").strip()
            if not key:
                continue
            if key in indexed:
                raise PipelineInternalError(f"결합 키 중복: {dataset} {key}")
            indexed[key] = row
        return indexed, path

    def environment(self, quarter: str, scope_key: str):
        grain = "commercial_area" if scope_key == "상권_코드" else "admin_dong"
        prev_q = previous_quarter(quarter)
        rows = self._query(
            f'SELECT period AS "기준_년분기_코드", spatial_unit_code AS "{scope_key}", '
            'total_store_count AS "전체_점포_수", open_store_count AS "개업_점포_수", '
            'close_store_count AS "폐업_점포_수" '
            f"FROM location.area_store_totals WHERE spatial_unit_type = '{grain}' "
            f"AND period IN ('{quarter}', '{prev_q}')"
        )
        current = [r for r in rows if r["기준_년분기_코드"] == quarter]
        previous = [r for r in rows if r["기준_년분기_코드"] == prev_q]
        return build_environment(current, previous, quarter, scope_key)

    # -- seed·반경 (location.anchor_point) ---------------------------
    @staticmethod
    def _prov_str(folder: str, token: str) -> str:
        p = DbSource._provenance(folder, token)
        return str(p.relative_to(ROOT)) if isinstance(p, Path) else str(p)

    def _anchor_rows(self, anchor_type: str, *, file_like: str | None = None) -> list[dict[str, str]]:
        """context.anchor_snapshot 의 원본 CSV 행을 파일 순서대로 (빈 값 포함)."""
        where = f"anchor_type = '{anchor_type}'"
        if file_like is not None:
            where += f" AND source_file LIKE '{file_like}' AND source_file NOT LIKE 'data/카카오POI/context/%'"
        rows = self._query(
            f"SELECT attributes FROM context.anchor_snapshot WHERE {where} ORDER BY source_file, row_seq"
        )
        return [json.loads(r["attributes"]) for r in rows]

    def _building_rows(self) -> list[dict[str, str]]:
        """Tier1(commercial_building) + Tier2(link/register/floor_use) 보강.

        PR #14 리뷰 P1·P2: 도로명주소·건물명·상업층 확인여부를 조인한다. 한
        PNU에 distinct mgm_bldrgst_pk가 2개 이상이면(실재 건물이 여럿) 어느
        쪽에도 승격하지 않는다(NOT EXISTS 조건 — 루트 scripts/recommendation_pipeline.py
        와 동일 규칙). 상업층 확인여부는 postgres boolean이 CSV로 't'/'f'
        문자열로 오므로 여기서 pg_bool()로 안전 변환해 얹는다(load_building_seeds
        이후 단계는 이미 파이썬 bool을 받는다).
        """
        rows = self._query(
            "SELECT b.building_pk AS \"건물관리번호\", b.pnu AS \"PNU\", "
            "b.sigungu_code AS \"시군구코드\", b.sigungu_name AS \"시군구명\", "
            "b.legal_dong_code AS \"법정동코드\", b.lot_address AS \"대지위치\", "
            "b.lot_number AS \"지번\", b.lot_kind AS \"지번구분\", "
            "b.use_code AS \"용도코드\", b.use_name AS \"용도명\", b.use_group AS \"용도군\", "
            "b.floors_above AS \"지상층수\", b.floors_below AS \"지하층수\", "
            "b.building_area_m2 AS \"건축면적_㎡\", b.gross_floor_area_m2 AS \"연면적_㎡\", "
            "b.building_age_years AS \"건물연식_년\", "
            "b.area_join_type AS \"상권_결합\", "
            "replace(b.host_area_id, 'commercial_area:', '') AS \"상권_코드\", "
            "b.admin_dong_code AS \"행정동_코드\", b.admin_dong_name AS \"행정동_명\", "
            "ST_X(b.point) AS x_5181, ST_Y(b.point) AS y_5181, "
            "r.road_address AS \"도로명주소\", r.building_name AS \"건물명\", "
            "EXISTS (SELECT 1 FROM context.building_floor_use f WHERE f.mgm_bldrgst_pk = lk.mgm_bldrgst_pk) "
            "  AS has_confirmed_commercial_floor "
            "FROM context.commercial_building b "
            "LEFT JOIN LATERAL ("
            "  SELECT * FROM context.commercial_building_link l WHERE l.pnu = b.pnu "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM context.commercial_building_link l2 "
            "    WHERE l2.pnu = l.pnu AND l2.mgm_bldrgst_pk <> l.mgm_bldrgst_pk"
            "  ) LIMIT 1"
            ") lk ON true "
            "LEFT JOIN context.building_register r ON r.mgm_bldrgst_pk = lk.mgm_bldrgst_pk "
            "WHERE b.point IS NOT NULL "
            "ORDER BY b.building_pk, b.snapshot"
        )
        for row in rows:
            row["_has_confirmed_commercial_floor"] = pg_bool(row.get("has_confirmed_commercial_floor"))
        return rows

    def seeds(self, target_buffer, include_poi, generated_path, seed_mode="buildings"):
        if seed_mode not in {"anchors", "buildings", "hybrid"}:
            raise PipelineInputError("seed_mode는 anchors, buildings, hybrid 중 하나여야 합니다.")
        if seed_mode == "buildings":
            building_src = self._prov_str("data/건축물대장", "상가건물_서울")
            return load_building_seeds(target_buffer, self._building_rows(), building_src)

        out: list[dict[str, Any]] = []
        apt_src = self._prov_str("data/공동주택", "아파트단지_서울")
        for row in self._anchor_rows("apartment"):
            if row.get("geocode_신뢰도") not in ("high", "medium") or not row.get("X_5181"):
                continue
            try:
                point = Point(float(row["X_5181"]), float(row["Y_5181"]))
            except (TypeError, ValueError):
                continue
            if target_buffer.covers(point):
                out.append(_apt_seed(row, point, apt_src))
        stn_src = self._prov_str("data/도시철도역사", "역사정보_서울")
        for row in self._anchor_rows("station"):
            try:
                point = Point(float(row["X_5181"]), float(row["Y_5181"]))
            except (TypeError, ValueError, KeyError):
                continue
            if target_buffer.covers(point):
                out.append(_station_seed(row, point, stn_src))
        if include_poi:
            poi = self._query(
                "SELECT source_file, attributes FROM context.anchor_snapshot "
                "WHERE anchor_type = 'kakao_poi' AND source_file LIKE 'data/카카오POI/%' "
                "AND source_file NOT LIKE 'data/카카오POI/context/%' ORDER BY source_file, row_seq"
            )
            for r in poi:
                row = json.loads(r["attributes"])
                try:
                    point = Point(float(row["x_5181"]), float(row["y_5181"]))
                except (TypeError, ValueError, KeyError):
                    continue
                if target_buffer.covers(point):
                    out.append(_poi_seed(row, point, r["source_file"]))
        if generated_path is not None:
            out.extend(load_generated_seeds(generated_path, target_buffer))
        if seed_mode == "hybrid":
            building_src = self._prov_str("data/건축물대장", "상가건물_서울")
            buildings = load_building_seeds(target_buffer, self._building_rows(), building_src)
            return merge_seeds(out) + buildings
        return merge_seeds(out)

    def radius_points(self):
        stations = _points_from_rows(self._anchor_rows("station"))
        buses = _points_from_rows(self._anchor_rows("bus_stop"))
        apts = _points_from_rows(self._anchor_rows("apartment"), require_geocode=True)
        return stations, buses, apts, {
            "transit": self._provenance("data/도시철도역사", "역사정보_서울"),
            "bus": self._provenance("data/버스정류장", "버스정류소_서울"),
            "apartment": self._provenance("data/공동주택", "아파트단지_서울"),
        }

    # -- 네이버 검색 관심도 (context.metric_snapshot) ----------------
    def naver_attention(self, industry_code):
        if industry_code not in NAVER_INDUSTRY_NAMES:
            return None, {}
        rows = self._query(
            "SELECT period, value_numeric FROM context.metric_snapshot "
            "WHERE metric_name = 'naver_rel_index' AND spatial_unit_type = 'region' "
            f"AND industry_code = '{industry_code}'"
        )
        points: list[tuple[str, float]] = []
        for r in rows:
            ym = (r.get("period") or "").strip()
            rel = num(r, "value_numeric")
            if re.fullmatch(r"[0-9]{6}", ym) and rel is not None:
                points.append((ym, rel))
        if not points:
            return None, {}
        name = NAVER_INDUSTRY_NAMES[industry_code]
        # 계절성은 후보 판정에 사용하지 않는 선택적 보조 맥락이다. 현재
        # migration에는 이 테이블이 없을 수 있으며, 파일 모드도 동일하게
        # 계절성 CSV가 없으면 빈 dict로 계산한다.
        season = {}
        attn = _naver_attention_compute(points, name, industry_code, season)
        return attn, {"naver_trend": self._provenance("data/네이버트렌드", "업종_검색트렌드_월")}

    # -- 임대료·crosswalk (context.rent_index, location.area_crosswalk) --
    def rent(self):
        rows = self._query(
            "SELECT grain, rone_area, period AS \"기준_년분기_코드\", value_numeric AS \"값\" "
            "FROM context.rent_index "
            "WHERE store_type = '소규모상가' AND indicator = '임대가격지수' AND grain IN ('상권', '서울전체')"
        )
        area_rows: dict[str, dict[str, str]] = {}
        seoul_rows: dict[str, str] = {}
        for r in rows:
            quarter = r.get("기준_년분기_코드", "")
            if r["grain"] == "상권" and r["rone_area"]:
                old = area_rows.get(r["rone_area"])
                if old is None or quarter > old.get("기준_년분기_코드", ""):
                    area_rows[r["rone_area"]] = r
            elif r["grain"] == "서울전체" and quarter > seoul_rows.get("quarter", ""):
                seoul_rows = {"quarter": quarter, "value": r.get("값", "")}
        prov = self._provenance("data/임대료", "R-ONE_임대동향_분기")
        return area_rows, seoul_rows, (prov if isinstance(prov, Path) else None)

    def vacancy(self):
        rows = self._query(
            "SELECT grain, rone_area, period AS \"기준_년분기_코드\", value_numeric AS \"값\" "
            "FROM context.rent_index "
            "WHERE store_type = '소규모상가' AND indicator = '공실률' AND grain IN ('상권', '서울전체')"
        )
        area_rows: dict[str, dict[str, str]] = {}
        seoul_rows: dict[str, str] = {}
        for r in rows:
            quarter = r.get("기준_년분기_코드", "")
            if r["grain"] == "상권" and r["rone_area"]:
                old = area_rows.get(r["rone_area"])
                if old is None or quarter > old.get("기준_년분기_코드", ""):
                    area_rows[r["rone_area"]] = r
            elif r["grain"] == "서울전체" and quarter > seoul_rows.get("quarter", ""):
                seoul_rows = {"quarter": quarter, "value": r.get("값", "")}
        prov = self._provenance("data/임대료", "R-ONE_공실률_분기")
        return area_rows, seoul_rows, (prov if isinstance(prov, Path) else None)

    def crosswalk(self):
        rows = self._query(
            "SELECT split_part(source_area_id, ':', 2) AS rone, split_part(target_area_id, ':', 2) AS trdar "
            "FROM location.area_crosswalk "
            "WHERE relation_type = 'rone_to_commercial_proxy' AND join_eligible"
        )
        return {r["trdar"]: {"R_ONE_상권": r["rone"]} for r in rows if r.get("trdar")}

    def population(self, flow_dong=None):
        # context.population_snapshot(dataset·grain·spatial_code·period·attributes jsonb)에서
        # as_of 파티션을 읽어 파일 소스와 동일 코어(population.assemble)로 조립한다.
        # 적재: services/recommendation-api/scripts/ingest_population.py.
        # 테이블 미적재 시 None(missing 처리) — to_regclass 로 먼저 확인해 조회 실패로
        # 요청 전체가 중단되지 않게 한다.
        reg = self._query("SELECT to_regclass('context.population_snapshot') AS reg")
        if not reg or not reg[0].get("reg"):
            return None
        # 계단식이라 dataset 별 as_of 파티션만 읽는다. 구 파티션이 남아 있어도
        # (dataset, period) 로 고정해 조회 결과에 섞이지 않게 한다 → attributes 와
        # as_of 가 항상 as_of 상수를 가리킨다.
        as_of = {
            "resident": population.RESIDENT_AS_OF,
            "worker": population.WORKER_AS_OF,
            "foreign_resident": population.FOREIGN_LATEST_COMPLETE,
        }
        _in = ", ".join(f"('{ds}', '{q}')" for ds, q in (
            ("resident", as_of["resident"]),
            ("worker", as_of["worker"]),
            ("foreign", as_of["foreign_resident"]),
        ))
        rows = self._query(
            "SELECT dataset, grain, spatial_code, attributes "
            f"FROM context.population_snapshot WHERE (dataset, period) IN ({_in})"
        )
        if not rows:
            return None
        buckets: dict[tuple[str, str], dict[str, dict[str, str]]] = {}
        for r in rows:
            buckets.setdefault((r["dataset"], r["grain"]), {})[r["spatial_code"]] = json.loads(r["attributes"])
        return population.assemble(
            resident_trdar=buckets.get(("resident", "commercial_area"), {}),
            resident_dong=buckets.get(("resident", "admin_dong"), {}),
            worker_trdar=buckets.get(("worker", "commercial_area"), {}),
            worker_dong=buckets.get(("worker", "admin_dong"), {}),
            foreign_dong_raw={
                code: {
                    "장기_외국인_평균": population._f(row, "장기_외국인_평균"),
                    "단기_외국인_평균": population._f(row, "단기_외국인_평균"),
                    "장기_관측일수": population._f(row, "장기_관측일수"),
                    "단기_관측일수": population._f(row, "단기_관측일수"),
                }
                for code, row in buckets.get(("foreign", "admin_dong"), {}).items()
            },
            crosswalk=(population.load_crosswalk_from_db(self._query)
                       or population.load_crosswalk(ROOT)),  # DB(location.area_crosswalk) 우선, 미적재 시 파일 폴백
            flow_dong=flow_dong,
            as_of=as_of,
            source_paths={**population._SOURCE_PATHS,
                          "population_crosswalk": "location.area_crosswalk (commercial_to_admin_overlap)"},
        )

    def urban_plan(self):
        # context.plan_snapshot(urban_project_overlap + redevelopment_association)에서 조립.
        # 미적재 시 None → 파일 소스 폴백/ missing 처리 (#29).
        return urban_plan.load_from_db(self._query, ROOT)

    def retrieve_requests(self, requests, selected_region, industry_code, quarter, *, target_areas=None):
        # RAG 검색 SQL 은 지역·차원·업종별로 갈라져 종류가 매우 많고(수백 지역 ×
        # 최대 7차원 × 업종) 최종 후보 상권 최대50개로 제한한다. 공용 캐시에 태우면 값비싼
        # Seoul-wide 블롭을 FIFO 로 밀어내므로 캐시를 우회한다.
        try:
            return execute_retrieval_requests(
                lambda sql: self._query(sql, use_cache=False),
                requests, selected_region, industry_code, quarter,
                target_areas=target_areas,
            )
        except (ValueError, self._db.ServingDbError) as exc:
            raise PipelineDependencyError("RAG 읽기 전용 검색 도구를 실행하지 못했습니다.") from exc

    # -- 뉴스 (context.news_snapshot + news_manifest) ---------------
    def news_catalogs(self, include_news):
        if not include_news:
            return [], {}
        manifests = {
            r["source"]: json.loads(r["manifest"])
            for r in self._query("SELECT source, manifest FROM context.news_manifest")
        }
        rec_rows = self._query(
            "SELECT source, topic_match, "
            "coalesce((source_attributes->>'seoul_scope')::boolean, false) AS seoul_scope, "
            "array_to_json(sigungu_tags) AS sigungu_tags, array_to_json(dong_tags) AS dong_tags, "
            "array_to_json(topic_tags) AS topic_tags FROM context.news_snapshot"
        )
        by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rec_rows:
            by_source[r["source"]].append({
                "topic_match": r["topic_match"] == "t",
                "seoul_scope": r["seoul_scope"] == "t",
                "sigungu_tags": json.loads(r["sigungu_tags"] or "[]"),
                "dong_tags": json.loads(r["dong_tags"] or "[]"),
                "topic_tags": json.loads(r["topic_tags"] or "[]"),
            })
        catalogs: list[NewsCatalog] = []
        paths: dict[str, Any] = {}

        def _abs(rel: str) -> Path:
            p = Path(rel)
            return p if p.is_absolute() else (ROOT / p)

        # 파일 모드와 동일 순서: 빅카인즈 → 네이버
        if "bigkinds" in manifests and by_source.get("bigkinds"):
            m = manifests["bigkinds"]
            records = by_source["bigkinds"]
            period = m.get("search_period") or {}
            jsonl = _abs(m.get("output_jsonl") or "data/뉴스/bigkinds_news.jsonl")
            mani = _abs(m.get("manifest_path") or f"{jsonl}_manifest.json")
            catalogs.append(NewsCatalog(
                records=records, jsonl_path=jsonl, manifest_path=mani,
                query_label=str(m.get("query_label") or "빅카인즈 뉴스"),
                query_metadata_verified=bool(m.get("query_metadata_verified", False)),
                period_start=str(period.get("start") or "미상"), period_end=str(period.get("end") or "미상"),
                raw_row_count=int(m.get("raw_row_count") or 0),
                normalized_row_count=int(m.get("normalized_row_count") or len(records)),
                excluded_counts={str(k): int(v) for k, v in (m.get("excluded_counts") or {}).items()},
                dedupe_counts={str(k): int(v) for k, v in (m.get("dedupe_counts") or {}).items()},
                source_name="bigkinds",
                retrieved_at_utc=str(m["ingested_at_utc"]) if m.get("ingested_at_utc") else None,
                queries=(str(m.get("query_label") or "빅카인즈 뉴스"),),
                topic_match_count=int(m.get("topic_match_count") or sum(1 for x in records if x["topic_match"])),
            ))
            paths["bigkinds_news"] = jsonl
            paths["bigkinds_news_manifest"] = mani
        if "naver_news_api" in manifests and by_source.get("naver_news_api"):
            m = manifests["naver_news_api"]
            records = by_source["naver_news_api"]
            retrieved_at = str(m.get("retrieved_at_utc") or "")
            queries = tuple(str(x) for x in (m.get("query_labels") or []) if str(x).strip())
            per = retrieved_at[:10] if retrieved_at else "미상"
            raw_count = int(m.get("raw_result_count") or 0)
            norm_count = int(m.get("normalized_row_count") or len(records))
            jsonl = _abs(m.get("output_jsonl") or "data/뉴스/naver_news_snapshot.jsonl")
            mani = _abs(m.get("manifest_path") or "data/뉴스/naver_news_snapshot_manifest.json")
            catalogs.append(NewsCatalog(
                records=records, jsonl_path=jsonl, manifest_path=mani,
                query_label=" | ".join(queries) or "네이버 뉴스",
                query_metadata_verified=bool(m.get("query_metadata_verified", False)),
                period_start=per, period_end=per,
                raw_row_count=raw_count, normalized_row_count=norm_count,
                excluded_counts={},
                dedupe_counts={"url_exact": int(m.get("deduped_count") or max(raw_count - norm_count, 0))},
                source_name="naver_news", retrieved_at_utc=retrieved_at or None, queries=queries,
                topic_match_count=int(m.get("topic_match_count") or sum(1 for x in records if x["topic_match"])),
            ))
            paths["naver_news_snapshot"] = jsonl
            paths["naver_news_snapshot_manifest"] = mani
        return catalogs, paths


def make_source(source: str) -> FileSource | DbSource:
    return DbSource() if source == "db" else FileSource()


def run_pipeline(
    request: RecommendationRequest, out_dir: Path | None = None, include_poi: bool = False,
    include_poi_context: bool = False, limit: int | None = None,
    generated_points: Path | None = None, include_news: bool = True,
    source: str = "db", llm_mode: str = "auto", seed_mode: str = "buildings",
) -> dict[str, Any]:
    if request.industry_code is not None and request.industry_code not in SUPPORTED_INDUSTRIES:
        raise PipelineInputError(f"지원하지 않는 업종 코드: {request.industry_code}")
    if limit is not None and not 1 <= limit <= 50:
        raise PipelineInputError("limit은 1 이상 50 이하이어야 합니다.")
    if seed_mode not in {"anchors", "buildings", "hybrid"}:
        raise PipelineInputError("seed_mode는 anchors, buildings, hybrid 중 하나여야 합니다.")
    selected_region = {"sido": request.sido, "sigungu": request.sigungu, "dong": request.dong}
    for code_field in ('sigungu_code', 'admin_dong_code'):
        if getattr(request, code_field, None):
            selected_region[code_field] = getattr(request, code_field)
    reset_call_budget()  # 이 실행의 LLM 호출 상한 카운터 초기화 (LLM_MAX_CALLS_PER_RUN)
    try:
        input_interpretation = plan_input(
            selected_region=selected_region,
            raw_user_text=request.special_condition_text,
            explicit_industry_code=request.industry_code,
            llm_mode=llm_mode,
        )
    except LLMRuntimeError as exc:
        raise PipelineDependencyError("입력 해석 LLM을 사용할 수 없습니다.") from exc
    if input_interpretation["confirmation_required"]:
        questions = "; ".join(input_interpretation["clarification_questions"])
        raise PipelineInputError(f"입력 확인이 필요합니다: {questions}")
    resolved_industry = input_interpretation["resolved_industry_code"]
    if resolved_industry not in SUPPORTED_INDUSTRIES:
        raise PipelineInputError("업종을 확인할 수 없습니다. 업종 코드 또는 업종명을 입력해 주세요.")
    request = replace(request, industry_code=resolved_industry)
    if not re.fullmatch(r"[0-9]{4}[1-4]", request.quarter):
        raise PipelineInputError(f"분기 코드는 YYYYQ 형식(마지막 자리는 1~4)이어야 합니다: {request.quarter}")
    conditions = input_interpretation["conditions"]
    preferences = input_interpretation.get("preferences", {})
    src = make_source(source)
    data_source_manifest = src.describe()
    data_source_manifest["seed_mode"] = seed_mode
    trdar_layer, hinterland_layer, dong_layer, sigungu_by_prefix = src.layers()
    selected_dongs, target_poly, target_buffer = resolve_region(request, dong_layer, sigungu_by_prefix)
    # The spatial layer comes from location.area in DB mode. Resolve legacy
    # name-only calls once; every downstream retrieval uses these identifiers.
    gu_code = getattr(request, 'sigungu_code', None)
    dong_code = getattr(request, 'admin_dong_code', None)
    if selected_dongs:
        gu_code = selected_dongs[0].code[:5]
        if len(selected_dongs) == 1 and (request.dong or dong_code):
            dong_code = selected_dongs[0].code
        request = replace(request, sigungu=sigungu_by_prefix.get(gu_code, request.sigungu),
                          dong=selected_dongs[0].name if dong_code else request.dong,
                          sigungu_code=gu_code, admin_dong_code=dong_code)
    selected_region.update(sigungu=request.sigungu, dong=request.dong)
    if gu_code:
        selected_region['sigungu_code'] = gu_code
    if dong_code:
        selected_region['admin_dong_code'] = dong_code
    if request.dong or dong_code:
        # A legal-dong alias can resolve to multiple administrative polygons.
        # Preserve all resolved identifiers; never send the alias back to SQL.
        selected_region['admin_dong_codes'] = [record.code for record in selected_dongs]
        if not selected_dongs and dong_code:
            selected_region['admin_dong_codes'] = [dong_code]
    query_context = build_query_context(request.special_condition_text, selected_region,
                                        request.industry_code, conditions, preferences)
    question_contract = build_question_contract(query_context)
    query_context['question_contract'] = question_contract
    input_interpretation['question_contract'] = question_contract
    target_sigungu = request.sigungu

    # 현재 분기 정규화 feature table용 인덱스. 중복 키는 index_rows에서 즉시 중단한다.
    store_trdar, store_path = src.scope_index("data/점포/2026년", "점포-상권", request.quarter, "상권_코드", request.industry_code)
    store_dong, store_dong_path = src.scope_index("data/점포/2026년", "점포-행정동", request.quarter, "행정동_코드", request.industry_code)
    sales_trdar, sales_path = src.scope_index("data/추정매출/2026", "추정매출-상권", request.quarter, "상권_코드", request.industry_code)
    sales_dong, sales_dong_path = src.scope_index("data/추정매출/2026", "추정매출-행정동", request.quarter, "행정동_코드", request.industry_code)
    flow_trdar, flow_path = src.scope_index("data/길단위인구", "길단위인구-상권", request.quarter, "상권_코드")
    flow_dong, flow_dong_path = src.scope_index("data/길단위인구", "길단위인구-행정동", request.quarter, "행정동_코드")
    change_trdar, change_path = src.scope_index("data/상권변화지표", "상권변화지표-상권", request.quarter, "상권_코드")
    change_dong, change_dong_path = src.scope_index("data/상권변화지표", "상권변화지표-행정동", request.quarter, "행정동_코드")
    naver_industry_attention, naver_paths = src.naver_attention(request.industry_code)
    news_catalogs, news_paths = src.news_catalogs(include_news)

    eh_trdar, eh_trdar_dist = src.environment(request.quarter, "상권_코드")
    eh_dong, eh_dong_dist = src.environment(request.quarter, "행정동_코드")

    trdar_area = {r.code: r.area for r in trdar_layer.records}
    dong_area = {r.code: r.area for r in dong_layer.records}
    # sorted 필수 — build_candidate가 pct()의 bisect_left로 서울 분위를 매긴다.
    # (기존 files 경로는 이 리스트가 CSV 순서라 분위가 소스 순서에 의존하는 버그였다.)
    all_flow_density = sorted(
        num(row, "총_유동인구_수") / trdar_area[key]
        for key, row in flow_trdar.items()
        if key in trdar_area and num(row, "총_유동인구_수") is not None and trdar_area[key]
    )
    all_sales_rows = sales_trdar  # 상권 업종 추정매출 인덱스와 동일 (서울 분위 분포용)
    all_store_rows = store_trdar
    all_sales_pp = [num(all_sales_rows[key], "당월_매출_금액") / num(all_store_rows[key], "전체_점포_수") for key in all_sales_rows if key in all_store_rows and num(all_sales_rows[key], "당월_매출_금액") is not None and num(all_store_rows[key], "전체_점포_수") not in (None, 0)]
    stations, buses, apts, radius_paths = src.radius_points()
    poi_context = load_completed_poi_context(request) if include_poi_context else None
    seeds = src.seeds(target_buffer, include_poi, generated_points, seed_mode)
    if not seeds:
        raise PipelineDependencyError("선택 범위에 추천 seed 데이터가 없습니다.")

    source_paths = {
        "store": store_path, "store_dong": store_dong_path,
        "sales": sales_path, "sales_dong": sales_dong_path,
        "flow": flow_path, "flow_dong": flow_dong_path,
        "change": change_path, "change_dong": change_dong_path,
        **naver_paths,
        **news_paths,
        **radius_paths,
    }
    crosswalk = src.crosswalk()
    rone_areas, rone_seoul, rone_path = src.rent()
    rone_vac_areas, rone_vac_seoul, rone_vac_path = src.vacancy()
    pop = src.population(flow_dong)  # 인구 3종(상주·직장·외국인, #28). 테이블 미적재 시 None → missing 처리
    try:
        plan = src.urban_plan()  # 도시계획·정비사업 추진단계(#29). None이면 미연결 처리
    except (PipelineError, LLMRuntimeError, OSError, RuntimeError):
        # 원천 CSV·plan_snapshot 미가용 시 #29 근거 없이 진행(라운드 6 F43).
        plan = None
    flow_dong_total_seoul = sorted(
        v for row in flow_dong.values()
        if (v := num(row, "총_유동인구_수")) is not None
    )
    candidates = [build_candidate(
        request, conditions, seed, trdar_layer, hinterland_layer, dong_layer, sigungu_by_prefix, target_sigungu,
        store_trdar, sales_trdar, flow_trdar, change_trdar, store_dong, sales_dong, flow_dong, change_dong,
        trdar_area, dong_area, eh_trdar, eh_trdar_dist, eh_dong, eh_dong_dist, stations, buses, apts,
        poi_context, include_poi_context, naver_industry_attention,
        news_catalogs,
        source_paths, all_flow_density, sorted(all_sales_pp), crosswalk, rone_areas, rone_seoul, rone_path,
        rone_vac_areas, rone_vac_seoul, rone_vac_path,
        pop, flow_dong_total_seoul, plan,
    ) for seed in seeds]
    # 검증된 품질 신호가 없으므로 근거 수로 등수를 매기지 않는다(-len(reasons) 제거).
    # tier → (같은 tier 안에서 candidate_type 라운드로빈으로 인터리브) → 반대근거 적은 순 → 신뢰도 → id.
    # 인터리브: --limit로 자를 때 특정 앵커 타입(예: 아파트)이 id 정렬 편향으로 통째로 잘리는 것을 막는다.
    # 신뢰도는 _sort_confidence(인구 FC-06 하향 반영 전) 기준 — 인구 근거는 순위 불변(F36).
    _conf_rank = {"high": 0, "medium": 1, "low": 2}
    _tier_rank = {"추천": 0, "조건부 검토": 1, "주의": 2}

    def _within_key(c: dict[str, Any]) -> tuple:
        return (len(c["counter_evidence"]), _conf_rank.get(c["_sort_confidence"], 3), c["candidate_id"])

    ordered: list[dict[str, Any]] = []
    for tier in sorted({c["fit_tier"] for c in candidates}, key=lambda t: _tier_rank.get(t, 3)):
        group = sorted((c for c in candidates if c["fit_tier"] == tier), key=_within_key)
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for c in group:
            buckets[c["candidate_type"]].append(c)
        cursors = {t: 0 for t in buckets}
        while any(cursors[t] < len(buckets[t]) for t in buckets):
            for t in sorted(buckets):
                if cursors[t] < len(buckets[t]):
                    ordered.append(buckets[t][cursors[t]])
                    cursors[t] += 1
    candidates = ordered
    for c in candidates:
        c.pop("_sort_confidence", None)  # 정렬 전용 내부 키 — 스키마·출력에서 제외
    candidates = _order_by_preferences(candidates, preferences)
    if limit is not None:
        candidates = candidates[:limit]
    target_areas = []
    seen_target_codes = set()
    for candidate in candidates:
        host = (candidate.get("location") or {}).get("host_commercial_area") or {}
        if host.get("code") and str(host["code"]) not in seen_target_codes:
            seen_target_codes.add(str(host["code"]))
            target_areas.append({
                "spatial_unit_type": "commercial_area",
                "spatial_unit_code": str(host["code"]),
                "spatial_unit_name": host.get("name") or "",
            })
    retrieval_context = src.retrieve_requests(
        retrieval_requests_for_contract(question_contract, input_interpretation.get("retrieval_requests", [])),
        selected_region, request.industry_code, request.quarter,
        target_areas=target_areas,
    )
    input_interpretation["retrieval"] = retrieval_context
    try:
        errors = validate_candidates(candidates)
    except Exception as exc:
        raise PipelineInternalError("추천 Evidence 스키마 검증을 실행할 수 없습니다.") from exc

    if out_dir is None:
        out_dir = ROOT / "output/recommendation_runs" / safe_slug(f"{request.sigungu}-{request.dong or '전체'}-{request.industry_code}-{request.quarter}")
    out_dir.mkdir(parents=True, exist_ok=True)
    request_payload = {
        **asdict(request),
        "conditions": conditions,
        "confirmation_required": False,
        "input_interpretation": input_interpretation,
    }
    coverage: dict[str, dict[str, int]] = defaultdict(lambda: {"matched": 0, "expected": 0})
    for candidate in candidates:
        for feature, item in candidate["feature_build"]["coverage"].items():
            coverage[feature]["matched"] += item["matched"]
            coverage[feature]["expected"] += item["expected"]
    if errors:
        explanations = {
            "cards": [], "explanation_mode": "unavailable", "degraded": True,
            "llm": {"mode": llm_mode, "configured": False, "reason": "Evidence schema 검증 실패"},
        }
    else:
        try:
            explanations = explain_candidates(
                candidates, llm_mode=llm_mode, retrieval_context=retrieval_context,
                query_context=query_context,
            )
        except LLMRuntimeError as exc:
            raise PipelineDependencyError("추천 설명 LLM을 사용할 수 없습니다.") from exc
    summary = {
        "candidate_count": len(candidates), "seed_count_before_limit": len(seeds), "applied_limit": limit,
        "fit_tier_counts": dict(Counter(c["fit_tier"] for c in candidates)),
        "greenfield_count": sum(c["greenfield"] for c in candidates),
        "schema_error_count": len(errors), "coverage": dict(coverage),
        "unsupported_conditions": conditions["unsupported_conditions"],
        "preferences": preferences,
        "retrieval": retrieval_context,
        "synthetic_anchor_count": sum(1 for c in candidates if c.get("synthetic_anchor")),
        "generated_points": str(generated_points) if generated_points else None,
        "include_poi": include_poi, "include_poi_context": include_poi_context,
        "include_news": include_news,
        "seed_mode": seed_mode,
        "news_context": {
            "used": bool(news_catalogs),
            "query_label": " | ".join(catalog.query_label for catalog in news_catalogs) or None,
            "query_metadata_verified": all(catalog.query_metadata_verified for catalog in news_catalogs) if news_catalogs else False,
            "period": " | ".join(
                f"{catalog.period_start}~{catalog.period_end}" if catalog.source_name == "bigkinds"
                else f"snapshot@{catalog.retrieved_at_utc or catalog.period_end}"
                for catalog in news_catalogs
            ) or None,
            "row_counts_note": "성격이 다른 snapshot이므로 합산하지 않는다 — sources[]의 source별 raw/normalized를 사용",
            "jsonl_path": relative_path(news_catalogs[0].jsonl_path) if len(news_catalogs) == 1 else None,
            "manifest_path": relative_path(news_catalogs[0].manifest_path) if len(news_catalogs) == 1 else None,
            "stale_threshold_days": NEWS_SNAPSHOT_STALE_DAYS,
            "any_stale": any(news_snapshot_age(catalog.period_end)[1] for catalog in news_catalogs) if news_catalogs else False,
            "any_topic_filter_effective": any((catalog.topic_match_count / catalog.normalized_row_count) < 0.95 for catalog in news_catalogs if catalog.normalized_row_count) if news_catalogs else False,
            "sources": [
                {
                    "source": catalog.source_name,
                    "query_label": catalog.query_label,
                    "queries": list(catalog.queries),
                    "query_metadata_verified": catalog.query_metadata_verified,
                    "period": f"{catalog.period_start}~{catalog.period_end}" if catalog.source_name == "bigkinds" else f"snapshot@{catalog.retrieved_at_utc or catalog.period_end}",
                    "retrieved_at_utc": catalog.retrieved_at_utc,
                    "observed_end_period": catalog.period_end,
                    "snapshot_age_days": news_snapshot_age(catalog.period_end)[0],
                    "is_stale": news_snapshot_age(catalog.period_end)[1],
                    "raw_row_count": catalog.raw_row_count,
                    "normalized_row_count": catalog.normalized_row_count,
                    "topic_match_count": catalog.topic_match_count,
                    "topic_match_rate": round(catalog.topic_match_count / catalog.normalized_row_count, 3) if catalog.normalized_row_count else None,
                    "jsonl_path": relative_path(catalog.jsonl_path),
                    "manifest_path": relative_path(catalog.manifest_path),
                }
                for catalog in news_catalogs
            ],
            "limitation": "빅카인즈·네이버 뉴스 수동 snapshot의 시설·개발·정비 주제 보도량을 지역 보조 맥락으로만 사용. 공식 사업 상태·확정·성공 outcome이 아니며 fit_tier·정렬에 사용하지 않음. 네이버는 파이프라인에서 API를 호출하지 않음. snapshot이 관측 종료일 기준 " + str(NEWS_SNAPSHOT_STALE_DAYS) + "일을 넘기면 is_stale=true로 표시하며 재수집 신호로만 쓴다(판정·정렬 불변). topic_match_rate가 ~1.0이면 snapshot 자체가 이미 주제 한정 검색이라 기사량은 '주제 필터링된 부분집합'이 아니라 '주제 한정 snapshot 안에서 지역 매칭된 수'다. 두 snapshot은 성격이 달라 row 수를 합산하지 않는다(sources[]의 source별 수 사용)",
        },
        "poi_context": {
            "used": poi_context is not None,
            "csv_path": relative_path(poi_context.csv_path) if poi_context else None,
            "manifest_path": relative_path(poi_context.manifest_path) if poi_context else None,
            "retrieved_at": poi_context.retrieved_at if poi_context else None,
            "categories": list(poi_context.categories) if poi_context else [],
            "limitation": "완결 Kakao 카테고리 관측 스냅샷만. fit_tier 판정·정렬·성공 outcome에는 사용하지 않음",
        },
        "join_rule": "R-ONE crosswalk join_eligible=yes만 자동 결합",
        "input_interpretation": {
            "planner_mode": input_interpretation["planner"]["execution"],
            "parse_confidence": input_interpretation["parse_confidence"],
            "confirmation_required": input_interpretation["confirmation_required"],
            "analysis_plan_step_count": len(input_interpretation["analysis_plan"]),
        },
        "explanation": {
            "mode": explanations["explanation_mode"],
            "degraded": explanations["degraded"],
            "llm": explanations["llm"],
        },
    }
    manifest_source_paths = {relative_path(p) for p in source_paths.values()}
    if poi_context:
        manifest_source_paths.update({relative_path(poi_context.csv_path), relative_path(poi_context.manifest_path)})
    # 후보 evidence에 실제 연결된 경로(합성 seed·생성 evidence·인허가)를 manifest 최상위에도 합친다.
    for candidate in candidates:
        manifest_source_paths.update(candidate["feature_build"].get("sources", []))
    manifest = {"pipeline": "recommendation_pipeline_v2_llm_input", "build_passed": not errors, "request": request_payload,
                "candidate_count": len(candidates), "source_paths": sorted(manifest_source_paths),
                "data_source": data_source_manifest, "seed_mode": seed_mode,
                "input_interpretation": input_interpretation,
                "explanation": explanations["llm"],
                "validation": {"schema_errors": errors, "generated_by": "GPT(Codex)", "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()}}
    atomic_write_text(out_dir / "request.json", json.dumps(request_payload, ensure_ascii=False, indent=2))
    atomic_write_text(out_dir / "candidates.json", json.dumps(candidates, ensure_ascii=False, indent=2))
    atomic_write_text(out_dir / "explanations.json", json.dumps(explanations, ensure_ascii=False, indent=2))
    atomic_write_text(out_dir / "coverage-summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
    atomic_write_text(out_dir / "run-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    notes = [
        f"# 추천 파이프라인 실행 — {request.sigungu} {request.dong or '전체'} · {request.industry_code}",
        "", f"- 생성 주체: GPT(Codex)", f"- 분기: {request.quarter}", f"- seed: {len(seeds)}개 → 출력 {len(candidates)}개",
        f"- 등급: {summary['fit_tier_counts']}", f"- schema 오류: {len(errors)}",
        f"- 입력 해석: {input_interpretation['planner']['execution']} · confidence={input_interpretation['parse_confidence']}",
        f"- 분석 계획: {len(input_interpretation['analysis_plan'])}개(read-only)",
        f"- 설명 생성: {explanations['explanation_mode']} · degraded={explanations['degraded']}",
        f"- seed_mode: {seed_mode}",
        "- 매물·공실·성공 outcome이 없는 조건은 `missing_features`/`unsupported_conditions`로 유지한다.",
    ]
    if errors:
        notes.append("\n## schema 오류\n" + "\n".join(f"- {error}" for error in errors[:20]))
    for candidate in candidates:
        notes.append(f"\n## {candidate['location']['place_name']} ({candidate['candidate_id']}) — {candidate['fit_tier']}")
        notes.append(f"- host: {(candidate['location']['host_commercial_area'] or {}).get('name', '없음(행정동 배경값)')}")
        notes.append(f"- 근거(+): {'; '.join(candidate['reasons']) or '없음'}")
        notes.append(f"- 반대(−): {'; '.join(candidate['counter_evidence']) or '없음'}")
        if candidate.get("context_notes"):
            notes.append(f"- 배경 서술(판정·정렬 미반영): {'; '.join(candidate['context_notes'])}")
        poi_evidence = [item for item in candidate["evidence"] if "관측_" in item["metric_name"]]
        if poi_evidence:
            notes.append("- 상세 POI 관측(중립): " + "; ".join(
                f"{item['metric_name']} {item['value']}{item['unit']}" for item in poi_evidence
            ))
            notes.append("- POI 한계: supplied 카테고리 스냅샷일 뿐 전체 상가·수요·공실·성공을 뜻하지 않으며 등급·정렬에는 미사용")
    atomic_write_text(out_dir / "run-notes.md", "\n".join(notes) + "\n")
    if errors:
        raise PipelineInternalError("추천 Evidence 내부 검증에 실패했습니다.")
    return {"out_dir": str(out_dir), "summary": summary, "candidates": candidates, "explanations": explanations, "input_interpretation": input_interpretation}


def main() -> int:
    parser = argparse.ArgumentParser(description="증거 중심 서울 입지 추천 MVP 파이프라인")
    parser.add_argument("--sido", required=True)
    parser.add_argument("--sigungu", required=True)
    parser.add_argument("--dong")
    parser.add_argument("--industry-code", help="선택 업종 코드. 생략하면 --user-input에서 LLM/최소 파서가 추출")
    parser.add_argument("--special-condition-text", default="")
    parser.add_argument("--user-input", help="와이어프레임의 자연어 입력. --special-condition-text보다 우선")
    parser.add_argument("--quarter", default=DEFAULT_QUARTER)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--include-poi", action="store_true")
    parser.add_argument("--include-poi-context", action="store_true", help="complete Kakao rect POI snapshot을 반경 RAG 보조 근거로 연결")
    parser.add_argument("--include-generated-points", type=Path,
                        help="generate_gridpoint_evidence.py의 seeds.json(또는 디렉터리) — 격자 생성 좌표를 후보 seed에 추가")
    parser.add_argument("--no-news-context", action="store_true",
                        help="data/뉴스의 빅카인즈·수동 네이버 뉴스 보조 근거를 사용하지 않음(비교 QA용)")
    parser.add_argument("--source", choices=("db", "files"), default="db",
                        help="분기 팩트·영역 폴리곤 소스. db(기본)=PostgreSQL(Docker ideaton-db), files=원천 CSV/shp")
    parser.add_argument("--llm-mode", choices=("auto", "required", "offline"), default="auto",
                        help="auto=설정된 hosted LLM 사용·없으면 폴백, required=LLM 필수, offline=LLM 호출 안 함")
    parser.add_argument("--seed-mode", choices=("anchors", "buildings", "hybrid"), default="buildings",
                        help="buildings(건축물대장 건물 centroid 기본), anchors(기존 역·단지·POI), hybrid(둘 다)")
    parser.add_argument("--db-url", help="--source db 접속 문자열 오버라이드(기본: .env DATABASE_URL 또는 POSTGRES_*)")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.db_url:
        os.environ["DATABASE_URL"] = args.db_url
    raw_user_text = args.user_input if args.user_input is not None else args.special_condition_text
    request = RecommendationRequest(args.sido, args.sigungu, args.dong, args.industry_code, raw_user_text, args.quarter)
    try:
        result = run_pipeline(request, args.out, args.include_poi, args.include_poi_context, args.limit,
                              args.include_generated_points, include_news=not args.no_news_context,
                              source=args.source, llm_mode=args.llm_mode, seed_mode=args.seed_mode)
    except PipelineError as exc:
        print(f"FAIL: {exc}")
        return 2
    summary = result["summary"]
    print(f"PASS: {result['out_dir']}")
    print(json.dumps({k: v for k, v in summary.items() if k != "coverage"}, ensure_ascii=False))
    print(json.dumps(summary["coverage"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
