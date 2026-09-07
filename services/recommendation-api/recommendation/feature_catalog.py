"""Shared feature and metric metadata for intent matching and evidence routing.

The catalog is deliberately descriptive.  It does not assign a score, choose
an interpretation, or turn a preference into a hard candidate condition.  It
only gives the question-contract and evidence-routing layers one vocabulary
for connecting natural language to already-produced structured evidence.
"""
from __future__ import annotations

import json
import re
from typing import Any


FEATURE_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "feature_id": "FC-01", "label": "유동밀도", "topic_id": "flow",
        "analysis_topics": ("demand", "population"),
        "metric_names": ("유동밀도",),
        "aliases": ("유동인구", "유동량", "사람이 많이 다니", "유동이 많은"),
        "preference_types": ("demand_profile", "flow_presence"),
        "preference_values": ("flow", "pedestrian"),
    },
    {
        "feature_id": "FC-03", "label": "상주인구", "topic_id": "population",
        "analysis_topics": ("demand", "population"),
        "metric_names": ("총_상주인구_수",),
        "aliases": ("상주인구", "주민", "거주자", "사는 사람이", "주거 인구"),
        "preference_types": ("resident_presence",),
        "preference_values": ("resident",),
    },
    {
        "feature_id": "FC-04", "label": "직장인구", "topic_id": "jobs",
        "analysis_topics": ("demand", "population"),
        "metric_names": ("총_직장_인구_수", "직장인 인구"),
        "aliases": ("직장인구", "직장 인구", "직장인", "회사원", "오피스 인구"),
        "preference_types": ("demand_profile",),
        "preference_values": ("office_worker",),
    },
    {
        "feature_id": "FC-05", "label": "활동유형", "topic_id": "population",
        "analysis_topics": ("demand", "population"),
        "metric_names": ("활동유형_우세성분",),
        "aliases": ("활동유형", "생활 패턴", "주중 주말", "주간 활동", "야간 거주"),
        "preference_types": ("activity_profile", "preferred_demand_window"),
        "preference_values": ("morning", "afternoon", "evening", "night"),
    },
    {
        "feature_id": "FC-06a", "label": "외국인 거주", "topic_id": "foreign_customer_presence",
        "analysis_topics": ("demand", "population"),
        "metric_names": ("외국인_거주_근사비율",),
        "aliases": ("외국인", "외국인 거주", "거주 외국인", "외국인이 많이 사는", "장기체류 외국인", "외국인 정주"),
        "preference_types": ("foreign_customer_presence", "target_customer"),
        "preference_values": ("foreigner", "foreign_resident"),
    },
    {
        "feature_id": "FC-06b", "label": "외국인 방문", "topic_id": "foreign_customer_presence",
        "analysis_topics": ("demand", "population"),
        "metric_names": ("외국인_방문_근사비율",),
        "aliases": ("외국인", "외국인 방문", "방문 외국인", "외국인이 많이 오는", "관광객", "방문객", "단기체류 외국인"),
        "preference_types": ("foreign_customer_presence", "target_customer"),
        "preference_values": ("foreigner", "tourist", "foreign_visitor"),
    },
    {
        "feature_id": "FC-07", "label": "대중교통 접근성", "topic_id": "accessibility",
        "analysis_topics": ("accessibility", "demand"),
        "metric_names": ("반경500m_도시철도역수", "반경250m_버스정류소수"),
        "aliases": ("지하철", "지하철역", "역세권", "버스", "대중교통", "교통 접근성"),
        "preference_types": ("near_anchor",),
        "preference_values": ("station", "bus_stop"),
    },
    {
        "feature_id": "FC-08", "label": "배후 주거단지 규모", "topic_id": "population",
        "analysis_topics": ("demand", "population"),
        "metric_names": ("반경500m_아파트_세대수",),
        "aliases": ("아파트", "주거단지", "세대수", "배후 주거"),
        "preference_types": ("near_anchor", "housing_presence"),
        "preference_values": ("apartment",),
    },
    {
        "feature_id": "FC-20", "label": "임대가격지수", "topic_id": "rent",
        "analysis_topics": ("cost",),
        "metric_names": ("R-ONE_임대가격지수",),
        "aliases": ("임대료", "임대 가격", "월세", "보증금", "렌트"),
        "preference_types": ("rent_preference",),
        "preference_values": ("rent",),
    },
    {
        "feature_id": "FC-21", "label": "공실률", "topic_id": "vacancy",
        "analysis_topics": ("cost", "risk"),
        "metric_names": ("R-ONE_공실률",),
        "aliases": ("공실", "공실률", "빈 점포"),
        "preference_types": ("vacancy_preference",),
        "preference_values": ("vacancy",),
    },
    {
        "feature_id": "FC-30", "label": "동일 업종 점포수", "topic_id": "competition",
        "analysis_topics": ("competition",),
        "metric_names": (),
        "aliases": ("경쟁", "동종 점포", "점포 수", "점포수"),
        "preference_types": ("avoid_high_competition", "competition_profile"),
        "preference_values": ("competition",),
    },
    {
        "feature_id": "FC-31", "label": "업종 점포당매출", "topic_id": "sales",
        "analysis_topics": ("sales_potential",),
        "metric_names": (),
        "aliases": ("매출", "점포당 매출", "매출 수준"),
        "preference_types": ("sales_profile",),
        "preference_values": ("sales",),
    },
    {
        "feature_id": "FC-32", "label": "프랜차이즈 비율", "topic_id": "competition",
        "analysis_topics": ("competition",),
        "metric_names": (),
        "aliases": ("프랜차이즈", "가맹점", "체인"),
        "preference_types": ("franchise_profile",),
        "preference_values": ("franchise",),
    },
    {
        "feature_id": "FC-42", "label": "업종 검색 관심도", "topic_id": "demand",
        "analysis_topics": ("demand",),
        "metric_names": ("FC-42_업종_현재검색관심도",),
        "aliases": ("검색 관심도", "검색량", "관심도", "트렌드"),
        "preference_types": ("trend_request",),
        "preference_values": ("trend",),
    },
)

