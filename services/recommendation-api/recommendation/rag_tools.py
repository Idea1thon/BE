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
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Callable


ALLOWED_RAG_TOOLS = {"search_region_evidence"}
ALLOWED_RAG_DIMENSIONS = {"sales", "stores", "flow", "change", "rent", "vacancy", "workplace_population"}
MAX_RAG_REQUESTS = 4
MAX_RAG_ROWS_PER_DIMENSION = 20
_QUARTER_RE = re.compile(r"^[0-9]{4}[1-4]$")
MAX_TARGET_AREAS = 50
LEGACY_EVIDENCE_DIMENSION_COUNT = 4
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
        if (not isinstance(item, dict) or not isinstance(item.get("tool"), str)
                or item["tool"] not in ALLOWED_RAG_TOOLS):
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


_DONG_DIMENSIONS = {"sales", "stores", "flow", "change", "workplace_population"}


def _region_code(selected_region: dict, field: str, digits: int) -> str:
    value = str(selected_region.get(field) or "").strip()
    if value and not re.fullmatch(r"[0-9]{%d}" % digits, value):
        raise ValueError(f"RAG 검색 지역 코드 형식 오류: {field}")
    return value


def _dong_codes(selected_region: dict) -> list[str]:
    raw = selected_region.get("admin_dong_codes", [])
    if not isinstance(raw, list) or len(raw) > MAX_TARGET_AREAS:
        raise ValueError("RAG 검색 행정동 코드 목록 형식 오류")
    codes = []
    for value in raw:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}", value):
            raise ValueError("RAG 검색 행정동 코드 형식 오류")
        codes.append(value)
    single = _region_code(selected_region, "admin_dong_code", 8)
    if single:
        codes.append(single)
    codes = list(dict.fromkeys(codes))
    gu = _region_code(selected_region, "sigungu_code", 5)
    if len(codes) > MAX_TARGET_AREAS or (gu and any(not code.startswith(gu) for code in codes)):
        raise ValueError("RAG 검색 행정동 코드 범위 오류")
    return codes


def _area_predicate(selected_region: dict) -> str:
    codes = _dong_codes(selected_region)
    if codes:
        return ("a.spatial_unit_type = 'admin_dong' AND a.spatial_unit_code IN ("
                + ", ".join(map(_sql_literal, codes)) + ")")
    gu = _region_code(selected_region, "sigungu_code", 5)
    if gu and not selected_region.get("dong") and "admin_dong_codes" not in selected_region:
        return f"a.spatial_unit_type = 'commercial_area' AND a.sigungu_code = {_sql_literal(gu)}"
    return "false"


