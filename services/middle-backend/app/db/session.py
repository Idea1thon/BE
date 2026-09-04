"""Async engine / session."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

# 테스트는 이벤트 루프가 테스트마다 새로 만들어진다. asyncpg 커넥션은 루프에
# 묶여 있어 풀을 재사용하면 "another operation is in progress"가 발생한다.
# 그래서 테스트에서만 NullPool을 쓴다.
_engine_kwargs = {"echo": settings.db_echo, "future": True}
if settings.db_use_null_pool:
    _engine_kwargs["poolclass"] = NullPool

engine = create_async_engine(settings.database_url, **_engine_kwargs)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
