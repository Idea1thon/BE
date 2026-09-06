"""Deterministic alert-event construction with dispatch intentionally disabled."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any


def idempotency_key(branch_id: str, as_of: date, score_version: str) -> str:
    raw = f"{branch_id}|{as_of.isoformat()}|{score_version}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def build_alert(
    *,
    branch_id: str,
    as_of: date,
    score: float | None,
    grade: str | None,
    risk_level: str | None,
    score_version: str,
    grade_policy: str,
    evidence_ids: list[str],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # strict 정책에서는 현재 계약의 confirmed branch warning을 허용한다.
    # 비-strict 정책은 잠정 결과이므로 위험 등급처럼 보여도 실제 알림을 막는다.
    is_strict = grade_policy == "strict"
    should_fire = is_strict and ((grade == "위험" and score is not None) or trigger is not None)
    suppressed_reason = None
    if not should_fire and (grade == "위험" or trigger is not None) and not is_strict:
        suppressed_reason = f"grade_policy={grade_policy}"
    return {
        # 상태 스냅샷 이벤트. 등급 전이(이전→현재) 감지는 중간 백엔드가 previous_grade
        # 를 보관·비교해야 하며, 이 서비스는 매 평가의 현재 상태만 만든다.
        "event_type": "branch_risk_evaluated",
        "event_id": f"evt-{idempotency_key(branch_id, as_of, score_version)}",
        "idempotency_key": idempotency_key(branch_id, as_of, score_version),
        "branch_id": branch_id,
        "grade": grade,
        "risk_level": risk_level,
        "previous_grade": None,
        "score": score,
        "as_of": as_of.isoformat(),
        "recipients": [
            {"role": "branch_owner", "channels": ["in_app", "email"]},
            {"role": "franchise_hq", "channels": ["in_app", "email"]},
        ],
        "report_link": None,
        "should_fire": should_fire,
        "trigger": trigger,
        "suppressed_reason": suppressed_reason,
        "alert_policy_version": "confirmed-branch-v1",
        "dispatch_status": "disabled",
        "dispatch_owner": "middle_backend",
        "evidence_ids": evidence_ids,
    }