def _dimension_sql(
    dimension: str,
    selected_region: dict[str, Any],
    industry_code: str,
    quarter: str,
    limit: int,
    target_codes: list[str] | None = None,
) -> str:
    predicate = _area_predicate(selected_region)
    if target_codes is not None:
        # The caller already resolved candidates against the selected region.
        # Nullable display metadata/crosswalks must not discard those hosts.
        predicate = ("a.spatial_unit_type = 'commercial_area' AND a.spatial_unit_code IN ("
                     + ", ".join(map(_sql_literal, target_codes)) + ")")
    period = _sql_literal(quarter)
    industry = _sql_literal(industry_code)
    common = (
        "SELECT a.spatial_unit_type, a.spatial_unit_code, "
        "a.spatial_unit_name, a.sigungu_name, "
    )
    if selected_region.get("sigungu_code") or _dong_codes(selected_region) or target_codes is not None:
        common = (
            "SELECT a.spatial_unit_type, a.spatial_unit_code, a.spatial_unit_name, "
            f"coalesce(nullif(a.sigungu_name, ''), {_sql_literal(str(selected_region.get('sigungu') or '').strip())}) AS sigungu_name, "
        )
    if dimension in ("rent", "vacancy"):
        indicator = "임대가격지수" if dimension == "rent" else "공실률"
        return common + (
            f"r.period, NULL::text AS industry_code, '{dimension}' AS dimension, "
            "r.value_numeric::text AS value, 'context.rent_index' AS source_table, "
            "r.rone_area AS source_region, true AS grain_is_proxy, "
            "'R-ONE 조사권역 대리지표이며 개별 건물 월세·공실을 뜻하지 않음' AS limitation, "
            "'latest_available' AS period_policy "
            "FROM location.area a JOIN location.area_crosswalk cw "
            "ON cw.target_area_id = a.area_id "
            "AND cw.relation_type = 'rone_to_commercial_proxy' AND cw.join_eligible "
            # Multiple eligible source regions are ambiguous. Do not choose one
            # arbitrarily or let duplicated targets consume other candidates' cap.
            "AND NOT EXISTS (SELECT 1 FROM location.area_crosswalk other_cw "
            "WHERE other_cw.target_area_id = cw.target_area_id "
            "AND other_cw.relation_type = cw.relation_type AND other_cw.join_eligible "
            "AND other_cw.source_area_id <> cw.source_area_id) "
            "JOIN LATERAL (SELECT ri.period, ri.value_numeric, ri.rone_area "
            "FROM context.rent_index ri WHERE ri.grain = '상권' "
            "AND ri.rone_area = split_part(cw.source_area_id, ':', 2) "
            f"AND ri.store_type = '소규모상가' AND ri.indicator = {_sql_literal(indicator)} "
            "AND ri.period ~ '^[0-9]{4}[1-4]$' "
            "AND ri.value_numeric >= 0 AND ri.value_numeric::text NOT IN ('NaN', 'Infinity') "
            "ORDER BY ri.period DESC LIMIT 1) r ON true "
            f"WHERE {predicate} ORDER BY a.spatial_unit_code, r.rone_area LIMIT {limit}"
        )
    if dimension == "workplace_population":
        return common + (
            "p.period, NULL::text AS industry_code, 'workplace_population' AS dimension, "
            "(p.attributes ->> '총_직장_인구_수') AS value, "
            "'context.population_snapshot' AS source_table, a.spatial_unit_name AS source_region, "
            "false AS grain_is_proxy, '직장인구는 실제 점심 방문량이 아님' AS limitation, "
            "'latest_not_after_requested_quarter' AS period_policy "
            "FROM location.area a JOIN LATERAL (SELECT p.period, p.attributes "
            "FROM context.population_snapshot p WHERE p.dataset = 'worker' "
            "AND p.grain = a.spatial_unit_type AND p.spatial_code = a.spatial_unit_code "
            f"AND p.period <= {period} AND p.period ~ '^[0-9]{{4}}[1-4]$' "
            "AND (p.attributes ->> '총_직장_인구_수') ~ '^[0-9]+([.][0-9]+)?$' "
            "ORDER BY p.period DESC LIMIT 1) p ON true "
            f"WHERE {predicate} ORDER BY a.spatial_unit_code LIMIT {limit}"
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
    selected_region: dict[str, Any],
    industry_code: str,
    quarter: str,
    *,
    target_areas: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute validated tools and return bounded, provenance-bearing rows."""
    if not _QUARTER_RE.fullmatch(quarter):
        raise ValueError("RAG 검색 분기 형식 오류")
    if not _INDUSTRY_RE.fullmatch(industry_code):
        raise ValueError("RAG 검색 업종 코드 오류")
    dong_codes = _dong_codes(selected_region)
    region_resolved = _area_predicate(selected_region) != "false"
    target_codes = None if target_areas is None else list(dict.fromkeys(
        str(area.get("spatial_unit_code")) for area in target_areas[:MAX_TARGET_AREAS]
        if isinstance(area, dict) and area.get("spatial_unit_type") == "commercial_area"
        and re.fullmatch(r"[0-9]{1,20}", str(area.get("spatial_unit_code") or ""))
    ))
    optional_tables = {
        "rent": ("context.rent_index", "location.area_crosswalk"),
        "vacancy": ("context.rent_index", "location.area_crosswalk"),
        "workplace_population": ("context.population_snapshot",),
    }
    table_available: dict[str, bool] = {}
    # A planner may emit overlapping tool requests. Keep the request-level
    # response shape, but avoid issuing the same server-built SQL more than
    # once in this run. The returned rows are read-only at this layer, so a
    # shallow copy prevents an adapter from mutating the memoized list.
    data_query_cache: dict[str, list[dict[str, str]]] = {}
    data_query_cache_hits = 0

    def run_data_query(sql: str) -> list[dict[str, str]]:
        nonlocal data_query_cache_hits
        cached = data_query_cache.get(sql)
        if cached is not None:
            data_query_cache_hits += 1
            return [dict(row) for row in cached]
        rows = query(sql)
        data_query_cache[sql] = [dict(row) for row in rows]
        return rows

    results: list[dict[str, Any]] = []
    for index, request in enumerate(validate_retrieval_requests(requests), start=1):
        rows: list[dict[str, str]] = []
        availability: list[dict[str, Any]] = []
        for dimension in request["dimensions"]:
            # A verified dong has its own observations even without a host area.
            if dong_codes:
                plans = [("commercial_area", target_codes or [])]
                if dimension in _DONG_DIMENSIONS:
                    plans.append(("admin_dong", None))
            else:
                grain = "commercial_area" if target_codes is not None or selected_region.get("sigungu_code") or not selected_region.get("dong") else "admin_dong"
                plans = [(grain, target_codes)]
            for grain, codes in plans:
                state = {"dimension": dimension, "spatial_unit_type": grain,
                         "status": "missing", "reason": "no_rows"}
                try:
                    if codes == []:
                        state["reason"] = "no_target_areas"
                    elif codes is None and not region_resolved:
                        state["reason"] = "region_unresolved"
                    else:
                        for table in optional_tables.get(dimension, ()):
                            if table not in table_available:
                                found = query(f"SELECT to_regclass({_sql_literal(table)}) AS reg")
                                table_available[table] = bool(found and found[0].get("reg"))
                        if any(not table_available[t] for t in optional_tables.get(dimension, ())):
                            state["reason"] = "table_unavailable"
                        else:
                            row_limit = len(codes) if codes is not None else (len(dong_codes) if dong_codes else request["limit"])
                            if dimension == "change" and (codes is not None or dong_codes):
                                row_limit *= 2
                            dimension_rows = run_data_query(_dimension_sql(
                                dimension, selected_region, industry_code, quarter,
                                row_limit, codes,
                            ))[:row_limit]
                            rows.extend(dimension_rows)
                            if dimension_rows:
                                state.update(status="available", reason="rows_returned")
                            state["row_count"] = len(dimension_rows)
                except Exception:
                    # Each grain is independent; never leak connection details.
                    state.update(status="error", reason="query_failed")
                availability.append(state)
        results.append({
            "request_id": f"retrieval-{index}",
            "tool": request["tool"],
            "scope": request["scope"],
            "dimensions": request["dimensions"],
            "reason": request["reason"],
            "availability": availability,
            "target_area_count": len(target_codes) if target_codes is not None else (0 if dong_codes else None),
            "admin_dong_count": len(dong_codes),
            "row_count": len(rows),
            "rows": rows,
        })
    return {
        "mode": "db",
        "requested_count": len(requests),
        "executed_count": len(results),
        "unique_data_query_count": len(data_query_cache),
        "data_query_cache_hits": data_query_cache_hits,
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


_RETRIEVAL_METRICS = {
    # dimension -> (source_table, unit, integer-only)
    "sales": ("location.sales_quarter", "원", False),
    "stores": ("location.store_quarter", "개", True),
    "flow": ("location.flow_quarter", "명", False),
    "rent": ("context.rent_index", "지수", False),
    "vacancy": ("context.rent_index", "%", False),
    "workplace_population": ("context.population_snapshot", "명", True),
    "change_indicator_code": ("context.metric_snapshot", "코드", None),
    "change_indicator_name": ("context.metric_snapshot", "분류", None),
}


def build_retrieval_evidence(context: Any) -> list[dict[str, Any]]:
    """Validate and expose bounded, citable RAG rows.

    Retrieval evidence is kept at its observed spatial grain so question
    comparisons can distinguish candidates. It is never merged into the
    candidate's own evidence or used by deterministic selection. Every row
    carries the source scope and limitation supplied by the server query.
    """
    if not isinstance(context, dict) or not isinstance(context.get("results"), list):
        return []
    metrics = {
        "sales": ("location.sales_quarter", "원"),
        "stores": ("location.store_quarter", "개"),
        "flow": ("location.flow_quarter", "명"),
        "rent": ("context.rent_index", "지수"),
        "vacancy": ("context.rent_index", "%"),
        "workplace_population": ("context.population_snapshot", "명"),
        "change_indicator_code": ("context.metric_snapshot", "코드"),
        "change_indicator_name": ("context.metric_snapshot", "분류"),
    }
    evidence: dict[str, dict[str, Any]] = {}
    for result in context["results"][:MAX_RAG_REQUESTS]:
        if not isinstance(result, dict) or not isinstance(result.get("rows"), list):
            continue
        declared = result.get("dimensions")
        dimensions = set(d for d in declared if isinstance(d, str) and d in ALLOWED_RAG_DIMENSIONS) if isinstance(declared, list) else None
        dimension_count = len(dimensions) if dimensions is not None else LEGACY_EVIDENCE_DIMENSION_COUNT
        row_cap = MAX_RAG_ROWS_PER_DIMENSION * dimension_count
        target_count = result.get("target_area_count")
        if type(target_count) is int and 0 <= target_count <= MAX_TARGET_AREAS and dimensions is not None:
            row_cap = target_count * (dimension_count + int("change" in dimensions))
            dong_count = result.get("admin_dong_count")
            if type(dong_count) is int and 0 <= dong_count <= MAX_TARGET_AREAS:
                row_cap += dong_count * (len(dimensions & _DONG_DIMENSIONS) + int("change" in dimensions))
        for row in result["rows"][:row_cap]:
            if not isinstance(row, dict):
                continue
            dimension = row.get("dimension")
            if not isinstance(dimension, str) or dimension not in _RETRIEVAL_METRICS:
                continue
            source_table, _, _ = _RETRIEVAL_METRICS[dimension]
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
            if not all(names.values()):
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
            source_table, unit = metrics[dimension]
            for metric, raw_value in values.items():
                if dimension.startswith("change_indicator_"):
                    value = str(raw_value).strip() if isinstance(raw_value, str) else None
                    if not value or len(value) > 240:
                        continue
                else:
                    value = _numeric_evidence(raw_value)
                    if value is None or (dimension in ("stores", "workplace_population") and not isinstance(value, int)):
                        continue
                record = {
                    "dimension": metric, "value": value, "unit": unit,
                    "period": period, "industry_code": industry,
                    "spatial_unit_type": spatial_type, **names,
                    "source_table": source_table,
                }
                if dimension in ("rent", "vacancy", "workplace_population"):
                    source_region = str(row.get("source_region") or "").strip()
                    limitation = str(row.get("limitation") or "").strip()
                    proxy = row.get("grain_is_proxy") in (True, "t", "true")
                    if not source_region or not limitation:
                        continue
                    if dimension in ("rent", "vacancy") and not proxy:
                        continue
                    if dimension == "vacancy" and value > 100:
                        continue
                    record.update(source_region=source_region, grain_is_proxy=proxy,
                                  limitation=limitation, period_policy=row.get("period_policy"))
                identity = {key: val for key, val in record.items() if key not in ("value", "unit", "spatial_unit_name", "limitation", "period_policy")}
                digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
                evidence_id = f"retrieval-{digest}"
                evidence[evidence_id] = {"evidence_id": evidence_id, **record}
    return list(evidence.values())