FEATURE_BY_ID = {item["feature_id"]: item for item in FEATURE_CATALOG}
FEATURE_CATALOG_VERSION = "2026-09-07"


def feature_catalog_for_prompt() -> list[dict[str, Any]]:
    """Return bounded, JSON-safe metadata without exposing mutable catalog rows."""
    return [
        {
            "feature_id": item["feature_id"], "label": item["label"],
            "topic_id": item["topic_id"], "analysis_topics": list(item["analysis_topics"]),
            "metric_names": list(item["metric_names"]), "aliases": list(item["aliases"]),
        }
        for item in FEATURE_CATALOG
    ]


def feature_topic_ids(feature_ids: list[str] | set[str] | tuple[str, ...]) -> set[str]:
    return {
        FEATURE_BY_ID[feature_id]["topic_id"]
        for feature_id in feature_ids
        if feature_id in FEATURE_BY_ID
    }


def feature_ids_for_record(value: Any) -> set[str]:
    """Resolve a structured metric/evidence record to catalog IDs.

    For structured JSON only declared feature/metric identity is considered;
    free-form notes are not allowed to make an unrelated metric match merely
    because a note happens to mention a question word.
    """
    record = value if isinstance(value, dict) else None
    if record is None and isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, dict):
            record = decoded
    if record is not None:
        feature_id = record.get("feature_id")
        if isinstance(feature_id, str) and feature_id in FEATURE_BY_ID:
            return {feature_id}
        metric = record.get("metric_name")
        if isinstance(metric, str):
            matches = {
                item["feature_id"] for item in FEATURE_CATALOG
                if metric in item["metric_names"]
            }
            if metric.endswith("_점포수"):
                matches.add("FC-30")
            if metric.endswith("_점포당매출"):
                matches.add("FC-31")
            if "프랜차이즈" in metric:
                matches.add("FC-32")
            return matches
        dimension = record.get("dimension")
        dimension_to_feature = {
            "flow": "FC-01", "workplace_population": "FC-04",
            "total_store_count": "FC-30", "franchise_store_count": "FC-32",
            "sales": "FC-31", "rent": "FC-20", "vacancy": "FC-21",
        }
        if dimension in dimension_to_feature:
            return {dimension_to_feature[dimension]}
        return set()
    if isinstance(value, str):
        return {
            item["feature_id"] for item in FEATURE_CATALOG
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(item['feature_id'])}(?![A-Za-z0-9])", value)
        }
    return set()


