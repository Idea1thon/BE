"""Alembic autogenerate 대상 필터.

물리 스키마 정본은 DB_SCHEMA 문서의 DDL이고, ORM 모델에는 인덱스를 선언하지 않는다.
그대로 두면 autogenerate가 마이그레이션이 만든 인덱스를 전부 삭제 대상으로 제안한다.

`alembic/env.py`와 회귀 테스트가 같은 판정을 쓰도록 여기 한 곳에만 둔다.
"""

from __future__ import annotations


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """인덱스는 autogenerate 비교에서 제외한다."""
    return type_ != "index"
