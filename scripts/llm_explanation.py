"""Evidence-grounded explanation generation with a template fallback."""
from __future__ import annotations

import json
import re
from typing import Any

from llm_runtime import LLMConfig, LLMRuntimeError, OpenAICompatibleJsonClient


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
        "claim_type": "descriptive",
        "explanation_mode": "template",
    }


def _candidate_numbers(candidate: dict[str, Any]) -> set[str]:
    serialized = json.dumps(candidate, ensure_ascii=False)
    return set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", serialized))


def validate_card(candidate: dict[str, Any], card: Any) -> tuple[bool, list[str]]:
    if not isinstance(card, dict):
        return False, ["설명 카드가 JSON 객체가 아님"]
    errors: list[str] = []
    if card.get("candidate_id") != candidate.get("candidate_id"):
        errors.append("candidate_id 불일치")
    for key in ("summary", "reasons", "counter_evidence", "missing_features"):
        if key not in card:
            errors.append(f"필수 필드 누락: {key}")
    if not isinstance(card.get("reasons"), list) or not isinstance(card.get("counter_evidence"), list):
        errors.append("근거 필드가 배열이 아님")
    text = json.dumps(card, ensure_ascii=False)
    for phrase in BANNED_PHRASES:
        if phrase in text:
            errors.append(f"금지 확정 표현: {phrase}")
    allowed_numbers = _candidate_numbers(candidate)
    for number in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", text):
        if number not in allowed_numbers:
            errors.append(f"Evidence에 없는 숫자: {number}")
            break
    claim_type = card.get("claim_type", "descriptive")
    if claim_type not in {"descriptive", "associational", "causal"}:
        errors.append("허용되지 않는 claim_type")
    if claim_type == "causal":
        errors.append("현재 파이프라인에서 인과 주장은 자동 허용하지 않음")
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

    system_prompt = (
        "당신은 서울 입지 추천 설명 카드 작성기다. 입력 후보 JSON의 Evidence와 reasons, "
        "counter_evidence, missing_features만 사용해 한국어 JSON을 작성하라. 숫자·분기·공간 단위를 새로 만들지 말라. "
        "성공·수익을 보장하지 말고, 인과관계를 주장하지 말라. 반환 필드는 candidate_id, summary, reasons, "
        "counter_evidence, context_notes, missing_features, claim_type이며 claim_type은 descriptive 또는 associational만 허용한다."
    )
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
