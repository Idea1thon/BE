"""부분 적용된 baseline 의 자동생성·기본값 표류를 복구한다.

0001 은 이미 있는 객체의 `CREATE` 문을 **객체 단위로** 건너뛴다. 그래야
`alembic_version` 이 없는 채로 스키마 일부가 있는 DB(운영 DB 를 `fmp` 에서
`ideaton` 으로 옮긴 상태) 위에서 `upgrade` 가 DuplicateTable 로 죽지 않는다.

그 대가로 **테이블은 있지만 안이 다른** 경우를 못 고친다. 실제로 걸렸다:

    NotNullViolationError: null value in column "id" of relation "franchise"

`franchise.id` 가 `NOT NULL` 인데 `BIGSERIAL` 이 만들어 주는 시퀀스와
`DEFAULT nextval(...)` 이 없다. 0001 의 `CREATE TABLE franchise` 는 테이블이
있으므로 건너뛰어졌고, 컬럼은 있으니 `ADD COLUMN IF NOT EXISTS` 류에도 걸리지
않는다. 애플리케이션이 `id` 를 서버에 맡기고 INSERT 하는 순간 실패한다.

같은 전략에서 나올 수 있는 표류는 세 갈래다.

1. **PK 자동생성 누락** — 시퀀스 자체가 없거나, 시퀀스는 있는데 컬럼 `DEFAULT`
   가 안 붙어 있는 경우. 0001 의 `BIGSERIAL` 컬럼 7개가 대상이다.
2. **시퀀스가 기존 최대값보다 뒤처짐** — 행을 다른 경로로(덤프 복원·수동 INSERT)
   넣으면 시퀀스가 따라가지 못해, 자동생성을 고치자마자 중복 키로 죽는다.
3. **NOT NULL 컬럼 누락** — 기본값이 있는 컬럼은 지금 채워 넣을 수 있다.
   기본값이 없는 NOT NULL 컬럼은 기존 행에 넣을 값을 **지어낼 수 없으므로**
   자동으로 만들지 않고 목록으로 알린다.

이 리비전은 행을 지우거나 바꾸지 않는다. 하는 일은 시퀀스 생성, 컬럼 `DEFAULT`
설정, `setval` 로 시퀀스 위치 맞추기, 누락된 기본값 있는 컬럼 추가뿐이다.
`setval` 은 현재 위치를 **앞으로만** 옮긴다 — 이미 더 나아간 시퀀스를 되돌리면
기존 값과 충돌한다.

정상 경로(빈 DB → `upgrade head`)에서는 전부 no-op 이다. 반복 실행해도 같다.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from app.db.migration_guards import column_exists, relation_exists

revision = "0007_repair_serial_defaults"
down_revision = "0006_merge_siren_and_regions"
branch_labels = None
depends_on = None


# 0001 이 `BIGSERIAL` 로 만든 컬럼. 여기 없는 PK 는 자연키(`region.code` 등)나
# 상위 테이블 FK(`report_analysis.report_id`)라 자동생성이 필요 없다.
SERIAL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("franchise", "id"),
    ("user_account", "id"),
    ("branch", "id"),
    ("operation_report", "id"),
    ("notification", "id"),
    ("financial_product", "id"),
    ("refresh_token", "id"),
)


# 0001 의 컬럼 정의를 이 시점 기준으로 고정한 것. 값이 `None` 인 컬럼은 자동으로
# 만들지 않는다 — PK·식별 컬럼이라 기존 행에 부여할 값을 지어낼 수 없다.
#
# `report_analysis.risk_score`·`risk_level` 은 0004 가 nullable 로 바꿨으므로
# 0001 이 아니라 **0004 이후 모양**으로 적는다. 이 리비전이 0004 뒤에 실행되므로
# 여기서 다시 NOT NULL 을 걸면 부분 분석 결과를 못 받는 상태로 되돌아간다.
#
# 0004·0005 가 추가하는 컬럼(`trade_area_code`, `fixture_key`, `synthetic` 등)은
# 그쪽에서 이미 `ADD COLUMN IF NOT EXISTS` 로 처리하므로 중복해서 적지 않는다.
BASELINE_COLUMNS: dict[str, tuple[tuple[str, str | None], ...]] = {
    "region": (
        ("code", None),
        ("parent_code", "VARCHAR(20)"),
        ("level", "region_level NOT NULL"),
        ("name", "VARCHAR(50) NOT NULL"),
    ),
    "business_category": (
        ("code", None),
        ("name", "VARCHAR(50) NOT NULL"),
    ),
    "franchise": (
        ("id", None),
        ("name", "VARCHAR(100) NOT NULL"),
        ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
    ),
    "user_account": (
        ("id", None),
        ("franchise_id", "BIGINT NOT NULL"),
        ("email", "VARCHAR(255) NOT NULL"),
        ("password_hash", "VARCHAR(255) NOT NULL"),
        ("user_type", "user_type NOT NULL"),
        ("name", "VARCHAR(50) NOT NULL"),
        ("is_active", "BOOLEAN NOT NULL DEFAULT TRUE"),
        ("last_login_at", "TIMESTAMPTZ"),
        ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
    ),
    "branch": (
        ("id", None),
        ("franchise_id", "BIGINT NOT NULL"),
        ("owner_user_id", "BIGINT NOT NULL"),
        ("name", "VARCHAR(100) NOT NULL"),
        ("address", "VARCHAR(255) NOT NULL"),
        ("region_code", "VARCHAR(20) NOT NULL"),
        ("business_category_code", "VARCHAR(20) NOT NULL"),
        ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
    ),
    "report_input_field": (
        ("code", None),
        ("name", "VARCHAR(50) NOT NULL"),
        ("group_name", "VARCHAR(30) NOT NULL"),
        ("is_required", "BOOLEAN NOT NULL"),
        ("display_order", "SMALLINT NOT NULL"),
    ),
    "operation_report": (
        ("id", None),
        ("branch_id", "BIGINT NOT NULL"),
        ("report_month", "DATE NOT NULL"),
        ("status", "report_status NOT NULL DEFAULT 'DRAFT'"),
        ("input_source", "input_source NOT NULL DEFAULT 'MANUAL'"),
        ("net_sales", "NUMERIC(14,0)"),
        ("analysis_request_id", "UUID"),
        ("analysis_error", "TEXT"),
        ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
        ("updated_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
    ),
    "report_input_item": (
        ("report_id", None),
        ("field_code", None),
        ("amount", "NUMERIC(14,0) NOT NULL"),
    ),
    "report_analysis": (
        ("report_id", None),
        ("risk_score", "SMALLINT"),
        ("risk_level", "risk_level"),
        ("factors", "JSONB NOT NULL"),
        ("risk_periods", "JSONB NOT NULL"),
        ("recommendations", "JSONB NOT NULL"),
        ("rule_version", "VARCHAR(60) NOT NULL"),
        ("calculated_at", "TIMESTAMPTZ NOT NULL"),
    ),
    "notification": (
        ("id", None),
        ("recipient_user_id", "BIGINT NOT NULL"),
        ("report_id", "BIGINT NOT NULL"),
        ("message", "TEXT NOT NULL"),
        ("is_read", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("read_at", "TIMESTAMPTZ"),
        ("email_status", "email_status NOT NULL DEFAULT 'PENDING'"),
        ("email_sent_at", "TIMESTAMPTZ"),
        ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
    ),
    "financial_product": (
        ("id", None),
        ("target_risk_level", "risk_level NOT NULL"),
        ("name", "VARCHAR(100) NOT NULL"),
        ("description", "TEXT"),
        ("link_url", "VARCHAR(500) NOT NULL"),
        ("display_order", "SMALLINT NOT NULL DEFAULT 0"),
        ("is_active", "BOOLEAN NOT NULL DEFAULT TRUE"),
    ),
    "refresh_token": (
        ("id", None),
        ("user_id", "BIGINT NOT NULL"),
        ("token_hash", "VARCHAR(255) NOT NULL"),
        ("expires_at", "TIMESTAMPTZ NOT NULL"),
        ("revoked_at", "TIMESTAMPTZ"),
        ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
    ),
}


def _quote(identifier: str) -> str:
    """식별자를 안전하게 감싼다.

    이름은 이 파일에 고정된 allowlist 에서만 오지만, 인용은 해 둔다 — 나중에
    누가 목록에 예약어를 추가해도 조용히 깨지지 않는다.
    """
    return '"' + identifier.replace('"', '""') + '"'


def _has_rows(table: str) -> bool:
    return bool(
        op.get_bind()
        .execute(text(f"SELECT EXISTS (SELECT 1 FROM {_quote(table)} LIMIT 1)"))
        .scalar()
    )


def _serial_sequence(table: str, column: str) -> str | None:
    """컬럼에 붙은 시퀀스의 정규화된 이름. 없으면 None.

    `pg_get_serial_sequence` 는 `DEFAULT nextval(...)` 로 붙은 시퀀스와 identity
    컬럼의 내부 시퀀스를 모두 찾아 준다. 그래서 두 경우를 따로 다룰 필요가 없다.
    """
    return (
        op.get_bind()
        .execute(
            text("SELECT pg_get_serial_sequence(:table, :column)"),
            {"table": table, "column": column},
        )
        .scalar()
    )


def _align_sequence(sequence: str, table: str, column: str) -> None:
    """시퀀스를 `MAX(id)` 다음으로 맞춘다. 앞으로만 움직인다.

    `is_called=false` 로 넣으므로 다음 `nextval` 이 정확히 그 값을 돌려준다.
    `pg_sequence_last_value` 는 아직 한 번도 쓰이지 않은 시퀀스에서 NULL 이라
    빈 테이블에서는 1 부터 시작한다.

    되돌리지 않는 것이 중요하다. 이미 더 나아간 시퀀스를 `MAX(id)+1` 로 낮추면
    그 사이에 발급된 값과 충돌한다.

    `CAST(:sequence AS regclass)` 로 쓴다. `:sequence::regclass` 는 SQLAlchemy 의
    바인드 파라미터 정규식이 뒤에 붙은 `:` 때문에 파라미터로 인식하지 않아
    "syntax error at or near :" 로 죽는다.
    """
    op.get_bind().execute(
        text(
            "SELECT setval(:sequence, GREATEST("
            f"  (SELECT coalesce(max({_quote(column)}), 0) FROM {_quote(table)}) + 1,"
            "   coalesce(pg_sequence_last_value(CAST(:sequence AS regclass)) + 1, 1)"
            "), false)"
        ),
        {"sequence": sequence},
    )


def _repair_serial(table: str, column: str) -> None:
    sequence = _serial_sequence(table, column)
    if sequence is None:
        # 자동생성이 아예 없다. 시퀀스를 만들고 컬럼에 묶는다.
        #
        # OWNED BY 를 붙이는 이유: 테이블·컬럼이 사라질 때 시퀀스도 같이 정리된다.
        # 붙이지 않으면 고아 시퀀스가 남고, 이후 pg_get_serial_sequence 도 못 찾는다.
        sequence_name = f"{table}_{column}_seq"
        quoted = _quote(sequence_name)
        op.execute(f"CREATE SEQUENCE IF NOT EXISTS {quoted} AS bigint")
        op.execute(f"ALTER SEQUENCE {quoted} OWNED BY {_quote(table)}.{_quote(column)}")
        op.execute(
            f"ALTER TABLE {_quote(table)} ALTER COLUMN {_quote(column)} "
            f"SET DEFAULT nextval('{sequence_name}'::regclass)"
        )
        sequence = _serial_sequence(table, column)
        if sequence is None:  # pragma: no cover — 위 DDL 이 성공하면 반드시 잡힌다
            raise RuntimeError(
                f"{table}.{column} 의 시퀀스를 만들었는데 연결되지 않았다. "
                "수동 확인이 필요하다."
            )
    _align_sequence(sequence, table, column)


def upgrade() -> None:
    unrecoverable: list[str] = []

    # ── 1·2. PK 자동생성 복구 + 시퀀스 위치 맞추기
    for table, column in SERIAL_COLUMNS:
        if not relation_exists(table):
            # 0001 이 만들었어야 한다. 없으면 아래 컬럼 검사에서 다루지 않고
            # 넘어가며, 실제로는 0001 이 이미 만들었으므로 도달하지 않는다.
            continue
        if not column_exists(table, column):
            unrecoverable.append(f"{table}.{column} (PK 컬럼 자체가 없음)")
            continue
        _repair_serial(table, column)

    # ── 3. 누락된 컬럼 복구. 기본값이 없는 NOT NULL 은 값을 지어내지 않고 알린다.
    for table, columns in BASELINE_COLUMNS.items():
        if not relation_exists(table):
            continue
        table_has_rows = _has_rows(table)
        for column, definition in columns:
            if column_exists(table, column):
                continue
            if definition is None:
                unrecoverable.append(f"{table}.{column} (식별 컬럼 — 값을 만들 수 없음)")
                continue
            upper = definition.upper()
            if "NOT NULL" in upper and "DEFAULT" not in upper and table_has_rows:
                unrecoverable.append(
                    f"{table}.{column} ({definition}) — 기존 행에 넣을 값이 없음"
                )
                continue
            op.execute(
                f"ALTER TABLE {_quote(table)} ADD COLUMN IF NOT EXISTS "
                f"{_quote(column)} {definition}"
            )

    if unrecoverable:
        raise RuntimeError(
            "baseline 스키마 표류를 자동으로 복구할 수 없다. 아래 컬럼을 채울 값을 "
            "정한 뒤 별도 마이그레이션으로 처리할 것 — 이 리비전은 값을 지어내지 "
            "않는다:\n  - " + "\n  - ".join(unrecoverable)
        )


def downgrade() -> None:
    """되돌리지 않는다.

    이 리비전이 한 일은 자동생성 복구다. `DEFAULT` 를 떼거나 시퀀스를 지우면
    복구 대상이던 고장 상태로 되돌아가고, 그 사이에 발급된 PK 값은 그대로 남는다.
    추가한 컬럼도 지우면 그 컬럼에 들어간 행 데이터가 사라진다.
    """
