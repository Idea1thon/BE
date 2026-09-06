"""두 갈래로 갈라진 head 를 합친다 — DDL 변경 없음.

PR #36 의 `0005_siren_operational_storage` 와 PR #46 의 `0005_seoul_all_regions` 가
둘 다 `0004_siren_integration` 을 부모로 잡은 채 따로 병합됐다. 그 결과 head 가
2개가 되어 `alembic upgrade head` 가 아예 실행되지 않았다.

    FAILED: Multiple head revisions are present for given argument 'head'

`tests/conftest.py` 의 세션 픽스처가 `alembic upgrade head` 를 돌리므로 이 상태에서는
middle-backend 테스트 전체가 수집 단계에서 죽고, 컨테이너 기동
(`docker-entrypoint.sh` 의 `set -e` + `alembic upgrade head`)도 같은 자리에서 멈춘다.

이 리비전은 순수 merge 다. 두 갈래는 서로 다른 객체를 다루므로(한쪽은 사이렌 저장
테이블·컬럼, 다른 한쪽은 `region` 기준정보 행) 실행 순서에 의존하는 충돌이 없고,
합치는 데 추가 DDL 이 필요하지 않다. 두 갈래 중 한쪽을 다른 쪽 뒤로 옮겨 붙이는
방식(down_revision 재작성)은 이미 각 갈래를 개별 적용한 로컬 DB 의 `alembic_version`
과 어긋나므로 쓰지 않았다.

`downgrade` 도 비어 있다. merge 를 되돌리면 alembic 이 다시 두 갈래로 갈라지고,
그 상태가 정확히 지금 고치려는 문제다.

리비전 id 는 32자를 넘기지 않는다. `alembic_version.version_num` 이 VARCHAR(32) 라
긴 id 를 쓰면 마이그레이션 마지막 UPDATE 가 StringDataRightTruncationError 로 죽는다.
"""

revision = "0006_merge_siren_and_regions"
down_revision = ("0005_siren_operational_storage", "0005_seoul_all_regions")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """스키마를 바꾸지 않는다. 두 갈래를 하나의 head 로 잇기만 한다."""


def downgrade() -> None:
    """되돌리면 head 가 다시 2개가 된다. 의도적으로 비워 둔다."""
