"""사이렌 연동에 필요한 스키마 보강.

두 갈래다.

**1. `report_analysis` 가 사이렌 결과를 담을 수 있게 한다.**

사이렌은 시장 층이나 가맹점 층 중 하나라도 완전히 계산되지 않으면 종합 점수·등급을
**의도적으로 null** 로 준다(`services/siren/pipeline.py`: `"score": composite if
both_calculated else None`). "데이터 부족을 안전으로 표시하지 않는다" 는 설계이고,
그 결과를 우리가 못 받으면 부분 분석이 통째로 버려진다. 두 컬럼을 nullable 로
바꾸고 `calculation_status` 로 확정/부분을 구분한다.

`rule_version` 은 VARCHAR(20) 인데 상대 `score_version` 이
"risk-siren-v1.2-provisional"(27자)다. 잘라 넣으면 재현성 정보가 깨지므로 넓힌다.
`alert_policy_version` 은 점수와 별개로 경고 발생 조건을 결정하므로 컬럼을 나눈다.

제약 완화·타입 확장·컬럼 추가뿐이라 기존 행을 깨뜨리지 않는다.

**2. `branch` 에 좌표·상권 코드를 둔다.**

사이렌 `BranchLocation` 은 `trade_area_code`·`x_5181`·`y_5181` 을 필수로 받는데
우리는 주소 문자열만 갖고 있었다. 전부 nullable 로 두어 값이 없는 점포는 지금처럼
분석 대상에서 빠지게 한다 — 좌표를 지어내면 다른 상권의 위험도가 그 점포 것으로
표시된다.

0001~0003 과 같은 이유로 재실행이 가능해야 한다. `op.add_column` 은 `IF NOT EXISTS`
를 만들지 못하므로 raw `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 로 바꿨다.
생성되는 컬럼 타입·NULL 여부·기본값은 이전과 같다. `DROP NOT NULL` 과
`ALTER COLUMN ... TYPE` 은 원래 멱등이라 그대로 둔다.
"""

from alembic import op

from app.db.migration_guards import constraint_exists

revision = "0004_siren_integration"
down_revision = "0003_integrity_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── report_analysis: 부분 분석 결과 수용
    op.execute("ALTER TABLE report_analysis ALTER COLUMN risk_score DROP NOT NULL")
    op.execute("ALTER TABLE report_analysis ALTER COLUMN risk_level DROP NOT NULL")
    op.execute("ALTER TABLE report_analysis ALTER COLUMN rule_version TYPE VARCHAR(60)")
    op.execute(
        "ALTER TABLE report_analysis ADD COLUMN IF NOT EXISTS "
        "calculation_status VARCHAR(20) NOT NULL DEFAULT 'calculated'"
    )
    op.execute(
        "ALTER TABLE report_analysis ADD COLUMN IF NOT EXISTS "
        "alert_policy_version VARCHAR(60)"
    )
    # 점수와 등급은 함께 있거나 함께 없어야 한다. 사이렌 계약(HqRisk)과 같은 규칙이다.
    # 한쪽만 있는 행은 목록 정렬(risk_level)과 상세(risk_score)가 어긋난다.
    if not constraint_exists("ck_analysis_score_grade_together"):
        op.execute(
            "ALTER TABLE report_analysis ADD CONSTRAINT ck_analysis_score_grade_together "
            "CHECK ((risk_score IS NULL) = (risk_level IS NULL))"
        )

    # ── branch: 사이렌이 요구하는 위치 입력
    op.execute(
        "ALTER TABLE branch ADD COLUMN IF NOT EXISTS trade_area_code VARCHAR(20)"
    )
    op.execute("ALTER TABLE branch ADD COLUMN IF NOT EXISTS x_5181 NUMERIC(12, 2)")
    op.execute("ALTER TABLE branch ADD COLUMN IF NOT EXISTS y_5181 NUMERIC(12, 2)")
    # 좌표는 두 값이 함께 있어야 의미가 있다. 하나만 있으면 잘못된 지점을 가리킨다.
    if not constraint_exists("ck_branch_coords_together"):
        op.execute(
            "ALTER TABLE branch ADD CONSTRAINT ck_branch_coords_together "
            "CHECK ((x_5181 IS NULL) = (y_5181 IS NULL))"
        )


def downgrade() -> None:
    op.execute("ALTER TABLE branch DROP CONSTRAINT IF EXISTS ck_branch_coords_together")
    op.execute("ALTER TABLE branch DROP COLUMN IF EXISTS y_5181")
    op.execute("ALTER TABLE branch DROP COLUMN IF EXISTS x_5181")
    op.execute("ALTER TABLE branch DROP COLUMN IF EXISTS trade_area_code")

    op.execute(
        "ALTER TABLE report_analysis DROP CONSTRAINT IF EXISTS "
        "ck_analysis_score_grade_together"
    )
    op.execute("ALTER TABLE report_analysis DROP COLUMN IF EXISTS alert_policy_version")
    op.execute("ALTER TABLE report_analysis DROP COLUMN IF EXISTS calculation_status")
    op.execute("ALTER TABLE report_analysis ALTER COLUMN rule_version TYPE VARCHAR(20)")
    # NOT NULL 복구는 null 행이 없을 때만 성공한다. 되돌리기 전에 정리해야 한다.
    op.execute("ALTER TABLE report_analysis ALTER COLUMN risk_level SET NOT NULL")
    op.execute("ALTER TABLE report_analysis ALTER COLUMN risk_score SET NOT NULL")
