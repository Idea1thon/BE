"""사이렌 연동. 상대 서비스를 가짜로 세워 우리 쪽 변환·오류 처리만 검증한다.

정본은 `services/siren/models.py`(요청 계약)와 `services/siren/pipeline.py`
(`analyze()` 응답)다. 픽스처를 우리 가정대로 지어내면 계약 불일치를 테스트가
못 잡는다 — PR #26 에서 `explanations` 를 list 로 가정했다가 실제로는 dict 였다.
아래 응답 픽스처는 `pipeline.py` 의 `result` 조립부에서 그대로 옮겼다.
"""

from __future__ import annotations

import datetime as dt
import decimal

import httpx
import pytest

from app.errors import ApiError
from app.models.enums import InputSource, RiskLevel
from app.services import siren_client
from app.services.siren_mapper import (
    FIELD_TO_SIREN,
    LocationInputs,
    SirenMappingError,
    build_analyze_request,
    build_location,
    build_monthly_report,
    notification_message,
    should_notify,
    to_analysis_values,
)

# --------------------------------------------------------------------------- #
# 픽스처 — services/siren/pipeline.py analyze() 결과 구조
# --------------------------------------------------------------------------- #
CALCULATED = {
    "request_id": "req-1",
    "branch": {"franchise_id": "1", "branch_id": "7", "as_of": "2026-08-31"},
    "risk": {
        "score": 74.1944,
        "grade": "위험",
        "score_version": "risk-siren-v1.2-provisional",
        "calculation_status": "calculated",
        "policy_status": "provisional",
        "branch_floor_applied": False,
    },
    "layers": {
        "market_risk": {"score": 55.0, "status": "calculated"},
        "branch_risk": {"score": 84.0, "status": "calculated"},
    },
    "components": {
        "closure": {"score": 40.0, "status": "calculated"},
        "sales_decline": {"market": {"score": 30.0}, "branch": {"score": 60.0}},
        "competition": {"score": 20.0, "status": "calculated"},
        "profitability": {"score": 90.0, "status": "calculated"},
    },
    "review_signal": {"status": "missing", "sub_score": None},
    "evidence": [{"evidence_id": "ev-profit-margin-3m", "signal_id": "SR-05"}],
    "missing_data": [],
    "uncertainty": [],
    "excluded_future_months": [],
    "data_provenance": {"contains_synthetic": False},
    "alert": {"should_fire": True, "trigger": {"basis": "composite"}},
    "financial_products": {"status": "catalog_match_pending", "items": []},
    "explanation": {},
    "projections": {"branch_owner": {"recommended_actions": []}},
    "franchise_closure": {"status": "missing"},
}

# market_data 를 넘기지 않으면 상대는 시장 층을 missing 으로 두고 점수·등급을
# null 로 준다(pipeline.py: `"score": composite if both_calculated else None`).
PARTIAL = {
    **CALCULATED,
    "risk": {
        "score": None,
        "grade": None,
        "score_version": "risk-siren-v1.2-provisional",
        "calculation_status": "partial",
        "policy_status": "provisional",
        "branch_floor_applied": False,
    },
    "layers": {
        "market_risk": {"score": None, "status": "missing"},
        "branch_risk": {"score": 84.0, "status": "calculated"},
    },
    "alert": {"should_fire": True, "trigger": {"basis": "profitability", "score": 90.0}},
}


def _items(**overrides: float) -> dict[str, decimal.Decimal]:
    return {k: decimal.Decimal(str(v)) for k, v in overrides.items()}


# --------------------------------------------------------------------------- #
# 1. 입력 항목 매핑
# --------------------------------------------------------------------------- #
def test_field_map_covers_every_seeded_input_field():
    """시드 35개 항목이 모두 대응을 갖는다. 하나라도 빠지면 상대가 422 를 준다."""
    from scripts.seed import INPUT_FIELDS

    seeded = {code for code, *_ in INPUT_FIELDS}
    assert seeded == set(FIELD_TO_SIREN)


def test_field_map_has_no_duplicate_targets():
    """두 항목이 같은 자리로 가면 한쪽이 조용히 덮인다."""
    targets = list(FIELD_TO_SIREN.values())
    assert len(targets) == len(set(targets))


def test_monthly_report_places_amounts_at_contract_paths():
    payload = build_monthly_report(
        report_month=dt.date(2026, 8, 1),
        items=_items(HALL_CARD=1000, DLV_BAEMIN=500, OPS_RENT=300, FIN_LOAN_INTEREST=40),
        input_source=InputSource.MANUAL,
    )
    assert payload["month"] == "2026-08"
    assert payload["sales"]["hall"]["credit"] == 1000.0
    assert payload["sales"]["delivery"]["baemin"] == 500.0
    assert payload["opex"]["rent_mgmt"] == 300.0
    assert payload["finance"]["loan_interest"] == 40.0


