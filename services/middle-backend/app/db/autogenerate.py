"""Alembic autogenerate 대상 필터.

물리 스키마 정본은 DB_SCHEMA 문서의 DDL이고, ORM 모델에는 인덱스를 선언하지 않는다.
그대로 두면 autogenerate가 마이그레이션이 만든 인덱스를 전부 삭제 대상으로 제안한다.

`alembic/env.py`와 회귀 테스트가 같은 판정을 쓰도록 여기 한 곳에만 둔다.
"""

from __future__ import annotations

# 사이렌이 읽는 원천·리뷰 저장 테이블. `0005_siren_operational_storage` 가 만들고
# 사이렌 provider 가 조회하지만, middle-backend ORM 에는 모델이 없다(우리는 분석
# 결과만 소유한다). 비교 대상에 두면 autogenerate 가 "ORM 에 없으니 지우자"고
# DropTableOp 를 제안하고, 그 revision 이 그대로 병합되면 사이렌 원천 데이터가
# 사라진다. 모델을 추가하는 대신 소유 경계를 그대로 두고 비교에서 뺀다.
SIREN_OWNED_TABLES = frozenset(
    {
        "siren_branch_location",
        "franchise_closure_year",
        "siren_review",
    }
)


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """인덱스와 사이렌 소유 테이블은 autogenerate 비교에서 제외한다."""
    if type_ == "index":
        return False
    if type_ == "table" and name in SIREN_OWNED_TABLES:
        return False
    return True
