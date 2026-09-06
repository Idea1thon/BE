"""Allowlisted, read-only retrieval tools for the recommendation RAG.

The LLM can request a tool and dimensions, but it never supplies SQL, table
names, joins, region values, industry codes, or periods.  The server binds
those values and builds the small set of queries below.
"""
from __future__ import annotations

import math
import re
import hashlib
import json
from decimal import Decimal, InvalidOperation
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


def _area_predicate(selected_region: dict[str, str | None], dong_codes: list[str] | None = None) -> str:
    """행정동은 파이프라인이 resolve_region 으로 이미 확정한 코드로 건다.

    location.area 의 admin_dong 행은 sigungu_name 이 비어 있어 이름+시군구
    매칭이 0행이 됐다. 확정 코드를 받으면 그것으로만 필터하고(법정동 별칭도
    이 단계에서 이미 풀려 있다), 못 받으면 이름만으로 폴백한다.
    """
    sigungu = str(selected_region.get("sigungu") or "").strip()
    codes = sorted({str(code).strip() for code in (dong_codes or []) if str(code).strip()})
    if codes:
        joined = ", ".join(_sql_literal(code) for code in codes)
        return f"a.spatial_unit_type = 'admin_dong' AND a.spatial_unit_code IN ({joined})"
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
    dong_codes: list[str] | None = None,
) -> str:
    predicate = _area_predicate(selected_region, dong_codes)
    period = _sql_literal(quarter)
    industry = _sql_literal(industry_code)
    # admin_dong 행은 sigungu_name 이 비어 있으므로 선택 시군구 이름으로 채운다.
    sigungu_expr = f"coalesce(nullif(a.sigungu_name, ''), {_sql_literal(str(selected_region.get('sigungu') or '').strip())}) AS sigungu_name"
    common = (
        "SELECT a.spatial_unit_type, a.spatial_unit_code, "
        f"a.spatial_unit_name, {sigungu_expr}, "
    )
    if dimension == "sales":
        return common + (
            "s.period, s.industry_code, 'sales' AS dimension, s.sales_amount::text AS value, "
            "'location.sales_quarter' AS source_table "
            "FROM location.area a "
            "JOIN location.sales_quarter s ON s.spatial_unit_code = a.spatial_unit_code "
            "AND s.spatial_unit_type = a.spatial_unit_type "
            f"WHERE {predicate} AND s.period = {period} AND s.industry_code = {industry} "
            f"ORDER BY a.spatial_unit_code LIMIT {limit}"
        )
    if dimension == "stores":
        return common + (
            "s.period, s.industry_code, 'stores' AS dimension, json_build_object("
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
            "f.period, NULL::text AS industry_code, 'flow' AS dimension, f.flow_total::text AS value, "
            "'location.flow_quarter' AS source_table "
            "FROM location.area a "
            "JOIN location.flow_quarter f ON f.spatial_unit_code = a.spatial_unit_code "
            "AND f.spatial_unit_type = a.spatial_unit_type "
            f"WHERE {predicate} AND f.period = {period} "
            f"ORDER BY a.spatial_unit_code LIMIT {limit}"
        )
    if dimension == "change":
        return common + (
            "m.period, NULL::text AS industry_code, m.metric_name AS dimension, "
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
    dong_codes: list[str] | None = None,
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
                dimension, selected_region, industry_code, quarter, request["limit"], dong_codes,
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


def _numeric_evidence(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    if number == number.to_integral_value():
        # Bound malformed inputs before converting arbitrarily large exponents.
        return int(number) if number.adjusted() < 100 else None
    result = float(number)
    return result if math.isfinite(result) else None


def build_retrieval_evidence(context: Any) -> list[dict[str, Any]]:
    """Expose validated regional facts for explanation, never candidate scoring.

    Every record carries its own source, spatial scope, period, industry and
    unit. Unknown or missing values are omitted rather than interpreted as zero.
    IDs depend on the source coordinates and metric, not retrieval ordering.
    """
    if not isinstance(context, dict) or not isinstance(context.get("results"), list):
        return []
    metrics = {
        "sales": ("location.sales_quarter", "원"),
        "stores": ("location.store_quarter", "개"),
        "flow": ("location.flow_quarter", "명"),
        "change_indicator_code": ("context.metric_snapshot", "코드"),
        "change_indicator_name": ("context.metric_snapshot", "분류"),
    }
    evidence: dict[str, dict[str, Any]] = {}
    for result in context["results"][:MAX_RAG_REQUESTS]:
        if not isinstance(result, dict) or not isinstance(result.get("rows"), list):
            continue
        for row in result["rows"][:MAX_RAG_ROWS_PER_DIMENSION * len(ALLOWED_RAG_DIMENSIONS)]:
            if not isinstance(row, dict):
                continue
            dimension = row.get("dimension")
            if not isinstance(dimension, str) or dimension not in metrics:
                continue
            source_table, unit = metrics[dimension]
            if row.get("source_table") != source_table:
                continue
            period = str(row.get("period") or "")
            if not _QUARTER_RE.fullmatch(period):
                continue
            spatial_type = row.get("spatial_unit_type")
            if spatial_type not in ("admin_dong", "commercial_area"):
                continue
            names = {key: str(row.get(key) or "").strip() for key in (
                "spatial_unit_code", "spatial_unit_name", "sigungu_name",
            )}
            # sigungu_name 은 admin_dong 행에서 비어 있을 수 있다(_dimension_sql 이
            # 선택 시군구로 채우지만 방어적으로 허용). code·name 은 필수.
            if not names["spatial_unit_code"] or not names["spatial_unit_name"]:
                continue
            industry = row.get("industry_code") if dimension in ("sales", "stores") else None
            if dimension in ("sales", "stores") and (
                not isinstance(industry, str) or not _INDUSTRY_RE.fullmatch(industry)
            ):
                continue
            values = {dimension: row.get("value")}
            if dimension == "stores":
                try:
                    values = json.loads(row["value"]) if isinstance(row.get("value"), str) else row.get("value")
                except (ValueError, TypeError):
                    continue
                if not isinstance(values, dict):
                    continue
                values = {key: values.get(key) for key in ("total_store_count", "franchise_store_count")}
            for metric, raw_value in values.items():
                if dimension.startswith("change_indicator_"):
                    value = str(raw_value).strip() if isinstance(raw_value, str) else None
                    if not value or len(value) > 240:
                        continue
                else:
                    value = _numeric_evidence(raw_value)
                    if value is None or (dimension == "stores" and not isinstance(value, int)):
                        continue
                record = {
                    "dimension": metric, "value": value, "unit": unit,
                    "period": period, "industry_code": industry,
                    "spatial_unit_type": spatial_type, **names,
                    "source_table": source_table,
                }
                identity = {key: val for key, val in record.items() if key not in ("value", "unit", "spatial_unit_name")}
                digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
                evidence_id = f"retrieval-{digest}"
                evidence[evidence_id] = {"evidence_id": evidence_id, **record}
    return list(evidence.values())
