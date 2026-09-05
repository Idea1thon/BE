"""Kakao Local POI를 후보 seed 확장용 정규화 CSV로 저장한다.

이 스크립트는 특정 중심점·반경의 카테고리/키워드 검색 결과를 수집한다.
서울 전체 경계를 자동으로 추정하거나 모든 상권을 대신 순회하지 않는다.
행정동·상권 단위의 전체 수집은 후보 파이프라인에서 확정한 경계와 호출예산을
받아 별도 배치로 실행해야 한다.

예시(잠실역 주변 음식점·카페):
  .venv/bin/python3 scripts/ingest_kakao_poi.py \
      --x 127.1000 --y 37.5133 --radius 1000 \
      --category FD6 --category CE7 \
      --out data/카카오POI/카카오_로컬_POI_잠실역.csv

출력 좌표는 원본 WGS84와 기존 공간 데이터 결합용 EPSG:5181을 함께 보존한다.
동일 Kakao place id는 한 행으로 dedup하고 검색 카테고리·질의는 병합한다.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import tempfile
from collections import OrderedDict

from pyproj import Transformer

from _env import ROOT
from kakao_local import (
    CATEGORY_NAMES,
    budget_snapshot,
    search_category,
    search_keyword,
)

DEFAULT_OUT = os.path.join(ROOT, "data", "카카오POI", "카카오_로컬_POI.csv")
FIELDS = [
    "source", "source_type", "poi_id", "place_name", "category_name",
    "category_group_code", "category_group_name", "phone",
    "road_address_name", "address_name", "x_wgs84", "y_wgs84",
    "x_5181", "y_5181", "place_url", "distance_m", "search_mode",
    "search_query", "search_center_x", "search_center_y", "search_radius_m",
    "search_rect", "grid_cell_id", "retrieved_at_utc",
]


def _float(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _fmt(value: float | None, digits: int) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def _join(existing: str, incoming: str) -> str:
    values = [x for x in (existing.split(" | ") if existing else []) if x]
    if incoming:
        values.extend(x for x in incoming.split(" | ") if x)
    return " | ".join(dict.fromkeys(values))


def _doc_to_row(doc: dict, *, mode: str, query: str, center_x: float,
                center_y: float, radius: int, retrieved_at: str,
                to5181) -> dict | None:
    x = _float(doc.get("x"))
    y = _float(doc.get("y"))
    if x is None or y is None:
        return None
    x5181, y5181 = to5181(x, y)
    code = str(doc.get("category_group_code") or "").strip().upper()
    return {
        "source": "kakao_local",
        "source_type": "observed_poi",
        "poi_id": str(doc.get("id") or "").strip(),
        "place_name": str(doc.get("place_name") or "").strip(),
        "category_name": str(doc.get("category_name") or "").strip(),
        "category_group_code": code,
        "category_group_name": str(doc.get("category_group_name") or CATEGORY_NAMES.get(code, "")).strip(),
        "phone": str(doc.get("phone") or "").strip(),
        "road_address_name": str(doc.get("road_address_name") or "").strip(),
        "address_name": str(doc.get("address_name") or "").strip(),
        "x_wgs84": _fmt(x, 8),
        "y_wgs84": _fmt(y, 8),
        "x_5181": _fmt(x5181, 2),
        "y_5181": _fmt(y5181, 2),
        "place_url": str(doc.get("place_url") or "").strip(),
        "distance_m": _fmt(_float(doc.get("distance")), 1),
        "search_mode": mode,
        "search_query": query,
        "search_center_x": _fmt(center_x, 8),
        "search_center_y": _fmt(center_y, 8),
        "search_radius_m": str(radius),
        "search_rect": "",
        "grid_cell_id": "",
        "retrieved_at_utc": retrieved_at,
    }


def _dedup(rows: list[dict]) -> tuple[list[dict], int]:
    result: OrderedDict[str, dict] = OrderedDict()
    duplicates = 0
    for row in rows:
        poi_id = row["poi_id"]
        key = f"id:{poi_id}" if poi_id else (
            f"fallback:{row['place_name'].casefold()}:{row['x_wgs84']}:{row['y_wgs84']}"
        )
        if key not in result:
            result[key] = row
            continue
        duplicates += 1
        old = result[key]
        for field in ("category_name", "category_group_code", "category_group_name",
                      "search_mode", "search_query", "search_rect", "grid_cell_id"):
            old[field] = _join(old[field], row[field])
        old_distance = _float(old["distance_m"])
        new_distance = _float(row["distance_m"])
        if new_distance is not None and (old_distance is None or new_distance < old_distance):
            old["distance_m"] = row["distance_m"]
    return list(result.values()), duplicates


def _fetch(args: argparse.Namespace) -> tuple[list[dict], dict]:
    retrieved_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5181", always_xy=True)
    budget_before = budget_snapshot()
    raw_rows: list[dict] = []
    request_count = 0
    skipped_no_coord = 0

    def pages(fetch_page, mode: str, query: str) -> None:
        nonlocal request_count, skipped_no_coord
        for page in range(1, args.max_pages + 1):
            if request_count >= args.max_requests:
                raise SystemExit(
                    f"이번 실행 API 요청 {request_count}회가 --max-requests({args.max_requests})에 도달했습니다."
                )
            response = fetch_page(page)
            request_count += 1
            meta = response.get("meta", {})
            for doc in response.get("documents", []):
                row = _doc_to_row(
                    doc, mode=mode, query=query, center_x=args.x, center_y=args.y,
                    radius=args.radius, retrieved_at=retrieved_at,
                    to5181=transformer.transform,
                )
                if row is None:
                    skipped_no_coord += 1
                else:
                    raw_rows.append(row)
            if meta.get("is_end", True):
                break

    for query in args.query or []:
        pages(
            lambda page, q=query: search_keyword(
                q, x=args.x, y=args.y, radius=args.radius, page=page,
                size=args.size, sort="distance",
            ),
            "keyword", query,
        )
    for code in args.category or []:
        code = code.upper()
        pages(
            lambda page, c=code: search_category(
                c, x=args.x, y=args.y, radius=args.radius, page=page,
                size=args.size, sort="distance",
            ),
            "category", code,
        )

    rows, duplicate_count = _dedup(raw_rows)
    rows.sort(key=lambda row: (
        _float(row["distance_m"]) is None,
        _float(row["distance_m"]) or 0.0,
        row["poi_id"],
    ))
    manifest = {
        "source": "Kakao Local REST API",
        "source_type": "observed_poi_snapshot",
        "retrieved_at_utc": retrieved_at,
        "coordinate_system": {"source": "EPSG:4326", "derived": "EPSG:5181"},
        "center": {"x_wgs84": args.x, "y_wgs84": args.y},
        "radius_m": args.radius,
        "queries": args.query or [],
        "category_group_codes": [x.upper() for x in (args.category or [])],
        "page_size": args.size,
        "max_pages_per_search": args.max_pages,
        "api_request_count": request_count,
        # low-level caller가 실제 HTTP 시도(재시도·오류 포함)를 보수적으로 기록한다.
        # request_count는 정상 응답을 받은 논리 검색 페이지 수다.
        "api_budget_before": budget_before,
        "api_budget_after": budget_snapshot(),
        "raw_rows_with_coordinates": len(raw_rows),
        "deduplicated_rows": len(rows),
        "duplicate_rows_removed": duplicate_count,
        "rows_skipped_without_coordinates": skipped_no_coord,
        "candidate_use": "optional seed/anchor expansion; not a success label or vacancy/listing dataset",
    }
    return rows, manifest


def _atomic_csv(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".kakao_poi_", suffix=".csv", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def _atomic_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".kakao_poi_", suffix=".json", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def _main() -> None:
    ap = argparse.ArgumentParser(description="Kakao Local POI 정규화 수집")
    ap.add_argument("--x", type=float, required=True, help="검색 중심 경도(WGS84)")
    ap.add_argument("--y", type=float, required=True, help="검색 중심 위도(WGS84)")
    ap.add_argument("--radius", type=int, default=1000, help="검색 반경(m), 최대 20000")
    ap.add_argument("--query", action="append", help="키워드(반복 가능)")
    ap.add_argument("--category", action="append", help="카테고리 코드(반복 가능, 예: FD6)")
    ap.add_argument("--size", type=int, default=15, help="페이지당 행 수(1~15)")
    ap.add_argument("--max-pages", type=int, default=3, help="검색별 최대 페이지(기본 3, Kakao 최대 45행)")
    ap.add_argument("--max-requests", type=int, default=100, help="이번 실행 최대 API 요청 수")
    ap.add_argument("--out", default=DEFAULT_OUT, help="정규화 CSV 경로")
    ap.add_argument("--manifest", help="manifest JSON 경로(기본: CSV 옆 *_manifest.json)")
    args = ap.parse_args()
    if not args.query and not args.category:
        ap.error("--query 또는 --category를 하나 이상 지정해야 합니다.")
    if not 0 <= args.radius <= 20000:
        ap.error("--radius는 0~20000m여야 합니다.")
    if not 1 <= args.size <= 15:
        ap.error("--size는 1~15여야 합니다.")
    if args.max_pages < 1 or args.max_requests < 1:
        ap.error("--max-pages와 --max-requests는 1 이상이어야 합니다.")
    args.out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    manifest_path = args.manifest or os.path.splitext(args.out)[0] + "_manifest.json"
    if not os.path.isabs(manifest_path):
        manifest_path = os.path.join(ROOT, manifest_path)

    rows, manifest = _fetch(args)
    _atomic_csv(args.out, rows)
    manifest["output_csv"] = os.path.relpath(args.out, ROOT)
    manifest["output_manifest"] = os.path.relpath(manifest_path, ROOT)
    _atomic_json(manifest_path, manifest)
    print(f"저장 완료: {os.path.relpath(args.out, ROOT)} ({len(rows)}행)")
    print(f"manifest: {os.path.relpath(manifest_path, ROOT)}")
    print(f"API 요청: {manifest['api_request_count']}회, 중복 제거: {manifest['duplicate_rows_removed']}행")


if __name__ == "__main__":
    _main()
