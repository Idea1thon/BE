"""Evidence-grounded explanation generation with a template fallback."""
from __future__ import annotations

import json
import re
from typing import Any

from llm_runtime import (
    LLMConfig,
    LLMRuntimeError,
    OpenAICompatibleJsonClient,
    RECOMMENDATION_LLM_POLICY,
)


BANNED_PHRASES = ("성공 보장", "수익 보장", "정확한 성공률", "무조건", "최적 입지", "확실히 성공")


def template_card(candidate: dict[str, Any]) -> dict[str, Any]:
    tier = candidate.get("fit_tier", "조건부 검토")
    return {
        "candidate_id": candidate.get("candidate_id"),
        "summary": f"{tier} 후보입니다. 관측된 근거와 확인되지 않은 조건을 함께 검토해야 합니다.",
        "reasons": list(candidate.get("reasons") or []),
        "counter_evidence": list(candidate.get("counter_evidence") or []),
        "context_notes": list(candidate.get("context_notes") or []),
        "missing_features": list(candidate.get("missing_features") or []),
        "inference_hypotheses": [],
        "claim_type": "descriptive",
        "explanation_mode": "template",
    }


def _evidence_numbers(candidate: dict[str, Any]) -> set[str]:
    """Return only numeric values that are actually observed in Evidence.

    Candidate metadata also contains years, IDs, coordinates, and counters.
    Treating every number in the serialized candidate as usable made it
    possible for an explanation to attach an unrelated observed number to a
    different metric.
    """
    numbers: set[str] = set()
    for item in candidate.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        for key in ("value", "seoul_percentile"):
            value = item.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numbers.update(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", str(value)))
    return numbers


def _candidate_reference_ids(candidate: dict[str, Any]) -> set[str]:
    """Collect reference IDs and metric names available to an explanation."""
    refs: set[str] = set()
    for item in candidate.get("evidence") or []:
        if isinstance(item, dict):
            for key in ("evidence_id", "metric_name"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    refs.add(value.strip())
    serialized = json.dumps(candidate, ensure_ascii=False)
    refs.update(re.findall(r"FC-[A-Za-z0-9_-]+", serialized))
    return refs


def _validate_inference_hypotheses(raw: Any) -> list[str]:
    """Validate the explicitly unverified channel for LLM-generated analysis."""
    if not isinstance(raw, list):
        return ["inference_hypotheses가 배열이 아님"]
    errors: list[str] = []
    for index, item in enumerate(raw[:12]):
        if not isinstance(item, dict):
            errors.append(f"inference_hypotheses[{index}]가 객체가 아님")
            continue
        claim = item.get("claim")
        if not isinstance(claim, str) or not claim.strip():
            errors.append(f"inference_hypotheses[{index}].claim 누락")
        elif len(claim) > 500:
            errors.append(f"inference_hypotheses[{index}].claim 길이 제한 초과")
        if item.get("status") != "unverified":
            errors.append(f"inference_hypotheses[{index}]는 status=unverified여야 함")
        if item.get("claim_type") not in {"hypothesis", "scenario", "causal_hypothesis", "estimate"}:
            errors.append(f"inference_hypotheses[{index}]의 claim_type 오류")
        if item.get("confidence") not in {"low", "medium", "high"}:
            errors.append(f"inference_hypotheses[{index}]의 confidence 오류")
        refs = item.get("basis_refs")
        if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
            errors.append(f"inference_hypotheses[{index}].basis_refs 오류")
    if len(raw) > 12:
        errors.append("inference_hypotheses 개수 제한 초과")
    return errors


def validate_card(candidate: dict[str, Any], card: Any) -> tuple[bool, list[str]]:
    if not isinstance(card, dict):
        return False, ["설명 카드가 JSON 객체가 아님"]
    errors: list[str] = []
    allowed_keys = {
        "candidate_id", "summary", "reasons", "counter_evidence", "context_notes",
        "missing_features", "inference_hypotheses", "claim_type", "explanation_mode",
    }
    unexpected = sorted(set(card) - allowed_keys)
    if unexpected:
        errors.append(f"허용되지 않은 필드: {', '.join(unexpected)}")
    if card.get("candidate_id") != candidate.get("candidate_id"):
        errors.append("candidate_id 불일치")
    for key in ("summary", "reasons", "counter_evidence", "context_notes", "missing_features"):
        if key not in card:
            errors.append(f"필수 필드 누락: {key}")
    if not isinstance(card.get("summary"), str) or not card.get("summary", "").strip():
        errors.append("summary가 비어 있거나 문자열이 아님")
    for key in ("reasons", "counter_evidence", "context_notes", "missing_features"):
        values = card.get(key)
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            errors.append(f"{key}가 문자열 배열이 아님")
        elif len(values) > 12 or any(len(value) > 500 for value in values):
            errors.append(f"{key}의 길이 제한 초과")
    errors.extend(_validate_inference_hypotheses(card.get("inference_hypotheses")))
    valid_refs = _candidate_reference_ids(candidate)
    for index, item in enumerate(card.get("inference_hypotheses") or []):
        if not isinstance(item, dict):
            continue
        refs = item.get("basis_refs") or []
        for ref in refs:
            if ref not in valid_refs:
                errors.append(f"inference_hypotheses[{index}]의 근거 참조가 후보 Evidence에 없음: {ref}")

    # Numeric and certainty checks apply to observed explanation fields.
    # New estimates are allowed only in the explicitly marked inference
    # channel and never become Evidence or candidate-ranking inputs.
    observed_card = {
        key: value for key, value in card.items()
        if key not in {"candidate_id", "inference_hypotheses"}
    }
    text = json.dumps(observed_card, ensure_ascii=False)
    for phrase in BANNED_PHRASES:
        if phrase in text:
            errors.append(f"금지 확정 표현: {phrase}")
    allowed_numbers = _evidence_numbers(candidate)
    for number in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", text):
        if number not in allowed_numbers:
            errors.append(f"Evidence에 없는 숫자: {number}")
            break
    claim_type = card.get("claim_type", "descriptive")
    if claim_type not in {"descriptive", "associational"}:
        errors.append("허용되지 않는 claim_type")
    return not errors, errors


def explain_candidates(candidates: list[dict[str, Any]], llm_mode: str = "auto") -> dict[str, Any]:
    config = LLMConfig.from_env(llm_mode)
    if llm_mode == "required" and not config.available:
        raise LLMRuntimeError("llm-mode=required지만 LLM_API_URL/LLM_API_KEY/LLM_MODEL 설정이 없습니다.")
    cards: list[dict[str, Any]] = []
    errors: list[str] = []
    llm_used = 0
    client = None
    if config.available:
        try:
            client = OpenAICompatibleJsonClient(config)
        except LLMRuntimeError as exc:
            errors.append(str(exc))

    system_prompt = f"""{RECOMMENDATION_LLM_POLICY}

추가 역할: 검증된 서울 창업 입지 후보를 설명하는 Evidence 기반 설명 카드 작성기다.
후보 JSON의 Evidence, reasons, counter_evidence, context_notes, missing_features만 사용해 한국어 JSON을 작성하라.
각 문장은 관측된 사실 또는 관측된 한계를 설명하는 표현으로만 작성하라.
후보 JSON에 없는 내용을 관측 Evidence처럼 reasons, counter_evidence, context_notes, missing_features에 넣지 말라.
추가 분석이 필요하면 주소·수치·분기·매물·공실률·성공확률·수익률·인과관계도 생성할 수 있지만, 반드시 inference_hypotheses에만 넣고 status=unverified, basis_refs, confidence를 함께 반환하라.
inference_hypotheses의 값은 관측 Evidence, 후보 등급·정렬, 하드 조건으로 사용되지 않는 분석 가설이다.
성공·수익을 보장하는 표현은 관측 필드에 쓰지 말고, 인과관계는 causal_hypothesis로만 표시하라.
반드시 candidate_id, summary, reasons, counter_evidence, context_notes, missing_features, inference_hypotheses, claim_type을 반환하라.
claim_type은 descriptive 또는 associational만 허용한다."""
    for candidate in candidates:
        card = None
        if client:
            try:
                card = client.generate_json(system_prompt, {"candidate_evidence": candidate})
                valid, validation_errors = validate_card(candidate, card)
                if valid:
                    card["explanation_mode"] = "llm"
                    cards.append(card)
                    llm_used += 1
                    continue
                errors.extend(f"{candidate.get('candidate_id')}: {error}" for error in validation_errors)
                if llm_mode == "required":
                    raise LLMRuntimeError(
                        f"LLM 설명 카드 검증 실패({candidate.get('candidate_id')}): "
                        + "; ".join(validation_errors)
                    )
            except LLMRuntimeError as exc:
                if llm_mode == "required":
                    raise
                errors.append(f"{candidate.get('candidate_id')}: {exc}")
        cards.append(template_card(candidate))

    if not candidates:
        mode = "template"
    elif llm_used == len(candidates) and not errors:
        mode = "llm"
    elif llm_used:
        mode = "mixed"
    else:
        mode = "template"
    return {
        "cards": cards,
        "explanation_mode": mode,
        "degraded": mode != "llm",
        "llm": {**config.public_metadata(), "calls_succeeded": llm_used, "candidate_count": len(candidates), "validation_or_runtime_errors": errors[:20]},
    }
