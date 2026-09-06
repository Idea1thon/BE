"""보고서 제출 → 사이렌 호출 → 저장·알림 종단 흐름.

가짜 사이렌은 conftest 가 세운다(`SIREN_CALCULATED`). 여기서 확인하는 것은
추천 품질이 아니라 **우리가 상대 응답을 어떻게 저장하고 무엇을 알리는가** 다.

`database` 픽스처가 세션 스코프라 모든 테스트 파일이 DB 하나를 공유한다.
`operation_report` 의 유니크 제약이 `(branch_id, report_month)` 이므로 owner1
점포의 월은 파일 사이에서 전역 네임스페이스다. 2026년은 test_reports·
test_queries·test_notifications_pagination 이 이미 나눠 쓰고 있어서, 이 파일은
**2025년**을 쓴다. 여기 월을 바꿀 일이 생기면 다른 파일과 겹치는지 먼저 본다.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.errors import ApiError
from app.models import Branch, Notification, OperationReport, ReportAnalysis, UserAccount
from app.models.enums import ReportStatus, RiskLevel, UserType
from app.services import siren_client
from tests.conftest import SIREN_CALCULATED

OWNER1 = {"email": "owner1@example.com", "password": "devpass1234"}
HQ = {"email": "hq@example.com", "password": "devpass1234"}


async def _auth(client, creds) -> dict[str, str]:
    res = await client.post("/api/v1/auth/login", json=creds)
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


async def _submit(client, headers, month: str):
    fields = (await client.get("/api/v1/reports/input-fields", headers=headers)).json()["items"]
    items = [{"field_code": f["code"], "amount": 1000} for f in fields if f["is_required"]]
    res = await client.post(
        "/api/v1/reports",
        headers=headers,
        json={"report_month": month, "input_source": "MANUAL", "items": items},
    )
    # 제출이 막히면 여기서 세운다. 호출부가 바로 `["report_id"]` 를 꺼내는 탓에
    # 409(월 중복) 가 `KeyError: 'report_id'` 로 둔갑해 원인을 가렸다.
    assert res.status_code == 202, f"{month} 제출 실패: {res.status_code} {res.text}"
    return res


async def _session():
    from app.db.session import SessionLocal

    return SessionLocal()


# ------------------------------------------------------------------ 저장
async def test_analysis_row_is_saved_with_the_siren_values(client, seeded):
    headers = await _auth(client, OWNER1)
    res = await _submit(client, headers, "2025-03")
    assert res.status_code == 202, res.text
    report_id = res.json()["report_id"]

    async with await _session() as session:
        row = await session.get(ReportAnalysis, report_id)
        assert row is not None
        assert row.risk_score == 74            # 74.1944 → 버림
        assert row.risk_level is RiskLevel.DANGER
        assert row.rule_version == "risk-siren-v1.2-provisional"
        assert row.alert_policy_version == "confirmed-branch-v1"
        assert row.calculation_status == "calculated"
        assert row.factors == SIREN_CALCULATED["components"]


async def test_report_status_becomes_completed(client, seeded):
    headers = await _auth(client, OWNER1)
    report_id = (await _submit(client, headers, "2025-04")).json()["report_id"]
    async with await _session() as session:
        report = await session.get(OperationReport, report_id)
        assert report.status is ReportStatus.COMPLETED
        assert report.analysis_error is None


# ------------------------------------------------------------------ 알림
async def test_alert_creates_notifications_for_owner_and_hq(client, seeded):
    """CONTRACT.md 상 알림 생성은 우리 책임이고, 본사도 받는다(REQ-HQ-07~10)."""
    headers = await _auth(client, OWNER1)
    report_id = (await _submit(client, headers, "2025-05")).json()["report_id"]

    async with await _session() as session:
        rows = (
            await session.execute(
                select(Notification).where(Notification.report_id == report_id)
            )
        ).scalars().all()
        # 인원 수를 세지 않는다. 본사 계정 수는 이 파일이 정하는 값이 아니고,
        # 실제로 test_notifications_pagination 이 같은 프랜차이즈에 HQ 계정을
        # 하나 더 만든다. 확인할 것은 "몇 명" 이 아니라 "누구" 다.
        report = await session.get(OperationReport, report_id)
        branch = await session.get(Branch, report.branch_id)
        hq_ids = set(
            (
                await session.execute(
                    select(UserAccount.id).where(
                        UserAccount.franchise_id == branch.franchise_id,
                        UserAccount.user_type == UserType.HQ,
                    )
                )
            ).scalars().all()
        )

        got = {r.recipient_user_id for r in rows}
        assert got == {branch.owner_user_id} | hq_ids   # 점주 + 본사 전원
        assert len(rows) == len(got)                    # 같은 사람에게 두 번 보내지 않는다
        assert all("위험" in r.message for r in rows)
        assert all(r.email_status.value == "PENDING" for r in rows)  # 발송은 미구현


async def test_no_alert_creates_no_notification(client, seeded, monkeypatch):
    quiet = {**SIREN_CALCULATED, "alert": {"should_fire": False}}

    async def _analyze(payload):
        return quiet

    monkeypatch.setattr(siren_client, "analyze", _analyze)
    headers = await _auth(client, OWNER1)
    report_id = (await _submit(client, headers, "2025-06")).json()["report_id"]

    async with await _session() as session:
        rows = (
            await session.execute(
                select(Notification).where(Notification.report_id == report_id)
            )
        ).scalars().all()
        assert rows == []


# ------------------------------------------------------------------ 부분 결과
async def test_partial_result_is_stored_with_null_score(client, seeded, monkeypatch):
    """시장 자료가 없으면 상대가 점수·등급을 null 로 준다. 0으로 바꾸지 않는다."""
    partial = {
        **SIREN_CALCULATED,
        "risk": {**SIREN_CALCULATED["risk"], "score": None, "grade": None,
                 "calculation_status": "partial"},
        "alert": {"should_fire": False},
    }

    async def _analyze(payload):
        return partial

    monkeypatch.setattr(siren_client, "analyze", _analyze)
    headers = await _auth(client, OWNER1)
    report_id = (await _submit(client, headers, "2025-07")).json()["report_id"]

    async with await _session() as session:
        row = await session.get(ReportAnalysis, report_id)
        assert row is not None
        assert row.risk_score is None
        assert row.risk_level is None
        assert row.calculation_status == "partial"
        report = await session.get(OperationReport, report_id)
        assert report.status is ReportStatus.COMPLETED   # 부분도 분석 완료다


# ------------------------------------------------------------------ 실패
async def test_analysis_failure_does_not_lose_the_report(client, seeded, monkeypatch):
    """점주는 이미 값을 냈다. 분석 실패로 제출을 되돌리지 않는다."""

    async def _boom(payload):
        raise ApiError(503, "SERVICE_UNAVAILABLE", "위험도 분석 서비스에 연결할 수 없습니다")

    monkeypatch.setattr(siren_client, "analyze", _boom)
    headers = await _auth(client, OWNER1)
    res = await _submit(client, headers, "2025-08")
    assert res.status_code == 202, res.text      # 제출 자체는 성공
    report_id = res.json()["report_id"]

    async with await _session() as session:
        report = await session.get(OperationReport, report_id)
        assert report is not None
        assert report.status is ReportStatus.FAILED
        assert "연결할 수 없습니다" in report.analysis_error
        assert await session.get(ReportAnalysis, report_id) is None


async def test_status_endpoint_reflects_the_outcome(client, seeded):
    """INTERFACE_SPEC 의 폴링 경로. 동기라 첫 호출에서 확정 상태가 온다."""
    headers = await _auth(client, OWNER1)
    report_id = (await _submit(client, headers, "2025-09")).json()["report_id"]
    res = await client.get(f"/api/v1/reports/{report_id}/status", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "COMPLETED"


# ------------------------------------------------------------------ 상세 응답
# 저장 형식을 바꿨으면 응답 스키마도 같이 바뀌어야 한다. 아래 두 건이 없으면
# DB 행만 보는 테스트는 전부 통과하면서 API 는 500 을 내는 상태가 유지된다.
async def test_report_detail_carries_calculated_analysis(client, seeded):
    """`factors` 는 사이렌 `components` 라 dict 다.

    `AnalysisDetail.factors` 가 list 로 선언돼 있으면 성공 분석이 붙은 보고서마다
    이 조회가 500 이 된다. test_report_queries 의 픽스처는 factors 를 리스트로
    직접 INSERT 하므로 그쪽 테스트로는 잡히지 않는다.
    """
    headers = await _auth(client, OWNER1)
    report_id = (await _submit(client, headers, "2025-10")).json()["report_id"]

    res = await client.get(f"/api/v1/reports/{report_id}", headers=headers)
    assert res.status_code == 200, res.text
    analysis = res.json()["analysis"]
    assert analysis["factors"] == SIREN_CALCULATED["components"]
    assert analysis["risk_score"] == 74
    assert analysis["risk_level"] == "DANGER"


async def test_report_detail_survives_partial_analysis(client, seeded, monkeypatch):
    """부분 결과는 점수·등급이 null 이다. 상세 조회가 그걸 실어 나를 수 있어야 한다."""
    partial = {
        **SIREN_CALCULATED,
        "risk": {**SIREN_CALCULATED["risk"], "score": None, "grade": None,
                 "calculation_status": "partial"},
        "alert": {"should_fire": False},
    }

    async def _analyze(payload):
        return partial

    monkeypatch.setattr(siren_client, "analyze", _analyze)
    headers = await _auth(client, OWNER1)
    report_id = (await _submit(client, headers, "2025-11")).json()["report_id"]

    res = await client.get(f"/api/v1/reports/{report_id}", headers=headers)
    assert res.status_code == 200, res.text
    analysis = res.json()["analysis"]
    assert analysis["risk_score"] is None
    assert analysis["risk_level"] is None


# ------------------------------------------------------------------ 예상 밖 응답
async def test_unexpected_error_does_not_strand_the_report(client, seeded, monkeypatch):
    """보고서는 분석 전에 이미 커밋된다. 매핑 중 어떤 예외가 나든 ANALYZING 으로
    남으면 안 된다 — 재시도 대상으로도 안 잡히고 화면에는 영원히 '분석 중' 이다."""

    async def _boom(payload):
        raise TypeError("사이렌이 예상 밖 구조를 줬다고 치자")

    monkeypatch.setattr(siren_client, "analyze", _boom)
    headers = await _auth(client, OWNER1)
    res = await _submit(client, headers, "2025-12")
    report_id = res.json()["report_id"]

    async with await _session() as session:
        report = await session.get(OperationReport, report_id)
        assert report.status is ReportStatus.FAILED
        assert report.analysis_error                       # 이유가 남는다
        assert "TypeError" not in report.analysis_error    # 내부 예외는 노출하지 않는다
        assert await session.get(ReportAnalysis, report_id) is None


async def test_non_json_siren_body_becomes_a_failed_report(client, seeded, monkeypatch):
    """200 이어도 본문이 계약을 지킨다는 보장은 없다. 클라이언트 층에서 걸러야
    매핑 층이 AttributeError 로 터지지 않는다."""
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    fake = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler), base_url="http://siren.test"
    )
    monkeypatch.setattr(siren_client, "get_client", lambda: fake)

    headers = await _auth(client, OWNER1)
    res = await _submit(client, headers, "2025-02")
    report_id = res.json()["report_id"]

    async with await _session() as session:
        report = await session.get(OperationReport, report_id)
        assert report.status is ReportStatus.FAILED
        assert "해석할 수 없습니다" in report.analysis_error
