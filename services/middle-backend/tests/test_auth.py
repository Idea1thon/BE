"""인증 흐름 테스트. API_SPEC 1-1 / 2-1 및 D2(Access + Refresh) 기준."""

from __future__ import annotations

import pytest

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/users/me"

OWNER = {"email": "owner1@example.com", "password": "devpass1234"}
HQ = {"email": "hq@example.com", "password": "devpass1234"}


async def _login(client, creds) -> dict:
    res = await client.post(LOGIN, json=creds)
    assert res.status_code == 200, res.text
    return res.json()


async def test_health(client):
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


async def test_login_owner_returns_token_pair_and_user(client):
    body = await _login(client, OWNER)
    assert body["token"]
    assert body["refresh_token"]
    assert body["expires_in"] > 0
    assert body["user"]["email"] == OWNER["email"]
    assert body["user"]["user_type"] == "OWNER"
    assert body["user"]["franchise_id"] > 0


async def test_login_wrong_password_returns_401_envelope(client):
    res = await client.post(LOGIN, json={**OWNER, "password": "wrong"})
    assert res.status_code == 401
    error = res.json()["error"]
    assert error["code"] == "UNAUTHORIZED"
    assert "occurred_at" in error


async def test_login_unknown_email_is_indistinguishable(client):
    res = await client.post(LOGIN, json={"email": "nobody@example.com", "password": "x"})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


async def test_login_missing_field_returns_400(client):
    res = await client.post(LOGIN, json={"email": "owner1@example.com"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_password_is_not_stored_in_plaintext(client):
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models import UserAccount

    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(UserAccount.password_hash).where(
                    UserAccount.email == OWNER["email"]
                )
            )
        ).scalar_one()
    assert row != OWNER["password"]
    assert row.startswith("$2b$")


async def test_me_returns_branch_for_owner(client):
    body = await _login(client, OWNER)
    res = await client.get(ME, headers={"Authorization": f"Bearer {body['token']}"})
    assert res.status_code == 200, res.text
    me = res.json()
    assert me["user_type"] == "OWNER"
    assert me["franchise"]["name"]
    assert me["branch"] is not None
    assert me["branch"]["name"]


async def test_me_branch_is_null_for_hq(client):
    body = await _login(client, HQ)
    res = await client.get(ME, headers={"Authorization": f"Bearer {body['token']}"})
    assert res.status_code == 200, res.text
    me = res.json()
    assert me["user_type"] == "HQ"
    assert me["branch"] is None


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer bogus"}, {"Authorization": "Basic abc"}],
)
async def test_me_rejects_bad_auth(client, headers):
    res = await client.get(ME, headers=headers)
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


async def test_refresh_rotates_and_invalidates_old_token(client):
    first = await _login(client, OWNER)

    res = await client.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert res.status_code == 200, res.text
    second = res.json()
    assert second["refresh_token"] != first["refresh_token"]

    # 새 access token이 실제로 동작한다
    me = await client.get(ME, headers={"Authorization": f"Bearer {second['token']}"})
    assert me.status_code == 200

    # 회전된 이전 refresh token은 재사용 불가
    reused = await client.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert reused.status_code == 401


async def test_logout_revokes_refresh_token(client):
    body = await _login(client, OWNER)

    res = await client.post(LOGOUT, json={"refresh_token": body["refresh_token"]})
    assert res.status_code == 204

    after = await client.post(REFRESH, json={"refresh_token": body["refresh_token"]})
    assert after.status_code == 401


async def test_logout_is_idempotent(client):
    body = await _login(client, OWNER)
    assert (await client.post(LOGOUT, json={"refresh_token": body["refresh_token"]})).status_code == 204
    assert (await client.post(LOGOUT, json={"refresh_token": body["refresh_token"]})).status_code == 204


async def test_signup_endpoint_is_not_implemented(client):
    """회원가입은 이번 Phase 범위 밖이다."""
    res = await client.post(
        "/api/v1/auth/signup", json={"email": "new@example.com", "password": "x"}
    )
    assert res.status_code == 404
