"""마이그레이션 멱등성 판정용 카탈로그 조회.

`alembic upgrade` 가 **이미 부분적으로 만들어진 DB** 위에서도 돌 수 있어야 한다.
운영 DB가 `fmp` 에서 `ideaton` 으로 옮겨졌는데 `ideaton` 에는 `alembic_version`
이 없고 스키마 일부만 있는 상태가 실제로 발생했다. 그때 0001 이 처음부터 다시
실행되면서 `CREATE TABLE` 이 DuplicateTable 로 죽는다.

PostgreSQL 은 `CREATE TABLE/INDEX IF NOT EXISTS` 는 지원하지만
`CREATE TYPE IF NOT EXISTS` 와 `ADD CONSTRAINT IF NOT EXISTS` 는 없다. 그래서
DDL 문 자체를 고치는 대신 실행 전에 카탈로그를 보고 건너뛴다.

이 모듈은 **스키마를 알지 못한다**. 테이블·컬럼 이름을 하드코딩하지 않고 인자로
받는다. 마이그레이션은 과거 시점에 고정된 코드이므로, 여기에 스키마 지식을 넣으면
나중에 모델이 바뀔 때 옛 리비전의 동작이 조용히 바뀐다. 함수 추가만 하고 기존
함수의 의미는 바꾸지 않는다.

`DROP` 계열 판정은 두지 않는다. 이 모듈의 용도는 "없으면 만든다"뿐이고, 삭제를
쉽게 만들 이유가 없다.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


def _scalar(sql: str, /, **params: object) -> bool:
    """현재 마이그레이션 커넥션에서 존재 여부 한 건을 읽는다."""
    return bool(op.get_bind().execute(text(sql), params).scalar())


def relation_exists(name: str) -> bool:
    """테이블·뷰·인덱스·시퀀스 등 `pg_class` 관계가 있는지.

    `to_regclass` 는 search_path 를 따르므로 스키마를 가정하지 않는다. 없으면
    예외 대신 NULL 을 돌려주기 때문에 존재 확인에 그대로 쓸 수 있다.
    """
    return _scalar("SELECT to_regclass(:name) IS NOT NULL", name=name)


def type_exists(name: str) -> bool:
    """enum 등 사용자 정의 타입이 있는지. `to_regtype` 도 search_path 를 따른다."""
    return _scalar("SELECT to_regtype(:name) IS NOT NULL", name=name)


def constraint_exists(name: str) -> bool:
    """현재 스키마에 같은 이름의 제약이 있는지.

    `pg_constraint.conname` 은 스키마 안에서만 유일하므로 namespace 를 함께 본다.
    """
    return _scalar(
        "SELECT 1 FROM pg_constraint "
        "WHERE conname = :name AND connamespace = current_schema()::regnamespace",
        name=name,
    )


def column_exists(table: str, column: str) -> bool:
    """현재 스키마의 테이블에 해당 컬럼이 있는지."""
    return _scalar(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = current_schema() "
        "AND table_name = :table AND column_name = :column",
        table=table,
        column=column,
    )
