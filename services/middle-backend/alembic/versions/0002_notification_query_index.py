"""알림 목록 조회용 인덱스.

0001의 `idx_notification_unread`는 `WHERE is_read = FALSE` 부분 인덱스라
필터 없는 조회와 `is_read=true` 조회에는 쓰이지 않는다. 5-1의 정렬 키와
같은 `(recipient_user_id, created_at DESC, id DESC)` 인덱스를 추가한다.

`IF NOT EXISTS` 를 쓰는 이유는 0001 과 같다 — `alembic_version` 이 없는 채로
스키마 일부가 이미 있는 DB 위에서도 `upgrade` 가 돌아야 한다.
"""

from alembic import op

revision = "0002_notification_query_index"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_notification_recipient_created "
        "ON notification (recipient_user_id, created_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_notification_recipient_created")
