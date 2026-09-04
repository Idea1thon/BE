"""스키마 회귀 테스트. PR #5 리뷰 [P2] enum create_type / [P2] 인덱스 불일치 대응.

리뷰가 요구한 "생성 SQL 회귀 테스트"다. 코드를 되돌리면 여기서 잡힌다.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app import models  # noqa: F401 — 모델 등록
from app.db.autogenerate import include_object
from app.db.base import Base


def test_metadata_create_all_emits_no_create_type():
    """`create_all()`이 enum 타입을 다시 만들지 않는다.

    generic `sqlalchemy.Enum`은 `create_type`을 인자로 받지 않아 조용히 무시된다.
    그 상태로 되돌아가면 CREATE TYPE 6건이 다시 나오고, 마이그레이션이 만든
    타입과 충돌한다.
    """
    emitted: list[str] = []
    engine = sa.create_mock_engine(
        "postgresql+psycopg2://", lambda stmt, *a, **kw: emitted.append(type(stmt).__name__)
    )
    Base.metadata.create_all(engine)

    assert emitted.count("CreateEnumType") == 0, (
        "create_all()이 CREATE TYPE을 생성한다. "
        "sqlalchemy.dialects.postgresql.ENUM(create_type=False)을 쓰고 있는지 확인할 것."
    )
    assert "CreateTable" in emitted, "테이블 DDL 자체가 생성되지 않았다 — 테스트가 무의미하다"


def test_enum_columns_use_postgresql_enum_with_create_type_false():
    """모든 enum 컬럼이 PostgreSQL 전용 ENUM이고 create_type이 꺼져 있다."""
    from sqlalchemy.dialects.postgresql import ENUM as PGEnum

    checked = 0
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, sa.Enum):
                assert isinstance(column.type, PGEnum), (
                    f"{table.name}.{column.name}: generic Enum은 create_type을 무시한다"
                )
                assert column.type.create_type is False, (
                    f"{table.name}.{column.name}: create_type이 켜져 있다"
                )
                checked += 1
    assert checked >= 6, f"enum 컬럼을 {checked}개만 확인했다 — 모델 구성을 다시 볼 것"


def _index_diffs(sync_connection):
    context = MigrationContext.configure(
        sync_connection, opts={"include_object": include_object, "compare_type": False}
    )
    return compare_metadata(context, Base.metadata)


async def test_autogenerate_does_not_drop_migration_indexes(database):
    """autogenerate가 마이그레이션이 만든 인덱스를 삭제 대상으로 제안하지 않는다.

    ORM에는 인덱스를 선언하지 않으므로 `include_object`으로 비교에서 제외한다.
    그 필터가 빠지면 인덱스 15개가 remove_index로 잡힌다.
    """
    from app.db.session import engine

    async with engine.connect() as connection:
        diffs = await connection.run_sync(_index_diffs)

    index_diffs = [d for d in diffs if isinstance(d, tuple) and str(d[0]).endswith("_index")]
    assert index_diffs == [], f"인덱스 관련 변경이 제안됐다: {index_diffs}"

    # 인덱스 외 차이는 실패시키지 않는다. 문서 DDL과 ORM은 의도적으로 완전히
    # 같지 않다(COMMENT, CHECK 등). 다만 눈에 띄게 남겨 둔다.
    if diffs:
        print(f"\n[참고] 인덱스 외 autogenerate 차이 {len(diffs)}건: {diffs}")