def test_absent_optional_fields_become_zero():
    """필수 8개만 와도 상대 모델(기본값 없음, extra=forbid)을 만족해야 한다."""
    payload = build_monthly_report(
        report_month=dt.date(2026, 8, 1),
        items=_items(HALL_CARD=10),
        input_source=InputSource.MANUAL,
    )
    assert payload["labor"]["fulltime"] == 0.0
    assert payload["cogs"]["inventory_end"] == 0.0
    assert payload["sales"]["takeout"]["cash"] == 0.0


def test_blocks_are_not_shared_between_reports():
    """월별 dict 가 같은 객체를 공유하면 한 달 값이 다른 달로 샌다."""
    a = build_monthly_report(
        report_month=dt.date(2026, 7, 1), items=_items(HALL_CARD=1), input_source=InputSource.MANUAL
    )
    b = build_monthly_report(
        report_month=dt.date(2026, 8, 1), items=_items(HALL_CARD=2), input_source=InputSource.MANUAL
    )
    assert a["sales"]["hall"]["credit"] == 1.0
    assert b["sales"]["hall"]["credit"] == 2.0
    assert a["sales"] is not b["sales"]


def test_unknown_field_code_is_rejected_here_not_by_the_service():
    with pytest.raises(SirenMappingError) as exc:
        build_monthly_report(
            report_month=dt.date(2026, 8, 1),
            items=_items(NOT_A_FIELD=1),
            input_source=InputSource.MANUAL,
        )
    assert "NOT_A_FIELD" in str(exc.value)


def test_input_source_maps_to_real_source_tags_not_synthetic():
    manual = build_monthly_report(
        report_month=dt.date(2026, 8, 1), items={}, input_source=InputSource.MANUAL
    )
    pos = build_monthly_report(
        report_month=dt.date(2026, 8, 1), items={}, input_source=InputSource.POS
    )
    assert manual["sales_source"] == "self_reported"
    assert pos["cost_source"] == "pos"


# --------------------------------------------------------------------------- #
# 2. 위치
# --------------------------------------------------------------------------- #
def test_gu_code_is_the_first_five_digits():
    loc = build_location(
        region_code="1168010100",
        inputs=LocationInputs(trade_area_code="3120185", x_5181=1.0, y_5181=2.0),
    )
    assert loc["gu_code"] == "11680"
    assert loc["admin_dong_code"] is None  # 상대 계약에서 선택 항목


def test_sigungu_code_passes_through_unchanged():
    loc = build_location(
        region_code="11680",
        inputs=LocationInputs(trade_area_code="3120185", x_5181=1.0, y_5181=2.0),
    )
    assert loc["gu_code"] == "11680"


def test_short_region_code_is_rejected():
    with pytest.raises(SirenMappingError):
        build_location(
            region_code="11",
            inputs=LocationInputs(trade_area_code="3120185", x_5181=1.0, y_5181=2.0),
        )


def test_blank_trade_area_code_is_rejected():
    with pytest.raises(SirenMappingError):
        build_location(
            region_code="11680",
            inputs=LocationInputs(trade_area_code="  ", x_5181=1.0, y_5181=2.0),
        )


# --------------------------------------------------------------------------- #
# 3. 요청 조립
# --------------------------------------------------------------------------- #
def _request(**kw):
    base = dict(
        request_id="req-1",
        franchise_id=1,
        branch_id=7,
        as_of=dt.date(2026, 8, 31),
        industry_code="CS100001",
        location=build_location(
            region_code="11680",
            inputs=LocationInputs(trade_area_code="3120185", x_5181=1.0, y_5181=2.0),
        ),
        monthly_reports=[],
    )
    base.update(kw)
    return build_analyze_request(**base)


def test_llm_is_disabled_explicitly():
    """상대 기본값은 'explanation_only' 다. 명시하지 않으면 LLM 을 부른다."""
    assert _request()["options"]["llm_mode"] == "disabled"


def test_notifications_stay_off():
    """상대는 send_notifications=true 를 받으면 ValueError 를 던진다."""
    assert _request()["options"]["send_notifications"] is False


def test_identifiers_are_strings():
    payload = _request()
    assert payload["franchise_id"] == "1"
    assert payload["branch_id"] == "7"


def test_market_data_is_omitted_when_absent():
    """None 을 명시적으로 실어 보내지 않는다. 상대 기본값과 같은 뜻이다."""
    assert "market_data" not in _request()
    assert _request(market_data={"source": "seoul"})["market_data"] == {"source": "seoul"}


