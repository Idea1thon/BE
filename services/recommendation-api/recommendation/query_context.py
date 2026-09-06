"""One source-preserving user context for retrieval, reranking and explanation.

This module does not rewrite queries or infer intent. Conditions and region
are the caller's server-validated contracts. A quoted preference is traceable
input, not proof that any inferred preference fields are correct.
"""
from __future__ import annotations

from copy import deepcopy
import re
import unicodedata
from typing import Any


MAX_NORMALIZED_TEXT_LENGTH = 4000
PREFERENCE_GROUPS = (
    "location_preferences", "demand_preferences", "time_preferences",
    "competition_preferences", "business_preferences", "comparison_requests",
    "unsupported_requests",
)


def build_query_context(
    raw_text: str,
    selected_region: dict[str, Any] | None = None,
    industry_code: str | None = None,
    conditions: dict[str, Any] | None = None,
    preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Preserve input and add a bounded, semantically unchanged normalized view.

    NFC composes equivalent Unicode characters; whitespace becomes a single
    space. Punctuation, numbers, negation and clause order are left intact.
    If normalization exceeds the limit, return None instead of a truncated
    clause that could omit a negation. The full original remains available;
    downstream callers must apply their own model-input budget policy.

    Preference quotes must match the *original*, before normalization. This
    is intentionally strict and does not authorize preferences as filters.
    No arbitrary preference groups or unquoted unsupported requests enter
    the context. Server-validated unsupported conditions remain in conditions.
    """
    if not isinstance(raw_text, str):
        raise TypeError("raw_text must be a string")
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFC", raw_text)).strip()
    too_long = len(normalized) > MAX_NORMALIZED_TEXT_LENGTH
    grounded_preferences: dict[str, list[dict[str, Any]]] = {}
    if isinstance(preferences, dict):
        for group in PREFERENCE_GROUPS:
            items = preferences.get(group)
            if not isinstance(items, list):
                continue
            grounded_preferences[group] = [
                deepcopy(item) for item in items
                if isinstance(item, dict)
                and isinstance(item.get("source_text"), str)
                and item["source_text"].strip()
                and item["source_text"] in raw_text
            ]
    return {
        "original_text": raw_text,
        "normalized_text": None if too_long else normalized,
        "normalization_status": "too_long" if too_long else "normalized",
        "selected_region": deepcopy(selected_region) if isinstance(selected_region, dict) else {},
        "industry_code": industry_code,
        "conditions": deepcopy(conditions) if isinstance(conditions, dict) else {},
        "preferences": grounded_preferences,
    }
