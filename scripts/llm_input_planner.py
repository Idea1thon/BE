"""Natural-language input interpretation for the recommendation pipeline.

LLM output is a proposal. The returned conditions are normalized and the
caller must still validate the resolved request before querying data.
"""
from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from llm_runtime import (
    LLMConfig,
    LLMRuntimeError,
    OpenAICompatibleJsonClient,
    RECOMMENDATION_LLM_POLICY,
)


SUPPORTED_INDUSTRIES = {f"CS100{i:03d}" for i in range(1, 11)}
INDUSTRY_NAMES = {
    "CS100001": "한식", "CS100002": "중식", "CS100003": "일식", "CS100004": "양식",
    "CS100005": "제과점", "CS100006": "패스트푸드", "CS100007": "치킨", "CS100008": "분식",
    "CS100009": "호프-간이주점", "CS100010": "커피-음료",
}
INDUSTRY_KEYWORDS = {
    "CS100001": ("한식", "국밥", "백반", "김치찌개"),
    "CS100002": ("중식", "짜장", "짬뽕", "중화요리"),
    "CS100003": ("일식", "초밥", "스시", "돈카츠", "라멘"),
    "CS100004": ("양식", "파스타", "이탈리안", "레스토랑"),
    "CS100005": ("제과", "베이커리", "빵집", "디저트"),
    "CS100006": ("패스트푸드", "햄버거", "버거"),
    "CS100007": ("치킨", "닭강정"),
    "CS100008": ("분식", "떡볶이", "김밥"),
    "CS100009": ("호프", "주점", "술집", "펍"),
    "CS100010": ("커피", "카페", "음료", "커피숍"),
}
ALLOWED_CONDITION_KEYS = {
    "monthly_rent_max_krw", "deposit_max_krw", "store_area_min_m2", "store_area_max_m2",
    "parking_required", "target_customer", "operating_hours", "business_mode",
}
ALLOWED_PLAN_TOOLS = {
    "feature_store.lookup", "candidate_engine.select_candidates",
    "evidence_builder.build", "quality_gate.validate",
}
MAX_NUMERIC_DIGITS = 15


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in items if str(item).strip()))


def parse_amount(text: str) -> int | None:
    match = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(억|만)?", text)
    if not match:
        return None
    raw = match.group(1).replace(",", "")
    if len(raw.replace(".", "")) > MAX_NUMERIC_DIGITS:
        return None
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    if match.group(2) == "억":
        amount *= Decimal(100_000_000)
    elif match.group(2) == "만":
        amount *= Decimal(10_000)
    try:
        return int(amount)
    except (OverflowError, ValueError):
        return None


def parse_conditions(text: str) -> dict[str, Any]:
    """Deterministic minimum parser used as a fallback and a validation baseline."""
    text = text or ""
    monthly_match = re.search(r"월세\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(억|만)?", text)
    deposit_match = re.search(r"(?:보증금|전세금)\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(억|만)?", text)
    monthly = parse_amount(monthly_match.group(0)) if monthly_match else None
    deposit = parse_amount(deposit_match.group(0)) if deposit_match else None

    area_min = area_max = None
    area_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(평|㎡|m2|제곱미터)", text, re.IGNORECASE)
    if area_match:
        raw = area_match.group(1)
        if len(raw.replace(".", "")) <= MAX_NUMERIC_DIGITS:
            try:
                raw_area = Decimal(raw)
                area_m2 = raw_area * Decimal("3.3058") if area_match.group(2) == "평" else raw_area
                if area_m2.is_finite() and abs(area_m2) <= Decimal("1e15"):
                    nearby = text[area_match.start(): area_match.end() + 8]
                    area_value = round(float(area_m2), 2)
                    if re.search(r"이상|부터|최소", nearby):
                        area_min = area_value
                    elif re.search(r"이하|까지|미만", nearby):
                        area_max = area_value
                    else:
                        area_min = area_value
            except (InvalidOperation, OverflowError, ValueError):
                pass

    parking = bool(re.search(r"주차\s*(가능|필수|필요|있|확보)", text))
    customer_terms = ("직장인", "학생", "관광객", "주민", "가족", "외국인", "아침 손님", "1인 가구")
    target_customer = [term for term in customer_terms if term in text]
    hour = None
    if re.search(r"아침|출근|오전|모닝", text):
        hour = "morning"
    elif re.search(r"점심|낮", text):
        hour = "afternoon"
    elif re.search(r"저녁|퇴근", text):
        hour = "evening"
    elif re.search(r"밤|야간|새벽", text):
        hour = "night"
    business_mode = None
    if "배달" in text and re.search(r"매장|홀|식사", text):
        business_mode = "mixed"
    elif "배달" in text:
        business_mode = "delivery"
    elif re.search(r"매장|홀|식사", text):
        business_mode = "dine_in"

    unsupported: list[str] = []
    if monthly is not None:
        unsupported.append("monthly_rent_max_krw: 개별 매물 월세 데이터 없음")
    if deposit is not None:
        unsupported.append("deposit_max_krw: 개별 매물 보증금 데이터 없음")
    if area_min is not None or area_max is not None:
        unsupported.append("store_area_m2: 개별 매물 면적 데이터 없음")
    if parking:
        unsupported.append("parking_required: 개별 매물 주차 데이터 없음")
    return {
        "monthly_rent_max_krw": monthly,
        "deposit_max_krw": deposit,
        "store_area_min_m2": area_min,
        "store_area_max_m2": area_max,
        "parking_required": parking,
        "target_customer": target_customer,
        "operating_hours": hour,
        "business_mode": business_mode,
        "unsupported_conditions": unsupported,
    }


