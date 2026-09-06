"""입지 추천 연동. 추천 서비스를 가짜로 세워 우리 쪽 동작만 검증한다.

실제 추천 API 를 띄우지 않는 이유는 두 가지다. 파이프라인 실행에 원천 데이터와
수십 초가 필요하고, 여기서 확인하려는 것은 추천 품질이 아니라 **우리가 상대의
응답을 어떻게 번역하는가** 이기 때문이다. 타임아웃·429·422 같은 실패 경로는
가짜 서버가 아니면 재현하기도 어렵다.

httpx.MockTransport 를 쓴다. httpx 는 이미 의존성이라 추가 설치가 없다.
"""

from __future__ import annotations

import httpx
import pytest

from app.services import recommendation_client

HQ = {"email": "hq@example.com", "password": "devpass1234"}
OWNER1 = {"email": "owner1@example.com", "password": "devpass1234"}

GANGNAM = "11680"          # 시드의 강남구
YEOKSAM = "11680640"       # 마이그레이션 0005 의 역삼1동 (상권분석 행정동 코드)
SEOUL = "11"               # 시도

# 상대의 RecommendationApiResponse(services/recommendation-api/api/main.py)를
# 정본으로 삼는다. candidates 는 list, **explanations 는 dict** 다.
# 이 픽스처를 우리 가정대로 지어내면 계약 불일치를 테스트가 못 잡는다 —
# PR #26 리뷰에서 실제로 그렇게 놓쳤다.
COMPLETED_BODY = {
    "request_id": "r-1",
    "run_id": "run-abc",
    "status": "completed",
    "request": {"region": {"sido": "서울특별시", "sigungu": "강남구", "dong": None}},
    "input_interpretation": {"resolved_industry_code": "CS100001"},
    "summary": {"applied_limit": 5},
    "candidates": [{"rank": 1, "name": "역삼역 일대"}],
    "explanations": {
        "cards": [{"rank": 1, "text": "유동인구와 임대료 대비 우수"}],
        "explanation_mode": "rule",
        "degraded": False,
        "llm": {},
    },
}


async def _auth(client, creds) -> dict[str, str]:
    res = await client.post("/api/v1/auth/login", json=creds)
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    """토큰이 비어 있으면 클라이언트가 호출 전에 막는다. 테스트에서는 채운다."""
    monkeypatch.setattr(
        recommendation_client.settings, "internal_api_token", "test-token", raising=False
    )