def _negated(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 10):start]
    after = text[end:min(len(text), end + 14)]
    return bool(re.search(r"(?:말고|제외|빼고|아닌|없|않|안|적은|적지|많지)\s*$", before)
                or re.search(r"^\s*(?:은|는|을|를|이|가)?\s*(?:말고|제외|빼고|아닌|없|않|안|적지|많지)", after))


def _grounded_preference_items(query_context: dict[str, Any]) -> list[dict[str, Any]]:
    original = query_context.get("original_text")
    preferences = query_context.get("preferences")
    if not isinstance(original, str) or not isinstance(preferences, dict):
        return []
    items: list[dict[str, Any]] = []
    for group, values in preferences.items():
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            source_text = item.get("source_text")
            if isinstance(source_text, str) and source_text.strip() and source_text in original:
                items.append({**item, "preference_group": group})
    return items


def match_feature_intents(query_context: dict[str, Any]) -> list[dict[str, Any]]:
    """Semantically match grounded query text/preferences to catalog features.

    This is a metadata-driven fallback rather than a growing branch per user
    phrase.  Explicit planner preference types/values are stronger signals;
    catalog aliases and light token overlap cover paraphrases when the LLM is
    unavailable.  Matches are requests for relevant evidence, never ranking
    instructions.
    """
    text = query_context.get("normalized_text")
    if not isinstance(text, str) or not text.strip():
        text = query_context.get("original_text")
    if not isinstance(text, str) or not text.strip():
        return []
    preferences = _grounded_preference_items(query_context)
    output: list[dict[str, Any]] = []
    for feature in FEATURE_CATALOG:
        direct_hits: list[str] = []
        negated = False
        for alias in feature["aliases"]:
            for match in re.finditer(re.escape(alias), text):
                if _negated(text, match.start(), match.end()):
                    negated = True
                else:
                    direct_hits.append(alias)
        preference_hits: list[dict[str, Any]] = []
        for item in preferences:
            item_type = str(item.get("type") or "")
            item_value = str(item.get("value") or "")
            if (item_type in feature["preference_types"]
                    or item_value in feature["preference_values"]):
                preference_hits.append(item)
        if negated and not preference_hits:
            continue
        if not direct_hits and not preference_hits:
            continue
        # Preference metadata is the most reliable semantic bridge.  Direct
        # aliases are still enough for explicit natural-language questions.
        score = 0.96 if preference_hits else min(0.9, 0.62 + 0.05 * len(set(direct_hits)))
        source_text = (
            str(preference_hits[0].get("source_text") or "")[:240]
            if preference_hits else text[:240]
        )
        output.append({
            "feature_id": feature["feature_id"], "label": feature["label"],
            "topic_id": feature["topic_id"], "analysis_topics": list(feature["analysis_topics"]),
            "matched_terms": list(dict.fromkeys(direct_hits))[:8],
            "source_text": source_text, "match_method": "preference_metadata" if preference_hits else "catalog_semantic",
            "confidence": round(score, 2),
            "mode": str(preference_hits[0].get("mode") or "prefer") if preference_hits else "prefer",
        })
    return output
