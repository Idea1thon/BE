"""테스트 픽스처. 실제 PostgreSQL 테스트 DB를 사용한다.

SQLite로 대체하지 않는다 — native ENUM·JSONB·부분 인덱스를 쓰기 때문에
SQLite에서 통과해도 운영 DB에서의 동작을 보장하지 못한다.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx
import pytest

# `app.*` 는 여기서 import 하지 않는다. app.core.config 가 import 시점에
# settings 를 만들면서 .env(Azure, ssl=require)를 읽어버리고, 아래에서 세팅하는
# DATABASE_URL / DB_USE_NULL_POOL 이 무시된다. 필요한 곳에서 함수 안에 import 한다.

# alembic.ini 가 있는 곳. pytest 를 어느 디렉터리에서 부르든 같은 곳을 가리킨다.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

TEST_DB = os.getenv("TEST_DB_NAME", "fmp_test")

# 안전장치: 이 파일은 DB를 DROP/CREATE 한다. 개발·운영 DB를 실수로 지우지 않도록
# 이름이 `_test`로 끝나는 DB만 허용한다.
if not TEST_DB.endswith("_test"):
    raise RuntimeError(
        f"테스트 DB 이름은 '_test'로 끝나야 한다 (받은 값: {TEST_DB!r}). "
        "이 fixture는 대상 DB를 삭제·재생성한다."
    )

TEST_DB_URL = f"postgresql+asyncpg://localhost:5432/{TEST_DB}"
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["DB_USE_NULL_POOL"] = "true"
# The report endpoint deliberately keeps Siren opt-in in deployed environments.
# Integration tests opt in so the background task and persistence contract are
# exercised instead of asserting only the initial ANALYZING state.
os.environ["SIREN_ANALYSIS_ENABLED"] = "true"


def _run(cmd: list[str], **kwargs) -> None:
    """실패하면 상대가 뱉은 stderr 를 그대로 올린다.

    `check=True` + `capture_output=True` 만 쓰면 CalledProcessError 에 종료
    코드만 남아, alembic 이나 psql 이 왜 죽었는지 픽스처 밖에서 볼 수 없다.
    """
    done = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if done.returncode != 0:
        raise RuntimeError(
            f"{' '.join(cmd)} 실패 (exit {done.returncode})\n"
            f"--- stdout ---\n{done.stdout}\n--- stderr ---\n{done.stderr}"
        )


def _psql(db: str, sql: str) -> None:
    _run(["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", db, "-c", sql])


def _drop_test_db() -> None:
    # WITH (FORCE)는 PostgreSQL 13+. 앱 엔진이 커넥션을 잡고 있어도 삭제된다.
    _psql("postgres", f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")


@pytest.fixture(scope="session", autouse=True)
def database() -> AsyncGenerator[None, None]:
    _drop_test_db()
    _psql("postgres", f"CREATE DATABASE {TEST_DB}")
    # `alembic` 실행 파일이 아니라 지금 pytest 를 돌리는 인터프리터의 모듈로
    # 부른다. `.venv/bin/pytest` 처럼 venv 를 activate 하지 않고 직접 실행하면
    # PATH 에 `.venv/bin` 이 없어 `alembic` 을 못 찾는다(FileNotFoundError).
    _run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "DATABASE_URL": TEST_DB_URL},
    )
    yield
    try:
        from app.db.session import engine

        asyncio.run(engine.dispose())
    except Exception:  # noqa: BLE001 — 정리 실패가 테스트 결과를 가리지 않도록
        pass
    _drop_test_db()


@pytest.fixture(scope="session", autouse=True)
def seeded(database) -> None:
    from scripts.seed import seed

    asyncio.run(seed())


@pytest.fixture
async def client() -> AsyncGenerator:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    # raise_app_exceptions=False: 실제 HTTP 클라이언트처럼 예외 대신 500 응답을 받는다.
    # 이렇게 해야 500 공통 오류 봉투를 검증할 수 있다.
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as ac:
        yield ac

# --------------------------------------------------------------------------- #
# 사이렌 가짜 서버
# --------------------------------------------------------------------------- #
# POST /reports 가 제출 직후 위험도 분석을 부른다. 테스트에서 실제 서비스를
# 띄우지 않으므로 기본으로 가짜를 세운다. 개별 테스트는 monkeypatch 로 덮는다.
#
# 응답은 services/siren/pipeline.py 의 analyze() 결과 구조를 따른다. 우리
# 가정대로 지어내면 계약 불일치를 테스트가 못 잡는다.
SIREN_CALCULATED = {
    "request_id": "test",
    "branch": {"franchise_id": "1", "branch_id": "1", "as_of": "2026-08-31"},
    "risk": {
        "score": 74.1944,
        "grade": "위험",
        "score_version": "risk-siren-v1.2-provisional",
        "calculation_status": "calculated",
        "policy_status": "provisional",
    },
    "layers": {"market_risk": {"score": 55.0, "status": "calculated"},
               "branch_risk": {"score": 84.0, "status": "calculated"}},
    "components": {"closure": {"score": 40.0}, "sales_decline": {"market": {}, "branch": {}},
                   "competition": {"score": 20.0}, "profitability": {"score": 90.0}},
    "review_signal": {"status": "missing"},
    "evidence": [], "missing_data": [], "uncertainty": [], "excluded_future_months": [],
    "data_provenance": {"contains_synthetic": False},
    "alert": {"should_fire": True, "alert_policy_version": "confirmed-branch-v1",
              "trigger": {"basis": "composite"}},
    "financial_products": {"status": "catalog_match_pending", "items": []},
    "explanation": {"text": "테스트", "model": "deterministic-template-v1"},
    "projections": {"branch_owner": {"recommended_actions": []}},
    "franchise_closure": {"status": "missing"},
}


@pytest.fixture(autouse=True)
def _siren(monkeypatch):
    """기본 가짜 사이렌.

    `analyze()` 가 아니라 그 아래 HTTP 경계(`get_client`)를 가짜로 세운다.
    `analyze` 를 통째로 바꿔치우면 상태코드 변환·오류 봉투 같은 우리 코드가
    테스트에서 아예 실행되지 않고, `siren_client` 자체를 검사하는 테스트
    (tests/test_siren.py 6장)가 자기가 세운 응답 대신 이 가짜를 받는다.

    반환값을 바꾸려면 테스트에서 `get_client` 나 `analyze` 를 다시
    monkeypatch 한다 — 나중에 적용된 쪽이 이긴다.
    """
    from app.services import siren_client

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SIREN_CALCULATED)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler), base_url="http://siren.test"
    )
    monkeypatch.setattr(siren_client, "get_client", lambda: client)
