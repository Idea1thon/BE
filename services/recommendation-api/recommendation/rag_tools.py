"""Allowlisted, read-only retrieval tools for the recommendation RAG.

The LLM can request a tool and dimensions, but it never supplies SQL, table
names, joins, region values, industry codes, or periods.  The server binds
those values and builds the small set of queries below.
"""
from __future__ import annotations

import math
import re
from typing import Any, Callable


ALLOWED_RAG_TOOLS = {"search_region_evidence"}
ALLOWED_RAG_DIMENSIONS = {"sales", "stores", "flow", "change"}
MAX_RAG_REQUESTS = 4
MAX_RAG_ROWS_PER_DIMENSION = 20
_QUARTER_RE = re.compile(r"^[0-9]{5}$")
_INDUSTRY_RE = re.compile(r"^CS10000[1-9]$|^CS100010$")


def _sql_literal(value: str) -> str:
    """Quote a value for a server-built SQL statement.

    Values are additionally constrained by the request contract.  This helper
    is still required because ``serving_db`` executes a complete SQL string.
    """
    return "'" + value.replace("'", "''") + "'"


def validate_retrieval_requests(raw: Any) -> list[dict[str, Any]]:
    """Keep only bounded tool calls; discard arbitrary SQL or unknown tools."""
    if not isinstance(raw, list):
        return []
    output: list[dict[str, Any]] = []
    for item in raw[:MAX_RAG_REQUESTS]:
        if not isinstance(item, dict) or item.get("tool") not in ALLOWED_RAG_TOOLS:
            continue
        arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else item
        raw_dimensions = arguments.get("dimensions")
        if not isinstance(raw_dimensions, list):
            continue
        dimensions = list(dict.fromkeys(
            str(dimension).strip() for dimension in raw_dimensions
            if str(dimension).strip() in ALLOWED_RAG_DIMENSIONS
        ))
        if not dimensions:
            continue
        raw_limit = arguments.get("limit", MAX_RAG_ROWS_PER_DIMENSION)
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, (int, float)):
            raw_limit = MAX_RAG_ROWS_PER_DIMENSION
        if isinstance(raw_limit, float) and not math.isfinite(raw_limit):
            raw_limit = MAX_RAG_ROWS_PER_DIMENSION
        limit = max(1, min(MAX_RAG_ROWS_PER_DIMENSION, int(raw_limit)))
        reason = str(item.get("reason") or arguments.get("reason") or "사용자 요청에 필요한 지역 근거 조회").strip()[:240]
        output.append({
            "tool": "search_region_evidence",
            "scope": "selected_region",
            "dimensions": dimensions,
            "limit": limit,
            "reason": reason,
        })
    return output


def _area_predicate(selected_region: dict[str, str | None]) -> str:
    sigungu = str(selected_region.get("sigungu") or "").strip()
    dong = str(selected_region.get("dong") or "").strip()
    if dong:
        return (
            "a.spatial_unit_type = 'admin_dong' "
            f"AND a.spatial_unit_name = {_sql_literal(dong)}"
        )
    return (
        "a.spatial_unit_type = 'commercial_area' "
        f"AND a.sigungu_name = {_sql_literal(sigungu)}"
    )


def _dimension_sql(
    dimension: str,
    selected_region: dict[str, str | None],
    industry_code: str,
    quarter: str,
    limit: int,
) -> str:
    predicate = _area_predicate(selected_region)
    period = _sql_literal(quarter)
    industry = _sql_literal(industry_code)
    common = (
        "SELECT a.spatial_unit_type, a.spatial_unit_code, "
        "a.spatial_unit_name, "
    )
    if dimension == "sales":
        return common + (
            "'sales' AS dimension, s.sales_amount::text AS value, "
            "'location.sales_quarter' AS source_table "
            "FROM location.area a "
            "JOIN location.sales_quarter s ON s.spatial_unit_code = a.spatial_unit_code "
            "AND s.spatial_unit_type = a.spatial_unit_type "
            f"WHERE {predicate} AND s.period = {period} AND s.industry_code = {industry} "
            f"ORDER BY a.spatial_unit_code LIMIT {limit}"
        )
    if dimension == "stores":
        return common + (
            "'stores' AS dimension, json_build_object("
            "'total_store_count', s.total_store_count, "
            "'franchise_store_count', s.franchise_store_count)::text AS value, "
            "'location.store_quarter' AS source_table "
            "FROM location.area a "
            "JOIN location.store_quarter s ON s.spatial_unit_code = a.spatial_unit_code "
            "AND s.spatial_unit_type = a.spatial_unit_type "
            f"WHERE {predicate} AND s.period = {period} AND s.industry_code = {industry} "
            f"ORDER BY a.spatial_unit_code LIMIT {limit}"
        )
    if dimension == "flow":
        return common + (
            "'flow' AS dimension, f.flow_total::text AS value, "
            "'location.flow_quarter' AS source_table "
            "FROM location.area a "
            "JOIN location.flow_quarter f ON f.spatial_unit_code = a.spatial_unit_code "
            "AND f.spatial_unit_type = a.spatial_unit_type "
            f"WHERE {predicate} AND f.period = {period} "
            f"ORDER BY a.spatial_unit_code LIMIT {limit}"
        )
    if dimension == "change":
        return common + (
            "m.metric_name AS dimension, "
            "coalesce(m.value_text, m.value_numeric::text) AS value, "
            "'context.metric_snapshot' AS source_table "
            "FROM location.area a "
            "JOIN context.metric_snapshot m ON m.spatial_unit_code = a.spatial_unit_code "
            "AND m.spatial_unit_type = a.spatial_unit_type "
            f"WHERE {predicate} AND m.period = {period} "
            "AND m.metric_name IN ('change_indicator_code', 'change_indicator_name') "
            f"ORDER BY a.spatial_unit_code, m.metric_name LIMIT {limit}"
        )
    raise ValueError(f"허용되지 않은 RAG dimension: {dimension}")


def execute_retrieval_requests(
    query: Callable[[str], list[dict[str, str]]],
    requests: list[dict[str, Any]],
    selected_region: dict[str, str | None],
    industry_code: str,
    quarter: str,
) -> dict[str, Any]:
    """Execute validated tools and return bounded, provenance-bearing rows."""
    if not _QUARTER_RE.fullmatch(quarter):
        raise ValueError("RAG 검색 분기 형식 오류")
    if not _INDUSTRY_RE.fullmatch(industry_code):
        raise ValueError("RAG 검색 업종 코드 오류")
    results: list[dict[str, Any]] = []
    for index, request in enumerate(validate_retrieval_requests(requests), start=1):
        rows: list[dict[str, str]] = []
        for dimension in request["dimensions"]:
            rows.extend(query(_dimension_sql(
                dimension, selected_region, industry_code, quarter, request["limit"],
            )))
        results.append({
            "request_id": f"retrieval-{index}",
            "tool": request["tool"],
            "scope": request["scope"],
            "dimensions": request["dimensions"],
            "reason": request["reason"],
            "row_count": len(rows),
            "rows": rows[: len(request["dimensions"]) * request["limit"]],
        })
    return {
        "mode": "db",
        "requested_count": len(requests),
        "executed_count": len(results),
        "results": results,
    }