def _fake_service(monkeypatch, handler):
    """추천 서비스를 MockTransport 로 대체한다."""
    captured: dict = {}

    def _handle(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return handler(request)

    fake = httpx.AsyncClient(
        base_url="http://recommendation-api",
        headers={"X-Internal-Token": "test-token"},
        transport=httpx.MockTransport(_handle),
    )
    monkeypatch.setattr(recommendation_client, "get_client", lambda: fake)
    return captured


# ------------------------------------------------------------------ 정상 경로
async def test_returns_result_when_service_completes(client, monkeypatch):
    captured = _fake_service(
        monkeypatch, lambda req: httpx.Response(200, json=COMPLETED_BODY)
    )
    headers = await _auth(client, HQ)

    res = await client.post(
        "/api/v1/location-recommendations",
        headers=headers,
        json={"region_code": GANGNAM, "business_category_code": "CS100001"},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["run_id"] == "run-abc"
    assert body["candidates"][0]["name"] == "역삼역 일대"
    # 추천 서비스의 응답을 변환 없이 싣는다. explanations 는 list 가 아니라 dict 다.
    assert body["explanations"] == COMPLETED_BODY["explanations"]
    assert body["explanations"]["cards"][0]["rank"] == 1

    sent = captured["request"]
    assert sent.headers["X-Internal-Token"] == "test-token"
    assert sent.url.path == "/internal/recommendations"


async def test_converts_region_code_to_korean_names(client, monkeypatch):
    """추천 서비스로 표시 이름과 정본 지역 코드를 함께 전달한다."""
    import json

    captured = _fake_service(
        monkeypatch, lambda req: httpx.Response(200, json=COMPLETED_BODY)
    )
    headers = await _auth(client, HQ)

    await client.post(
        "/api/v1/location-recommendations",
        headers=headers,
        json={"region_code": GANGNAM},
    )

    payload = json.loads(captured["request"].content)
    assert payload["region"] == {"sido": "서울특별시", "sigungu": "강남구", "dong": None, "sigungu_code": GANGNAM}


async def test_dong_code_fills_dong_and_parent_sigungu(client, monkeypatch):
    import json

    captured = _fake_service(
        monkeypatch, lambda req: httpx.Response(200, json=COMPLETED_BODY)
    )
    headers = await _auth(client, HQ)

    await client.post(
        "/api/v1/location-recommendations",
        headers=headers,
        json={"region_code": YEOKSAM},
    )

    payload = json.loads(captured["request"].content)
    assert payload["region"]["sigungu"] == "강남구"
    assert payload["region"]["dong"] == "역삼1동"
    assert payload["region"]["sigungu_code"] == GANGNAM
    assert payload["region"]["admin_dong_code"] == YEOKSAM


async def test_owner_can_also_request(client, monkeypatch):
    """본사 전용이 아니다. 점주도 입지를 본다."""
    _fake_service(monkeypatch, lambda req: httpx.Response(200, json=COMPLETED_BODY))
    headers = await _auth(client, OWNER1)
    res = await client.post(
        "/api/v1/location-recommendations", headers=headers, json={"region_code": GANGNAM}
    )
    assert res.status_code == 200


# ------------------------------------------------------------------ 오래 걸릴 때
async def test_timeout_becomes_202_with_run_id(client, monkeypatch):
    """상대의 504 는 실패가 아니라 '나중에 물어보라'는 신호다."""
    _fake_service(
        monkeypatch,
        lambda req: httpx.Response(
            504,
            json={"detail": {"code": "pipeline_timeout", "message": "초과", "run_id": "run-late"}},
        ),
    )
    headers = await _auth(client, HQ)

    res = await client.post(
        "/api/v1/location-recommendations", headers=headers, json={"region_code": GANGNAM}
    )

    assert res.status_code == 202, res.text
    assert res.json()["status"] == "RUNNING"
    # run_id 를 그대로 주지 않는다. 값을 아는 사람이 남의 결과를 볼 수 있다.
    assert res.json()["run_ticket"] != "run-late"
    # FE 가 폴링 간격을 임의로 정하면 우리 재시도가 상대의 슬롯을 채운다.
    assert res.headers["Retry-After"] == str(res.json()["retry_after"])


async def _ticket_for(client, monkeypatch, creds=HQ) -> tuple[str, dict[str, str]]:
    """202 를 한 번 받아 폴링용 티켓을 얻는다."""
    _fake_service(
        monkeypatch,
        lambda req: httpx.Response(
            504, json={"detail": {"message": "초과", "run_id": "run-late"}}
        ),
    )
    headers = await _auth(client, creds)
    res = await client.post(
        "/api/v1/location-recommendations", headers=headers, json={"region_code": GANGNAM}
    )
    assert res.status_code == 202
    return res.json()["run_ticket"], headers


async def test_polling_returns_202_while_running(client, monkeypatch):
    ticket, headers = await _ticket_for(client, monkeypatch)
    _fake_service(
        monkeypatch,
        lambda req: httpx.Response(202, json={"run_id": "run-late", "status": "running"}),
    )
    res = await client.get(f"/api/v1/location-recommendations/{ticket}", headers=headers)
    assert res.status_code == 202


async def test_polling_returns_result_when_done(client, monkeypatch):
    ticket, headers = await _ticket_for(client, monkeypatch)
    _fake_service(monkeypatch, lambda req: httpx.Response(200, json=COMPLETED_BODY))
    res = await client.get(f"/api/v1/location-recommendations/{ticket}", headers=headers)
    assert res.status_code == 200
    assert res.json()["candidates"][0]["rank"] == 1


async def test_another_user_cannot_poll_with_someone_elses_ticket(client, monkeypatch):
    """티켓은 발급받은 사용자에게 묶인다. 값을 알아도 남은 쓸 수 없다."""
    ticket, _ = await _ticket_for(client, monkeypatch, creds=HQ)
    _fake_service(monkeypatch, lambda req: httpx.Response(200, json=COMPLETED_BODY))
    other = await _auth(client, OWNER1)

    res = await client.get(f"/api/v1/location-recommendations/{ticket}", headers=other)
    assert res.status_code == 404


async def test_forged_ticket_is_404(client, monkeypatch):
    _fake_service(monkeypatch, lambda req: httpx.Response(200, json=COMPLETED_BODY))
    headers = await _auth(client, HQ)
    res = await client.get(
        "/api/v1/location-recommendations/not-a-signed-ticket", headers=headers
    )
    assert res.status_code == 404


async def test_polling_unknown_run_is_404(client, monkeypatch):
    ticket, headers = await _ticket_for(client, monkeypatch)
    _fake_service(
        monkeypatch,
        lambda req: httpx.Response(404, json={"detail": {"message": "없음"}}),
    )
    res = await client.get(f"/api/v1/location-recommendations/{ticket}", headers=headers)
    assert res.status_code == 404


# ------------------------------------------------------------------ 실패 번역
async def test_capacity_exceeded_becomes_503_with_retry_after(client, monkeypatch):
    _fake_service(
        monkeypatch,
        lambda req: httpx.Response(
            429,
            headers={"Retry-After": "17"},
            json={"detail": {"message": "동시 추천 요청 한도에 도달했습니다"}},
        ),
    )
    headers = await _auth(client, HQ)

    res = await client.post(
        "/api/v1/location-recommendations", headers=headers, json={"region_code": GANGNAM}
    )

    assert res.status_code == 503, res.text
    body = res.json()["error"]
    assert body["code"] == "SERVICE_UNAVAILABLE"
    assert body["retry_after"] == 17
    # 상대 메시지를 그대로 쓰되 오류 봉투는 우리 형식이다(API_SPEC 0-3).
    assert "한도" in body["message"]


async def test_unsupported_region_becomes_400(client, monkeypatch):
    """상대의 422 는 사용자 입력 문제다. 500 이 아니라 400 으로 내린다."""
    _fake_service(
        monkeypatch,
        lambda req: httpx.Response(
            422, json={"detail": {"message": "현재 데이터 계약은 서울특별시만 지원합니다"}}
        ),
    )
    headers = await _auth(client, HQ)

    res = await client.post(
        "/api/v1/location-recommendations", headers=headers, json={"region_code": GANGNAM}
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_token_mismatch_becomes_500_not_401(client, monkeypatch):
    """토큰이 어긋난 것은 우리 설정 문제다.

    401 을 그대로 돌려주면 사용자는 로그인이 풀린 것으로 오해하고 다시
    로그인한다. 그래도 고쳐지지 않는다.
    """
    _fake_service(
        monkeypatch,
        lambda req: httpx.Response(401, json={"detail": {"message": "unauthorized"}}),
    )
    headers = await _auth(client, HQ)

    res = await client.post(
        "/api/v1/location-recommendations", headers=headers, json={"region_code": GANGNAM}
    )

    assert res.status_code == 500
    assert res.json()["error"]["code"] == "INTERNAL_ERROR"


async def test_connection_failure_becomes_503(client, monkeypatch):
    def _refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _fake_service(monkeypatch, _refuse)
    headers = await _auth(client, HQ)

    res = await client.post(
        "/api/v1/location-recommendations", headers=headers, json={"region_code": GANGNAM}
    )
    assert res.status_code == 503


async def test_missing_token_is_500_not_silent_empty(client, monkeypatch):
    """설정 누락이 '추천 결과 없음'으로 나타나면 원인을 못 찾는다."""
    monkeypatch.setattr(
        recommendation_client.settings, "internal_api_token", "", raising=False
    )
    headers = await _auth(client, HQ)

    res = await client.post(
        "/api/v1/location-recommendations", headers=headers, json={"region_code": GANGNAM}
    )
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "INTERNAL_ERROR"


# ------------------------------------------------------------------ 입력 검증
async def test_unknown_region_is_404(client, monkeypatch):
    _fake_service(monkeypatch, lambda req: httpx.Response(200, json=COMPLETED_BODY))
    headers = await _auth(client, HQ)
    res = await client.post(
        "/api/v1/location-recommendations", headers=headers, json={"region_code": "99999"}
    )
    assert res.status_code == 404


async def test_sido_level_region_is_rejected(client, monkeypatch):
    """시도 단위면 후보 범위가 서울 전체가 되어 추천의 의미가 없다."""
    _fake_service(monkeypatch, lambda req: httpx.Response(200, json=COMPLETED_BODY))
    headers = await _auth(client, HQ)
    res = await client.post(
        "/api/v1/location-recommendations", headers=headers, json={"region_code": SEOUL}
    )
    assert res.status_code == 400


async def test_unknown_industry_is_rejected_before_calling_service(client, monkeypatch):
    """왕복 한 번을 아끼고 메시지도 명확해진다."""
    called = {"n": 0}

    def _count(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json=COMPLETED_BODY)

    _fake_service(monkeypatch, _count)
    headers = await _auth(client, HQ)

    res = await client.post(
        "/api/v1/location-recommendations",
        headers=headers,
        json={"region_code": GANGNAM, "business_category_code": "NOPE"},
    )

    assert res.status_code == 400
    assert called["n"] == 0


async def test_requires_auth(client):
    res = await client.post(
        "/api/v1/location-recommendations", json={"region_code": GANGNAM}
    )
    assert res.status_code == 401


async def test_rejects_unknown_field(client):
    """extra='forbid'. 오타 난 필드가 조용히 무시되면 FE 가 원인을 못 찾는다."""
    headers = await _auth(client, HQ)
    res = await client.post(
        "/api/v1/location-recommendations",
        headers=headers,
        json={"region_code": GANGNAM, "regionCode": GANGNAM},
    )
    assert res.status_code == 400
