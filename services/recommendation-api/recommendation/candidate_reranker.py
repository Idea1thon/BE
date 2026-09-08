"""Deterministic candidate safety filtering and LLM evidence reranking.

The deterministic stage intentionally does not decide a recommendation tier.
It only removes candidates that cannot be safely reasoned about and bounds the
amount of evidence sent to the model. The model owns ordering and fit-tier
judgement for the remaining candidates.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from .llm_runtime import (
    LLMConfig,
    LLMRuntimeError,
    OpenAICompatibleJsonClient,
    RECOMMENDATION_LLM_POLICY,
    llm_stage,
)


FIT_TIERS = ("추천", "조건부 검토", "주의")
DEFAULT_TOP_K = 5
MIN_PREFILTER = 10
MAX_PREFILTER = 20


def prefilter_size(top_k: int = DEFAULT_TOP_K) -> int:
    """Return the bounded deterministic search window for a requested top-k."""
    return min(MAX_PREFILTER, max(MIN_PREFILTER, max(1, int(top_k)) * 3))


def _confidence_rank(candidate: dict[str, Any]) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(
        str((candidate.get("data_confidence") or {}).get("level")), 3
    )


def _hard_constraint_status(candidate: dict[str, Any]) -> int:
    """Only identify explicit blockers; do not infer quality from a score."""
    missing = candidate.get("missing_features") or []
    for item in missing:
        text = item if isinstance(item, str) else jsonish(item)
        if re.search(r"unsupported\.|하드|필수|조건 미충족", text, re.IGNORECASE):
            return 1
    return 0


def jsonish(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key}={item}" for key, item in value.items())
    return str(value or "")


def _prefilter_key(candidate: dict[str, Any]) -> tuple:
    # Stable data-safety ordering. Fit tier, reason count and any weighted
    # recommendation score are deliberately absent from this key.
    return (
        _hard_constraint_status(candidate),
        _confidence_rank(candidate),
        len(candidate.get("missing_features") or []),
        len(candidate.get("counter_evidence") or []),
        str(candidate.get("candidate_id") or ""),
    )


def prefilter_candidates(
    candidates: list[dict[str, Any]], *, top_k: int = DEFAULT_TOP_K,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Bound seeds to 10~20 candidates using only safety/data-quality signals."""
    window = prefilter_size(top_k)
    ordered = sorted(candidates, key=_prefilter_key)
    selected = ordered[:window]
    return selected, {
        "mode": "deterministic_prefilter",
        "input_count": len(candidates),
        "prefilter_count": len(selected),
        "prefilter_limit": window,
        "ordering_basis": [
            "hard_constraint_safety",
            "data_confidence",
            "missing_feature_count",
            "counter_evidence_count",
            "candidate_id",
        ],
    }