def _industry_candidates(text: str, explicit_code: str | None) -> list[dict[str, Any]]:
    if explicit_code:
        return [{"industry_code": explicit_code, "name": INDUSTRY_NAMES[explicit_code], "confidence": 1.0, "source": "ui"}]
    found = []
    for code, keywords in INDUSTRY_KEYWORDS.items():
        hits = [keyword for keyword in keywords if keyword in text]
        if hits:
            found.append({"industry_code": code, "name": INDUSTRY_NAMES[code], "confidence": round(min(0.95, 0.65 + 0.1 * len(hits)), 2), "matched_keywords": hits, "source": "fallback"})
    return found


def _default_plan(industry_code: str | None, conditions: dict[str, Any]) -> list[dict[str, Any]]:
    dimensions = ["현재수요", "경쟁·시장수용", "진입건전성", "비용부담", "수요구성", "미래신호", "데이터신뢰도"]
    return [
        {"tool": "feature_store.lookup", "purpose": "선택 지역·업종·분기별 검증된 피처 조회", "read_only": True, "dimensions": dimensions},
        {"tool": "candidate_engine.select_candidates", "purpose": "경계·seed·하드조건 기준 후보 생성 및 판정", "read_only": True, "industry_code": industry_code, "condition_keys": sorted(k for k in conditions if k in ALLOWED_CONDITION_KEYS)},
        {"tool": "evidence_builder.build", "purpose": "후보별 출처·기간·공간 단위·반대 근거가 있는 Evidence 조립", "read_only": True},
        {"tool": "quality_gate.validate", "purpose": "스키마·grain·출처·주장 유형 검증", "read_only": True},
    ]


def _normalize_remote_conditions(raw: Any, baseline: dict[str, Any]) -> dict[str, Any]:
    """Accept only LLM values that match facts parsed from the user text.

    The LLM may normalize a value (for example ``20평`` to m²), but it may
    not introduce a new budget, area, customer group, or operating mode. The
    deterministic parser is the source of truth for recommendation filters.
    """
    conditions = dict(baseline)
    if not isinstance(raw, dict):
        return conditions
    for key, value in raw.items():
        if key not in ALLOWED_CONDITION_KEYS or value is None:
            continue
        if key in {"monthly_rent_max_krw", "deposit_max_krw"}:
            if isinstance(value, (int, float)):
                if isinstance(value, float) and not math.isfinite(value):
                    continue
                try:
                    normalized = int(value)
                except (OverflowError, ValueError):
                    continue
            elif isinstance(value, str) and re.fullmatch(r"\s*[0-9][0-9,]*(?:\.[0-9]+)?\s*(?:억|만)?\s*", value):
                normalized = parse_amount(value)
            else:
                continue
            if normalized == baseline.get(key):
                conditions[key] = baseline[key]
        elif key in {"store_area_min_m2", "store_area_max_m2"}:
            if isinstance(value, (int, float)):
                if isinstance(value, float) and not math.isfinite(value):
                    continue
                normalized = round(float(value), 2)
            elif isinstance(value, str):
                match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(평|㎡|m2|제곱미터)?\s*", value, re.IGNORECASE)
                if not match:
                    continue
                if len(match.group(1).replace(".", "")) > MAX_NUMERIC_DIGITS:
                    continue
                try:
                    area = float(match.group(1)) * (3.3058 if match.group(2) == "평" else 1)
                except (OverflowError, ValueError):
                    continue
                if not math.isfinite(area) or abs(area) > 1e15:
                    continue
                normalized = round(area, 2)
            else:
                continue
            if normalized == baseline.get(key):
                conditions[key] = baseline[key]
        elif key == "parking_required":
            if isinstance(value, bool):
                normalized = value
            elif isinstance(value, str) and value.strip().lower() in {"true", "false"}:
                normalized = value.strip().lower() == "true"
            else:
                continue
            if normalized == bool(baseline.get(key)):
                conditions[key] = baseline[key]
        elif key == "target_customer":
            if isinstance(value, list):
                normalized = [str(x).strip() for x in value if str(x).strip()]
            else:
                normalized = [str(value).strip()]
            conditions[key] = [x for x in baseline.get(key, []) if x in normalized]
        elif key in {"operating_hours", "business_mode"}:
            normalized = str(value).strip()
            if normalized == baseline.get(key):
                conditions[key] = baseline[key]
    return conditions


