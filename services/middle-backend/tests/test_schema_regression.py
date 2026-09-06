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


# ORM 이 DB 보다 적게 알면 autogenerate 가 "지우자"고 제안한다. 그 제안이 그대로
# revision 이 되면 마이그레이션이 만든 제약이 조용히 사라진다.
#
# `remove_table` 도 함께 본다. `0005_siren_operational_storage` 가 만드는 사이렌
# 원천·리뷰 테이블은 ORM 에 모델이 없어서, 필터가 없으면 autogenerate 가 세 테이블을
# DropTable 로 제안한다. 그 revision 이 병합되면 사이렌 원천 데이터가 사라진다.
_DROP_OPS = ("remove_index", "remove_fk", "remove_constraint", "remove_table")


async def test_autogenerate_does_not_drop_migration_constraints(database):
    """autogenerate 가 마이그레이션이 만든 제약을 삭제 대상으로 제안하지 않는다.

    인덱스는 `include_object` 으로 비교에서 빼므로 애초에 잡히지 않는다.
    unique·FK·CHECK 는 ORM 에 선언돼 있어야 하고, 빠지면 여기서 걸린다.
    실제로 0003 의 fk_branch_owner_same_franchise 를 ORM 에 넣기 전에는
    이 테스트가 DropConstraintOp 로 실패했다.
    """
    from app.db.session import engine

    async with engine.connect() as connection:
        diffs = await connection.run_sync(_index_diffs)

    drops = [d for d in diffs if isinstance(d, tuple) and str(d[0]) in _DROP_OPS]
    assert drops == [], f"제약 삭제가 제안됐다: {drops}"

    # 삭제가 아닌 차이는 실패시키지 않는다. 문서 DDL 과 ORM 은 의도적으로 완전히
    # 같지 않다(COMMENT 등). 다만 눈에 띄게 남겨 둔다.
    if diffs:
        print(f"\n[참고] autogenerate 차이 {len(diffs)}건: {diffs}")


def test_orm_declares_the_franchise_boundary_constraints():
    """이슈 #7 의 두 제약이 ORM 메타데이터에 있다.

    위 autogenerate 테스트는 실제 DB 가 있어야 돌지만 이건 메타데이터만 본다.
    제약을 지우면 DB 없이도 즉시 걸린다.
    """
    from app.models import Branch, UserAccount

    user_uniques = {
        c.name for c in UserAccount.__table__.constraints if isinstance(c, sa.UniqueConstraint)
    }
    assert "uq_user_id_franchise" in user_uniques

    branch_fks = {c.name for c in Branch.__table__.constraints if isinstance(c, sa.ForeignKeyConstraint)}
    assert "fk_branch_owner_same_franchise" in branch_fks
