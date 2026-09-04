"""테스트 픽스처. 실제 PostgreSQL 테스트 DB를 사용한다.

SQLite로 대체하지 않는다 — native ENUM·JSONB·부분 인덱스를 쓰기 때문에
SQLite에서 통과해도 운영 DB에서의 동작을 보장하지 못한다.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import AsyncGenerator

import pytest

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


def _psql(db: str, sql: str) -> None:
    subprocess.run(
        ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", db, "-c", sql],
        check=True,
        capture_output=True,
    )


def _drop_test_db() -> None:
    # WITH (FORCE)는 PostgreSQL 13+. 앱 엔진이 커넥션을 잡고 있어도 삭제된다.
    _psql("postgres", f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")


@pytest.fixture(scope="session", autouse=True)
def database() -> AsyncGenerator[None, None]:
    _drop_test_db()
    _psql("postgres", f"CREATE DATABASE {TEST_DB}")
    subprocess.run(
        ["alembic", "upgrade", "head"],
        check=True,
        capture_output=True,
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