def _valid_plan(raw: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return fallback
    output = []
    for item in raw[:8]:
        if not isinstance(item, dict) or item.get("tool") not in ALLOWED_PLAN_TOOLS:
            continue
        output.append({
            "tool": item["tool"],
            "purpose": str(item.get("purpose") or "").strip()[:240],
            "read_only": True,
            **({"dimensions": item["dimensions"]} if isinstance(item.get("dimensions"), list) else {}),
        })
    return output or fallback


def _valid_inference_hypotheses(raw: Any) -> list[dict[str, Any]]:
    """Keep LLM-generated planning hypotheses separate from input filters."""
    if not isinstance(raw, list):
        return []
    output: list[dict[str, Any]] = []
    for item in raw[:12]:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()[:500]
        claim_type = item.get("claim_type")
        confidence = item.get("confidence")
        refs = item.get("basis_refs")
        if not claim or claim_type not in {"hypothesis", "scenario", "causal_hypothesis", "estimate"}:
            continue
        output.append({
            "claim": claim,
            "status": "unverified",
            "claim_type": claim_type,
            "basis_refs": [str(ref)[:120] for ref in refs[:8] if str(ref).strip()] if isinstance(refs, list) else [],
            "confidence": confidence if confidence in {"low", "medium", "high"} else "low",
        })
    return output


def plan_input(
    selected_region: dict[str, str | None],
    raw_user_text: str,
    explicit_industry_code: str | None = None,
    llm_mode: str = "auto",
) -> dict[str, Any]:
    """Return a validated input proposal; no data query is performed here."""
    text = raw_user_text or ""
    baseline_conditions = parse_conditions(text)
    fallback_candidates = _industry_candidates(text, explicit_industry_code)
    fallback_plan = _default_plan(explicit_industry_code or (fallback_candidates[0]["industry_code"] if len(fallback_candidates) == 1 else None), baseline_conditions)
    config = LLMConfig.from_env(llm_mode)
    planner_mode = "deterministic_fallback"
    remote_error = None
    remote: dict[str, Any] | None = None

    if config.available and text:
        prompt = f"""{RECOMMENDATION_LLM_POLICY}

추가 역할: 사용자 자연어의 업종·특별조건을 구조화하고, 읽기 전용 분석 계획 초안을 만드는 입력 계약 분석기다.
선택 지역은 절대 변경하지 말고, 자유 텍스트에 실제로 표현된 값만 반환하라.
업종은 CS100001~CS100010 중에서만 고르며, 업종을 확정할 수 없으면 후보를 억지로 하나로 만들지 말고 확인 질문을 반환하라.
숫자는 원화 또는 m²로 정규화할 수 있다. 원문에 없는 값은 조건 필드로 확정하지 말고, 필요하면 별도 분석 가설로 표시하라.
매물·공실·성공확률·미래 결과는 관측 사실이 아니라면 추정·가설·시나리오로 명시하라.
analysis_plan의 tool은 허용된 읽기 전용 도구만 사용하라.
추가 분석 가설이 필요하면 inference_hypotheses에만 넣고 status=unverified를 사용하라.
반환 형식은 payload의 output_shape를 따르는 JSON 객체 하나다."""
        payload = {
            "selected_region": selected_region,
            "explicit_industry_code": explicit_industry_code,
            "raw_user_text": text,
            "allowed_industries": INDUSTRY_NAMES,
            "allowed_condition_keys": sorted(ALLOWED_CONDITION_KEYS),
            "allowed_tools": sorted(ALLOWED_PLAN_TOOLS),
            "output_shape": {"industry_candidates": [], "conditions": {}, "clarification_questions": [], "unsupported_conditions": [], "analysis_plan": [], "inference_hypotheses": []},
        }
        try:
            remote = OpenAICompatibleJsonClient(config).generate_json(prompt, payload)
            planner_mode = "llm"
        except LLMRuntimeError as exc:
            remote_error = str(exc)
            if llm_mode == "required":
                raise

    candidates = fallback_candidates
    if remote:
        raw_candidates = remote.get("industry_candidates")
        parsed_candidates = []
        if isinstance(raw_candidates, list):
            for item in raw_candidates[:10]:
                if isinstance(item, str) and item in SUPPORTED_INDUSTRIES:
                    parsed_candidates.append({"industry_code": item, "name": INDUSTRY_NAMES[item], "confidence": None, "source": "llm"})
                elif isinstance(item, dict) and item.get("industry_code") in SUPPORTED_INDUSTRIES:
                    parsed_candidates.append({
                        "industry_code": item["industry_code"], "name": INDUSTRY_NAMES[item["industry_code"]],
                        "confidence": item.get("confidence"), "source": "llm",
                    })
        # A UI-selected industry is authoritative. Even a confident-looking
        # remote answer cannot overwrite it.
        if not explicit_industry_code and parsed_candidates:
            # When the user text contains an industry signal, the LLM may
            # normalize or rank it, but it cannot introduce a different
            # industry. With no deterministic signal, keep the request
            # unresolved and ask the user to confirm instead of guessing.
            fallback_codes = {item["industry_code"] for item in fallback_candidates}
            candidates = [item for item in parsed_candidates if item["industry_code"] in fallback_codes]
            if not candidates and not fallback_codes:
                candidates = []
            elif not candidates:
                candidates = fallback_candidates
        conditions = _normalize_remote_conditions(remote.get("conditions"), baseline_conditions)
        remote_questions = remote.get("clarification_questions")
        questions = [str(x) for x in remote_questions] if isinstance(remote_questions, list) else []
        remote_unsupported = remote.get("unsupported_conditions")
        unsupported = _dedupe(baseline_conditions["unsupported_conditions"] + ([str(x) for x in remote_unsupported] if isinstance(remote_unsupported, list) else []))
        conditions["unsupported_conditions"] = unsupported
        plan = _valid_plan(remote.get("analysis_plan"), fallback_plan)
        inference_hypotheses = _valid_inference_hypotheses(remote.get("inference_hypotheses"))
        parse_confidence = str(remote.get("parse_confidence") or ("high" if len(candidates) == 1 else "low"))
    else:
        conditions = baseline_conditions
        questions = []
        plan = fallback_plan
        inference_hypotheses = []
        parse_confidence = "high" if explicit_industry_code or len(candidates) == 1 else "low"

    if not explicit_industry_code and len(candidates) != 1:
        questions.insert(0, "창업하려는 업종을 10개 업종 중 하나로 선택해 주세요.")
    resolved = explicit_industry_code or (candidates[0]["industry_code"] if len(candidates) == 1 else None)
    if conditions.get("store_area_min_m2") is not None and conditions.get("store_area_max_m2") is not None and conditions["store_area_min_m2"] > conditions["store_area_max_m2"]:
        questions.append("매장 면적의 최소값과 최대값이 충돌합니다. 조건을 확인해 주세요.")

    return {
        "selected_region": selected_region,
        "raw_user_text": text,
        "industry_candidates": candidates,
        "resolved_industry_code": resolved,
        "conditions": conditions,
        "parse_confidence": parse_confidence if parse_confidence in {"high", "medium", "low"} else "medium",
        "clarification_questions": _dedupe(questions),
        "confirmation_required": bool(questions),
        "analysis_plan": plan,
        "inference_hypotheses": inference_hypotheses,
        "planner": {
            **config.public_metadata(),
            "stage": "input_interpretation_and_analysis_plan",
            "execution": planner_mode,
            "fallback_used": planner_mode != "llm",
            "error": remote_error,
        },
    }
