"""Alembic 환경. DB URL은 app 설정(.env)에서 가져온다."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.db.base import Base
from app import models  # noqa: F401  — 모델 등록

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        include_object=_include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _include_object(obj, name, type_, reflected, compare_to) -> bool:
    """물리 스키마 정본은 DB_SCHEMA.md의 DDL이다.

    ORM 모델에는 인덱스를 선언하지 않으므로, 그대로 두면 autogenerate가
    마이그레이션이 만든 인덱스를 전부 삭제 대상으로 제안한다. 인덱스는 비교에서 뺀다.
    """
    return type_ != "index"


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(settings.database_url, future=True)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
