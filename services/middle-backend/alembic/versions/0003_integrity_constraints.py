"""무결성 제약 보강 — 프랜차이즈 경계와 보고서 월 (이슈 #7).

PR #5 리뷰의 [P2] 두 건을 DB에서 보장한다. 지금은 계정이 시드로만 만들어져
어긋난 데이터가 생길 경로가 없지만, 회원가입 API와 보고서 제출을 붙이는 순간
애플리케이션 로직만으로는 막을 수 없는 위험이 된다.
"""

from alembic import op

revision = "0003_integrity_constraints"
down_revision = "0002_notification_query_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 점주와 점포의 동일 프랜차이즈 소속
    #
    # branch.franchise_id 와 owner_user_id 가 독립 FK라, franchise B 의 사용자를
    # franchise A 의 점포 owner 로 연결할 수 있었다. HQ 권한은 branch.franchise_id 를,
    # OWNER 조회는 branch.owner_user_id 를 신뢰하므로 둘이 어긋나면 권한 경계가 갈라진다.
    #
    # composite FK 의 참조 대상이 되려면 user_account 쪽에 같은 조합의 unique 가 있어야 한다.
    op.execute(
        "ALTER TABLE user_account "
        "ADD CONSTRAINT uq_user_id_franchise UNIQUE (id, franchise_id)"
    )
    op.execute(
        "ALTER TABLE branch "
        "ADD CONSTRAINT fk_branch_owner_same_franchise "
        "FOREIGN KEY (owner_user_id, franchise_id) "
        "REFERENCES user_account (id, franchise_id)"
    )

    # ── 보고서 월은 항상 해당 월 1일
    #
    # uq_report_branch_month(branch_id, report_month) 만으로는 같은 점포에
    # 2026-08-01 과 2026-08-15 를 모두 넣을 수 있어 "월 1건"이 실제로는 보장되지 않았다.
    #
    # date_trunc 대신 EXTRACT 를 쓴다. CHECK 는 IMMUTABLE 식만 받는데,
    # date_trunc 는 인자 타입에 따라 STABLE 로 해석될 여지가 있다.
    op.execute(
        "ALTER TABLE operation_report "
        "ADD CONSTRAINT ck_report_month_first_day "
        "CHECK (EXTRACT(DAY FROM report_month) = 1)"
    )
    op.execute(
        "COMMENT ON COLUMN operation_report.report_month IS "
        "'대상 월의 1일 (REQ-OW-11). ck_report_month_first_day 로 DB가 보장한다'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE operation_report DROP CONSTRAINT ck_report_month_first_day")
    op.execute("ALTER TABLE branch DROP CONSTRAINT fk_branch_owner_same_franchise")
    op.execute("ALTER TABLE user_account DROP CONSTRAINT uq_user_id_franchise")