def _evidence_payload(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for index, item in enumerate(candidate.get("evidence") or []):
        if not isinstance(item, dict):
            continue
        output.append({
            "evidence_ref": f"{candidate.get('candidate_id')}::evidence:{index}",
            "metric_name": item.get("metric_name"),
            "value": item.get("value"),
            "unit": item.get("unit"),
            "period": item.get("period") or item.get("observed_end_period"),
            "spatial_grain": item.get("spatial_grain"),
            "grain_is_proxy": item.get("grain_is_proxy"),
            "limitation": item.get("limitation"),
        })
    return output[:80]


def candidate_reasoning_payload(
    candidates: list[dict[str, Any]], retrieval_evidence: list[dict[str, Any]],
    query_context: dict[str, Any],
) -> dict[str, Any]:
    """Build the model input without deterministic tier/recommendation labels."""
    candidate_payload = []
    allowed_refs = set()
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id"))
        evidence = _evidence_payload(candidate)
        allowed_refs.update(item["evidence_ref"] for item in evidence)
        candidate_payload.append({
            "candidate_id": candidate_id,
            "candidate_type": candidate.get("candidate_type"),
            "location": {
                "place_name": (candidate.get("location") or {}).get("place_name"),
                "anchor": (candidate.get("location") or {}).get("anchor"),
                "host_commercial_area": (candidate.get("location") or {}).get("host_commercial_area"),
            },
            "hard_constraints": {
                "missing_features": copy.deepcopy(candidate.get("missing_features") or []),
                "greenfield": candidate.get("greenfield"),
            },
            "raw_signals": {
                "data_confidence": copy.deepcopy(candidate.get("data_confidence") or {}),
                "feature_coverage": copy.deepcopy((candidate.get("feature_build") or {}).get("coverage") or {}),
                "evidence": evidence,
            },
        })
    regional_refs = []
    for item in retrieval_evidence:
        if not isinstance(item, dict) or not isinstance(item.get("evidence_id"), str):
            continue
        regional_refs.append({
            "evidence_id": item["evidence_id"],
            "dimension": item.get("dimension"),
            "value": item.get("value"),
            "unit": item.get("unit"),
            "period": item.get("period"),
            "spatial_unit_type": item.get("spatial_unit_type"),
            "spatial_unit_code": item.get("spatial_unit_code"),
            "limitation": item.get("limitation"),
        })
        allowed_refs.add(item["evidence_id"])
    return {
        "query_context": {
            "selected_region": query_context.get("selected_region"),
            "industry_code": query_context.get("industry_code"),
            "question_contract": query_context.get("question_contract"),
        },
        "candidates": candidate_payload,
        "regional_retrieval_evidence": regional_refs,
        "allowed_evidence_refs": sorted(allowed_refs),
    }


def _stable_fallback(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return [copy.deepcopy(candidate) for candidate in candidates[:limit]]


def rerank_candidates(
    candidates: list[dict[str, Any]], *, retrieval_evidence: list[dict[str, Any]],
    query_context: dict[str, Any], limit: int | None, llm_mode: str = "auto",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rerank the prefiltered candidates and validate every model-selected ID."""
    final_limit = max(1, int(limit or DEFAULT_TOP_K))
    config = LLMConfig.from_env(llm_mode)
    base = {
        "mode": "deterministic_prefilter",
        "reasoning_status": "not_attempted",
        "grounding_status": "not_attempted",
        "input_count": len(candidates),
        "selected_count": min(final_limit, len(candidates)),
        "ranked_candidate_ids": [],
        "fallback_reason": None,
        "llm": config.public_metadata(),
    }
    if not candidates:
        base.update(reasoning_status="no_candidates", grounding_status="no_evidence")
        return [], base
    if not config.available:
        if llm_mode == "required":
            raise LLMRuntimeError("llm-mode=required지만 LLM_API_URL/LLM_API_KEY/LLM_MODEL 설정이 없습니다.")
        base.update(
            reasoning_status="unavailable",
            grounding_status="not_attempted",
            fallback_reason="llm_not_configured",
            ranked_candidate_ids=[str(item.get("candidate_id")) for item in candidates[:final_limit]],
        )
        return _stable_fallback(candidates, final_limit), base

    try:
        client = OpenAICompatibleJsonClient(config)
        with llm_stage("candidate_rerank"):
            reply = client.generate_json(
                f"""{RECOMMENDATION_LLM_POLICY}

추가 역할: 선택 지역과 업종의 후보를 비교하는 근거 기반 reranker다.
결정론적 tier, 기존 순위, reason 문장을 믿고 복사하지 말고 raw_signals와 regional_retrieval_evidence를 비교하라.
hard constraint를 위반한 후보는 추천하지 말고, 상충하는 근거와 데이터 한계를 반영해 fit_tier를 판단하라.
반드시 입력 후보 ID만 사용하고, 각 결과의 evidence_refs에는 입력의 allowed_evidence_refs에 있는 값만 넣어라.
모델이 근거를 충분히 확인할 수 없으면 조건부 검토 또는 주의로 낮추고 evidence_refs를 비워 두지 말라.
JSON 형식은 {{"ranked_candidates":[{{"candidate_id":"...","rank":1,"fit_tier":"추천|조건부 검토|주의","evidence_refs":["..."]}}]}} 하나만 반환하라.""",
                candidate_reasoning_payload(candidates, retrieval_evidence, query_context),
            )
    except LLMRuntimeError as exc:
        if llm_mode == "required":
            raise
        base.update(reasoning_status="runtime_error", grounding_status="not_attempted", fallback_reason=str(exc))
        return _stable_fallback(candidates, final_limit), base

    rows = reply.get("ranked_candidates") if isinstance(reply, dict) else None
    if not isinstance(rows, list):
        error = "LLM rerank 응답에 ranked_candidates 배열이 없습니다."
        if llm_mode == "required":
            raise LLMRuntimeError(error)
        base.update(reasoning_status="invalid_response", grounding_status="not_attempted", fallback_reason=error)
        return _stable_fallback(candidates, final_limit), base

    by_id = {str(candidate.get("candidate_id")): candidate for candidate in candidates}
    allowed_refs = set(candidate_reasoning_payload(candidates, retrieval_evidence, query_context)["allowed_evidence_refs"])
    accepted: list[tuple[int, int, str, dict[str, Any]]] = []
    seen: set[str] = set()
    grounded_count = 0
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        candidate_id = row.get("candidate_id")
        tier = row.get("fit_tier")
        if not isinstance(candidate_id, str) or candidate_id not in by_id or candidate_id in seen:
            continue
        if tier not in FIT_TIERS:
            continue
        refs = row.get("evidence_refs")
        refs = refs if isinstance(refs, list) else []
        valid_refs = [ref for ref in refs if isinstance(ref, str) and ref in allowed_refs]
        if valid_refs:
            grounded_count += 1
        raw_rank = row.get("rank")
        rank = raw_rank if isinstance(raw_rank, int) and raw_rank > 0 else position + 1
        accepted.append((rank, position, candidate_id, {"fit_tier": tier, "evidence_refs": valid_refs}))
        seen.add(candidate_id)
    accepted.sort(key=lambda item: (item[0], item[1], item[2]))
    ordered_ids = [item[2] for item in accepted]
    ordered_ids.extend(str(candidate.get("candidate_id")) for candidate in candidates if str(candidate.get("candidate_id")) not in seen)
    selected: list[dict[str, Any]] = []
    tier_by_id = {item[2]: item[3]["fit_tier"] for item in accepted}
    for candidate_id in ordered_ids[:final_limit]:
        candidate = copy.deepcopy(by_id[candidate_id])
        if candidate_id in tier_by_id:
            candidate["fit_tier"] = tier_by_id[candidate_id]
        selected.append(candidate)
    if not accepted:
        error = "LLM rerank 응답에 유효한 후보가 없습니다."
        if llm_mode == "required":
            raise LLMRuntimeError(error)
        base.update(reasoning_status="invalid_response", grounding_status="not_attempted", fallback_reason=error)
        return _stable_fallback(candidates, final_limit), base
    base.update(
        mode="llm_rerank",
        reasoning_status="grounded" if grounded_count else "unverified",
        grounding_status="grounded" if grounded_count == len(accepted) else "partial",
        selected_count=len(selected),
        ranked_candidate_ids=[str(item.get("candidate_id")) for item in selected],
        accepted_count=len(accepted),
        omitted_count=max(0, len(candidates) - len(accepted)),
        grounded_candidate_count=grounded_count,
    )
    return selected, base
