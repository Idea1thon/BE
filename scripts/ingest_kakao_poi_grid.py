"""선택 영역 폴리곤을 Kakao Local ``rect`` 격자로 수집한다.

카카오 장소 검색은 한 질의에서 최대 45 페이지 × 15개 결과만 반환한다.
이 스크립트는 행정동 경계를 EPSG:5181 격자로 나누고, 포화된 격자만
4분할해 supplied 카테고리/키워드의 관측 POI 스냅샷과 커버리지 manifest를
만든다. 결과는 ``data/카카오POI/context/``에 두어 기존 선택적 seed CSV와
섞지 않는다.

예시(먼저 호출 계획만 확인):
  .venv/bin/python3 scripts/ingest_kakao_poi_grid.py \\
      --sigungu 송파구 --dong 잠실동 --category FD6 --category CE7 --dry-run

예시(실제 수집):
  .venv/bin/python3 scripts/ingest_kakao_poi_grid.py \\
      --sigungu 송파구 --dong 잠실동 --category FD6 --category CE7 \\
      --cell-size-m 500 --min-cell-size-m 125 --max-api-requests 300

커버리지 ``complete_requested_queries``는 *입력한* 카테고리/키워드와
경계에 대해서만 완전 페이지 순회가 끝났다는 뜻이다. Kakao 전체 상가,
공실, 매물 또는 성공 outcome의 완전성을 뜻하지 않는다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyproj import Transformer
from shapely.geometry import Point, box

from _env import ROOT as ENV_ROOT
from ingest_kakao_poi import _atomic_csv, _atomic_json, _dedup, _doc_to_row
from kakao_local import KakaoLocalError, budget_snapshot, search_category, search_keyword
from recommendation_pipeline import RecommendationRequest, load_layers, resolve_region, safe_slug


ROOT = Path(ENV_ROOT)
PAGE_SIZE = 15
# Kakao Local은 page 번호를 45까지 허용하지만, keyword/category의
# ``pageable_count``는 최대 45문서다. 따라서 size=15일 때 한 셀·질의의
# 관측 가능 결과는 최대 3페이지다. total_count가 이를 넘으면 더 작은
# rect로 분할해 관측 범위를 줄여야 한다.
MAX_PAGEABLE_DOCUMENTS = 45
MAX_PAGES_PER_CELL_QUERY = math.ceil(MAX_PAGEABLE_DOCUMENTS / PAGE_SIZE)
DEFAULT_CELL_SIZE_M = 500
DEFAULT_MIN_CELL_SIZE_M = 125
DEFAULT_MAX_API_REQUESTS = 300


@dataclass(frozen=True)
class GridCell:
    cell_id: str
    level: int
    minx: float
    miny: float
    maxx: float
    maxy: float

    @property
    def geometry(self):
        return box(self.minx, self.miny, self.maxx, self.maxy)

    @property
    def width_m(self) -> float:
        return self.maxx - self.minx

    @property
    def height_m(self) -> float:
        return self.maxy - self.miny


@dataclass(frozen=True)
class QuerySpec:
    mode: str
    value: str


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _target_geometry(args: argparse.Namespace) -> tuple[Any, list[dict[str, str]]]:
    """공통 추천 파이프라인과 같은 시군구·법정동 alias 규칙으로 영역을 고른다."""
    _, _, dong_layer, sigungu_by_prefix = load_layers()
    request = RecommendationRequest(
        sido=args.sido,
        sigungu=args.sigungu,
        dong=args.dong,
        industry_code="CS100001",  # 이 수집기는 업종 feature를 읽지 않는다.
    )
    selected, target, _ = resolve_region(request, dong_layer, sigungu_by_prefix)
    if args.buffer_m:
        target = target.buffer(args.buffer_m)
    selected_meta = [{"code": rec.code, "name": rec.name} for rec in selected]
    return target, selected_meta


def build_base_cells(target: Any, cell_size_m: int) -> list[GridCell]:
    """대상 폴리곤과 교차하는 EPSG:5181 정사각형 셀을 만든다."""
    minx, miny, maxx, maxy = target.bounds
    first_x = math.floor(minx / cell_size_m) * cell_size_m
    first_y = math.floor(miny / cell_size_m) * cell_size_m
    cells: list[GridCell] = []
    x_index = 0
    x = first_x
    while x < maxx:
        y_index = 0
        y = first_y
        while y < maxy:
            candidate = GridCell(
                cell_id=f"g{x_index}-{y_index}", level=0,
                minx=x, miny=y, maxx=x + cell_size_m, maxy=y + cell_size_m,
            )
            if target.intersects(candidate.geometry):
                cells.append(candidate)
            y_index += 1
            y += cell_size_m
        x_index += 1
        x += cell_size_m
    return cells


def split_cell(cell: GridCell) -> list[GridCell]:
    midx = (cell.minx + cell.maxx) / 2
    midy = (cell.miny + cell.maxy) / 2
    return [
        GridCell(f"{cell.cell_id}.0", cell.level + 1, cell.minx, cell.miny, midx, midy),
        GridCell(f"{cell.cell_id}.1", cell.level + 1, midx, cell.miny, cell.maxx, midy),
        GridCell(f"{cell.cell_id}.2", cell.level + 1, cell.minx, midy, midx, cell.maxy),
        GridCell(f"{cell.cell_id}.3", cell.level + 1, midx, midy, cell.maxx, cell.maxy),
    ]


def rect_wgs84(cell: GridCell, transformer: Transformer) -> tuple[str, float, float]:
    """EPSG:5181 셀을 Kakao ``rect``과 중심점(WGS84)으로 바꾼다."""
    corners = [
        transformer.transform(cell.minx, cell.miny),
        transformer.transform(cell.minx, cell.maxy),
        transformer.transform(cell.maxx, cell.miny),
        transformer.transform(cell.maxx, cell.maxy),
    ]
    longitudes = [value[0] for value in corners]
    latitudes = [value[1] for value in corners]
    min_lon, max_lon = min(longitudes), max(longitudes)
    min_lat, max_lat = min(latitudes), max(latitudes)
    rect = f"{min_lon:.8f},{min_lat:.8f},{max_lon:.8f},{max_lat:.8f}"
    return rect, (min_lon + max_lon) / 2, (min_lat + max_lat) / 2


def query_specs(args: argparse.Namespace) -> list[QuerySpec]:
    specs = [QuerySpec("category", value.strip().upper()) for value in (args.category or []) if value.strip()]
    specs.extend(QuerySpec("keyword", value.strip()) for value in (args.query or []) if value.strip())
    if not specs:
        raise ValueError("--category 또는 --query를 하나 이상 지정해야 합니다.")
    return specs


def _fetch_one(
    cell: GridCell,
    spec: QuerySpec,
    *,
    target: Any,
    to_wgs84: Transformer,
    to_5181: Transformer,
    retrieved_at: str,
    request_count: int,
    max_api_requests: int,
) -> tuple[list[dict[str, str]], dict[str, Any], int]:
    """한 셀·한 질의를 끝까지 읽고, 포화 여부를 반환한다."""
    rect, center_x, center_y = rect_wgs84(cell, to_wgs84)
    rows: list[dict[str, str]] = []
    returned_docs = 0
    outside_target = 0
    last_meta: dict[str, Any] = {}
    last_page = 0
    status = "complete"

    for page in range(1, MAX_PAGES_PER_CELL_QUERY + 1):
        if request_count >= max_api_requests:
            status = "partial_request_cap"
            break
        if spec.mode == "category":
            response = search_category(spec.value, rect=rect, page=page, size=PAGE_SIZE, sort="accuracy")
        else:
            response = search_keyword(spec.value, rect=rect, page=page, size=PAGE_SIZE, sort="accuracy")
        request_count += 1
        last_page = page
        last_meta = response.get("meta", {}) if isinstance(response, dict) else {}
        for doc in response.get("documents", []):
            returned_docs += 1
            row = _doc_to_row(
                doc, mode=spec.mode, query=spec.value, center_x=center_x, center_y=center_y,
                radius=0, retrieved_at=retrieved_at, to5181=to_5181.transform,
            )
            if row is None:
                continue
            row["search_rect"] = rect
            row["grid_cell_id"] = cell.cell_id
            try:
                point = Point(float(row["x_5181"]), float(row["y_5181"]))
            except (TypeError, ValueError):
                continue
            if target.covers(point):
                rows.append(row)
            else:
                outside_target += 1
        if last_meta.get("is_end", True):
            break
    else:
        status = "saturated"

    # Kakao의 pageable_count는 최대 45문서다. total_count가 이를 넘으면
    # 마지막 페이지의 is_end가 true여도 사각형 전체를 관측하지 못했으므로
    # 정확한 커버리지를 위해 더 작은 셀로 분할한다.
    pageable_count = last_meta.get("pageable_count")
    total_count = last_meta.get("total_count")
    if status == "complete" and (
        (last_page == MAX_PAGES_PER_CELL_QUERY and not last_meta.get("is_end", True))
        or (isinstance(total_count, int) and isinstance(pageable_count, int) and total_count > pageable_count)
    ):
        status = "saturated"
    report = {
        "cell_id": cell.cell_id,
        "level": cell.level,
        "cell_bounds_epsg5181": [round(cell.minx, 2), round(cell.miny, 2), round(cell.maxx, 2), round(cell.maxy, 2)],
        "rect_wgs84": rect,
        "cell_size_m": round(max(cell.width_m, cell.height_m), 2),
        "query_mode": spec.mode,
        "query_value": spec.value,
        "pages_fetched": last_page,
        "page_size": PAGE_SIZE,
        "total_count": last_meta.get("total_count"),
        "pageable_count": last_meta.get("pageable_count"),
        "is_end": last_meta.get("is_end"),
        "documents_returned": returned_docs,
        "documents_inside_target": len(rows),
        "documents_outside_target": outside_target,
        "status": status,
    }
    return rows, report, request_count


def _manifest_base(args: argparse.Namespace, target: Any, selected: list[dict[str, str]],
                   cells: list[GridCell], specs: list[QuerySpec]) -> dict[str, Any]:
    to_wgs84 = Transformer.from_crs("EPSG:5181", "EPSG:4326", always_xy=True)
    minx, miny, maxx, maxy = target.bounds
    bounds_wgs = rect_wgs84(GridCell("target", 0, minx, miny, maxx, maxy), to_wgs84)[0]
    base_tasks = len(cells) * len(specs)
    return {
        "source": "Kakao Local REST API",
        "source_type": "observed_poi_grid_snapshot",
        "generated_by": "GPT(Codex)",
        "selection": {
            "sido": args.sido,
            "sigungu": args.sigungu,
            "requested_dong": args.dong,
            "resolved_admin_dongs": selected,
            "buffer_m": args.buffer_m,
            "area_m2": round(float(target.area), 2),
            "bounds_epsg5181": [round(minx, 2), round(miny, 2), round(maxx, 2), round(maxy, 2)],
            "bounds_wgs84_rect": bounds_wgs,
        },
        "query_contract": {
            "categories": [spec.value for spec in specs if spec.mode == "category"],
            "keywords": [spec.value for spec in specs if spec.mode == "keyword"],
            "rect_search": True,
            "page_size": PAGE_SIZE,
            "max_pages_per_cell_query": MAX_PAGES_PER_CELL_QUERY,
            "max_documents_before_split": MAX_PAGEABLE_DOCUMENTS,
            "coverage_meaning": "입력한 카테고리·키워드와 선택 경계에 대한 Kakao 페이지 순회 상태. 전체 상가·공실·매물·성공 outcome 완전성은 아님.",
        },
        "grid": {
            "crs": "EPSG:5181",
            "base_cell_size_m": args.cell_size_m,
            "min_cell_size_m": args.min_cell_size_m,
            "base_cell_count": len(cells),
            "base_query_task_count": base_tasks,
            "planned_minimum_api_requests": base_tasks,
            "planned_maximum_before_splitting": base_tasks * MAX_PAGES_PER_CELL_QUERY,
        },
    }


def collect(args: argparse.Namespace) -> tuple[list[dict[str, str]], dict[str, Any]]:
    target, selected = _target_geometry(args)
    cells = build_base_cells(target, args.cell_size_m)
    specs = query_specs(args)
    manifest = _manifest_base(args, target, selected, cells, specs)
    if args.dry_run:
        manifest.update({
            "retrieved_at_utc": None,
            "run_status": "dry_run",
            "coverage": {"status": "not_collected", "reason": "--dry-run: 네트워크 호출 없음"},
            "api_request_count": 0,
            "api_budget_before": budget_snapshot(),
            "api_budget_after": budget_snapshot(),
        })
        return [], manifest

    retrieved_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    to_wgs84 = Transformer.from_crs("EPSG:5181", "EPSG:4326", always_xy=True)
    to_5181 = Transformer.from_crs("EPSG:4326", "EPSG:5181", always_xy=True)
    budget_before = budget_snapshot()
    queue: deque[tuple[GridCell, QuerySpec]] = deque((cell, spec) for cell in cells for spec in specs)
    reports: list[dict[str, Any]] = []
    raw_rows: list[dict[str, str]] = []
    request_count = 0
    run_status = "complete"
    errors: list[str] = []

    while queue:
        cell, spec = queue.popleft()
        try:
            rows, report, request_count = _fetch_one(
                cell, spec, target=target, to_wgs84=to_wgs84, to_5181=to_5181,
                retrieved_at=retrieved_at, request_count=request_count,
                max_api_requests=args.max_api_requests,
            )
        except KakaoLocalError as exc:
            run_status = "partial_api_error"
            errors.append(str(exc))
            break
        raw_rows.extend(rows)
        if report["status"] == "saturated":
            if cell.width_m / 2 >= args.min_cell_size_m and cell.height_m / 2 >= args.min_cell_size_m:
                report["status"] = "saturated_split"
                for child in split_cell(cell):
                    if target.intersects(child.geometry):
                        queue.append((child, spec))
            else:
                report["status"] = "partial_saturated_min_cell"
                run_status = "partial_saturated"
        reports.append(report)
        if report["status"] == "partial_request_cap":
            run_status = "partial_request_cap"
            break

    rows, duplicate_count = _dedup(raw_rows)
    rows.sort(key=lambda row: (row.get("category_group_code", ""), row.get("place_name", ""), row.get("poi_id", "")))
    completed_leaves = sum(1 for report in reports if report["status"] == "complete")
    partial_leaves = sum(1 for report in reports if report["status"].startswith("partial_"))
    if run_status == "complete" and partial_leaves:
        run_status = "partial_saturated"
    manifest.update({
        "retrieved_at_utc": retrieved_at,
        "run_status": run_status,
        "api_request_count": request_count,
        "api_budget_before": budget_before,
        "api_budget_after": budget_snapshot(),
        "raw_rows_inside_target": len(raw_rows),
        "deduplicated_rows": len(rows),
        "duplicate_rows_removed": duplicate_count,
        "coverage": {
            "status": "complete_requested_queries" if run_status == "complete" and not queue else "partial",
            "completed_leaf_queries": completed_leaves,
            "partial_leaf_queries": partial_leaves,
            "unattempted_query_tasks": len(queue),
            "saturated_cells_split": sum(1 for report in reports if report["status"] == "saturated_split"),
            "coverage_reports": reports,
        },
        "errors": errors,
        "candidate_use": "observed POI context for detailed location evidence; not a vacancy/listing or success outcome dataset",
    })
    return rows, manifest


def _default_out(args: argparse.Namespace) -> Path:
    label = "-".join(part for part in (args.sigungu, args.dong or "전체") if part)
    return ROOT / "data" / "카카오POI" / "context" / f"카카오_로컬_POI_격자_{safe_slug(label)}.csv"


def _main() -> None:
    ap = argparse.ArgumentParser(description="선택 행정동 경계의 Kakao POI rect 격자 수집·커버리지 QA")
    ap.add_argument("--sido", default="서울특별시")
    ap.add_argument("--sigungu", required=True, help="예: 송파구")
    ap.add_argument("--dong", help="행정동 또는 확인된 법정동 alias, 생략 시 시군구 전체")
    ap.add_argument("--buffer-m", type=int, default=0, help="선택 경계 외 추가 수집 버퍼(m), 기본 0")
    ap.add_argument("--category", action="append", help="카테고리 코드(반복 가능, 예: FD6, CE7)")
    ap.add_argument("--query", action="append", help="보완 키워드(반복 가능)")
    ap.add_argument("--cell-size-m", type=int, default=DEFAULT_CELL_SIZE_M)
    ap.add_argument("--min-cell-size-m", type=int, default=DEFAULT_MIN_CELL_SIZE_M)
    ap.add_argument("--max-api-requests", type=int, default=DEFAULT_MAX_API_REQUESTS,
                    help="이번 실행의 논리 요청 상한. Kakao 영속 예산 가드가 별도로 적용됨")
    ap.add_argument("--dry-run", action="store_true", help="영역·격자·최소/최대 호출 계획만 manifest로 저장")
    ap.add_argument("--out", help="CSV 경로(기본: data/카카오POI/context/) ")
    ap.add_argument("--manifest", help="manifest JSON 경로(기본: CSV 옆 *_manifest.json)")
    args = ap.parse_args()
    if args.buffer_m < 0:
        ap.error("--buffer-m은 0 이상이어야 합니다.")
    if not 50 <= args.cell_size_m <= 5000:
        ap.error("--cell-size-m은 50~5000m여야 합니다.")
    if not 25 <= args.min_cell_size_m <= args.cell_size_m:
        ap.error("--min-cell-size-m은 25 이상이며 --cell-size-m 이하여야 합니다.")
    if args.max_api_requests < 1:
        ap.error("--max-api-requests는 1 이상이어야 합니다.")
    try:
        query_specs(args)
    except ValueError as exc:
        ap.error(str(exc))

    out_path = Path(args.out) if args.out else _default_out(args)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    manifest_path = Path(args.manifest) if args.manifest else out_path.with_name(f"{out_path.stem}_manifest.json")
    if not manifest_path.is_absolute():
        manifest_path = ROOT / manifest_path

    rows, manifest = collect(args)
    manifest["output_csv"] = _relative(out_path)
    manifest["output_manifest"] = _relative(manifest_path)
    if not args.dry_run:
        _atomic_csv(str(out_path), rows)
    _atomic_json(str(manifest_path), manifest)
    if args.dry_run:
        print(f"호출 계획 저장: {_relative(manifest_path)}")
    else:
        print(f"저장 완료: {_relative(out_path)} ({len(rows)}행)")
        print(f"manifest: {_relative(manifest_path)}")
        print(f"논리 API 요청: {manifest['api_request_count']}회 · 커버리지: {manifest['coverage']['status']}")
    if manifest["run_status"] != "complete" and not args.dry_run:
        raise SystemExit(f"부분 수집 상태: {manifest['run_status']} — manifest를 확인한 뒤 재실행하십시오.")


if __name__ == "__main__":
    _main()