# --------------------------------------------------------------------------- #
# 4. 응답 → report_analysis
# --------------------------------------------------------------------------- #
def test_calculated_result_maps_to_our_columns():
    values = to_analysis_values(CALCULATED)
    assert values.risk_score == 74  # 74.1944 → SMALLINT
    assert values.risk_level is RiskLevel.DANGER
    assert values.factors == CALCULATED["components"]
    assert values.calculation_status == "calculated"


def test_grade_boundaries_match_our_enum():
    pairs = (("정상", RiskLevel.NORMAL), ("주의", RiskLevel.CAUTION), ("위험", RiskLevel.DANGER))
    for grade, expected in pairs:
        body = {**CALCULATED, "risk": {**CALCULATED["risk"], "grade": grade}}
        assert to_analysis_values(body).risk_level is expected


def test_unknown_grade_is_not_guessed():
    body = {**CALCULATED, "risk": {**CALCULATED["risk"], "grade": "심각"}}
    with pytest.raises(SirenMappingError):
        to_analysis_values(body)


def test_partial_result_is_reported_as_unstorable_not_coerced():
    """데이터 부족을 0점·정상으로 바꾸지 않는다."""
    values = to_analysis_values(PARTIAL)
    assert values.risk_score is None
    assert values.risk_level is None
    assert values.storable is False
    assert any("risk_score" in b for b in values.blockers)
    assert any("risk_level" in b for b in values.blockers)


def test_rule_version_overflow_is_a_blocker_not_a_truncation():
    """VARCHAR(20) 에 27자를 잘라 넣으면 재현성 정보가 깨진다."""
    values = to_analysis_values(CALCULATED)
    assert values.rule_version == "risk-siren-v1.2-provisional"
    assert any("rule_version" in b for b in values.blockers)


def test_risk_periods_and_recommendations_are_empty_not_invented():
    """상대 응답에 REQ-SRN-02 4구간이 없다. 없는 값을 만들지 않는다."""
    values = to_analysis_values(CALCULATED)
    assert values.risk_periods == []
    assert values.recommendations == []


# --------------------------------------------------------------------------- #
# 5. 알림
# --------------------------------------------------------------------------- #
def test_alert_follows_should_fire_not_the_grade():
    """등급이 미확정이어도 확인된 점포 위험이면 알린다(confirmed-branch-v1)."""
    assert should_notify(CALCULATED) is True
    assert should_notify(PARTIAL) is True
    quiet = {**CALCULATED, "alert": {"should_fire": False}}
    assert should_notify(quiet) is False


def test_partial_message_does_not_claim_a_grade():
    message = notification_message(PARTIAL, branch_name="강남 역삼점")
    assert "단계입니다" not in message
    assert "확정되지 않았습니다" in message


def test_calculated_message_states_the_grade():
    assert "'위험'" in notification_message(CALCULATED, branch_name="강남 역삼점")


# --------------------------------------------------------------------------- #
# 6. 클라이언트 오류 변환
# --------------------------------------------------------------------------- #
def _fake(monkeypatch, handler):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://siren.test"
    )
    monkeypatch.setattr(siren_client, "get_client", lambda: client)
    return client


async def test_success_returns_the_body(monkeypatch):
    _fake(monkeypatch, lambda request: httpx.Response(200, json=CALCULATED))
    assert (await siren_client.analyze({}))["risk"]["grade"] == "위험"


async def test_contract_violation_is_our_fault_not_the_users(monkeypatch):
    """422 는 우리가 만든 payload 문제다. 점주에게 400 을 돌려주지 않는다."""
    body = {"detail": [{"loc": ["body", "location", "x_5181"], "msg": "field required"}]}
    _fake(monkeypatch, lambda request: httpx.Response(422, json=body))
    with pytest.raises(ApiError) as exc:
        await siren_client.analyze({})
    assert exc.value.status_code == 500
    assert exc.value.code == "INTERNAL_ERROR"


async def test_service_error_becomes_503(monkeypatch):
    _fake(monkeypatch, lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(ApiError) as exc:
        await siren_client.analyze({})
    assert exc.value.status_code == 503


async def test_connection_failure_becomes_503(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("no route", request=request)

    _fake(monkeypatch, boom)
    with pytest.raises(ApiError) as exc:
        await siren_client.analyze({})
    assert exc.value.status_code == 503


async def test_hq_summary_uses_its_own_path(monkeypatch):
    seen: list[str] = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"franchise_id": "1"})

    _fake(monkeypatch, handler)
    await siren_client.hq_summary({"franchise_id": "1"})
    assert seen == ["/internal/risk-sirens/hq-summary"]
