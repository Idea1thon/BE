"""initial schema — DB_SCHEMA.md 7장 CREATE TABLE SQL 그대로.

이 리비전은 손으로 작성하지 않았다. `DB_SCHEMA.md`의 검증된 DDL을 그대로 옮겼다.
스키마 정본은 문서이며, app/models는 ORM 접근용이다.

asyncpg는 한 번의 execute()에 복수 문장을 허용하지 않으므로 문장 단위로 실행한다.
분리는 작은따옴표 문자열과 -- 주석을 인식하는 스캐너로 수행한다.

**이미 일부가 만들어진 DB 위에서도 실행된다.** 운영 DB를 `fmp` 에서 `ideaton`
으로 옮겼을 때 `ideaton` 에는 `alembic_version` 이 없고 이 리비전의 객체 일부가
이미 있었다. 그 상태에서 `upgrade` 를 걸면 `CREATE TABLE` 이 DuplicateTable 로
죽어 배포가 멈춘다.

`DDL` 문자열은 문서 DDL 그대로 두고, 실행 직전에 카탈로그를 보고 이미 있는 객체의
생성문만 건너뛴다. PostgreSQL 에 `CREATE TYPE IF NOT EXISTS` 가 없어서 DDL 본문에
`IF NOT EXISTS` 를 심는 방식으로는 6개 enum 을 처리할 수 없고, 문서와 다른 SQL 을
남기면 "문서 DDL 그대로" 라는 이 리비전의 전제도 깨진다.

건너뛰기는 **객체 단위**다. 테이블이 있는데 컬럼이 빠진 경우는 여기서 메우지 않는다.
그런 표류는 조용히 덮으면 안 되므로 `COMMENT ON COLUMN` 이나 후속 리비전에서
드러나게 둔다. 기존 행을 지우거나 바꾸는 문장은 이 리비전에 없다.
"""

import re
from collections.abc import Iterator

from alembic import op

from app.db.migration_guards import relation_exists, type_exists

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

DDL = """
-- =====================================================================
-- Franchise Management Platform · Backend Service DB
-- PostgreSQL 14+
-- =====================================================================

-- ---------- Enum 타입 ----------
CREATE TYPE user_type     AS ENUM ('HQ', 'OWNER');
CREATE TYPE region_level  AS ENUM ('SIDO', 'SIGUNGU', 'DONG');
CREATE TYPE report_status AS ENUM ('DRAFT', 'ANALYZING', 'COMPLETED', 'FAILED');
CREATE TYPE risk_level    AS ENUM ('NORMAL', 'CAUTION', 'DANGER');
CREATE TYPE input_source  AS ENUM ('MANUAL', 'POS');
CREATE TYPE email_status  AS ENUM ('PENDING', 'SENT', 'FAILED');

-- ---------- 기준정보 ----------
CREATE TABLE region (
    code        VARCHAR(20)  PRIMARY KEY,
    parent_code VARCHAR(20)  REFERENCES region (code),
    level       region_level NOT NULL,
    name        VARCHAR(50)  NOT NULL
);
COMMENT ON TABLE region IS '지역 마스터 (REQ-HQ-18 3단 선택). 코드 체계는 데이터팀 서비스에 정합';
CREATE INDEX idx_region_parent ON region (parent_code);

CREATE TABLE business_category (
    code VARCHAR(20) PRIMARY KEY,
    name VARCHAR(50) NOT NULL
);
COMMENT ON TABLE business_category IS '업종 마스터 (REQ-HQ-18, REQ-SRN-04)';

-- ---------- 조직 · 계정 ----------
CREATE TABLE franchise (
    id         BIGSERIAL    PRIMARY KEY,
    name       VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE user_account (
    id            BIGSERIAL    PRIMARY KEY,
    franchise_id  BIGINT       NOT NULL REFERENCES franchise (id),
    email         VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    user_type     user_type    NOT NULL,
    name          VARCHAR(50)  NOT NULL,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    last_login_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON COLUMN user_account.franchise_id IS '본사·점주 모두 소속 (REQ-AUTH-06, REQ-SRN-09)';
CREATE INDEX idx_user_franchise ON user_account (franchise_id);

CREATE TABLE branch (
    id                     BIGSERIAL    PRIMARY KEY,
    franchise_id           BIGINT       NOT NULL REFERENCES franchise (id),
    owner_user_id          BIGINT       NOT NULL UNIQUE REFERENCES user_account (id),
    name                   VARCHAR(100) NOT NULL,
    address                VARCHAR(255) NOT NULL,
    region_code            VARCHAR(20)  NOT NULL REFERENCES region (code),
    business_category_code VARCHAR(20)  NOT NULL REFERENCES business_category (code),
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON COLUMN branch.owner_user_id IS 'UNIQUE = 1점주 1점포 (REQ-AUTH-07)';
CREATE INDEX idx_branch_franchise ON branch (franchise_id);
CREATE INDEX idx_branch_region    ON branch (region_code);
CREATE INDEX idx_branch_name      ON branch (name);

-- ---------- 운영보고서 (REQ-DATA-08 입력 계층) ----------
CREATE TABLE report_input_field (
    code          VARCHAR(40) PRIMARY KEY,
    name          VARCHAR(50) NOT NULL,
    group_name    VARCHAR(30) NOT NULL,
    is_required   BOOLEAN     NOT NULL,
    display_order SMALLINT    NOT NULL
);
COMMENT ON TABLE report_input_field IS '보고서 입력 항목 정의 35행 (REQ v0.11 입력 항목 표). 미결 #5로 변동 예상';

CREATE TABLE operation_report (
    id                  BIGSERIAL     PRIMARY KEY,
    branch_id           BIGINT        NOT NULL REFERENCES branch (id),
    report_month        DATE          NOT NULL,
    status              report_status NOT NULL DEFAULT 'DRAFT',
    input_source        input_source  NOT NULL DEFAULT 'MANUAL',
    net_sales           NUMERIC(14,0),
    analysis_request_id UUID,
    analysis_error      TEXT,
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT uq_report_branch_month UNIQUE (branch_id, report_month)
);
COMMENT ON COLUMN operation_report.report_month IS '대상 월의 1일 (REQ-OW-11 전월 보고서)';
COMMENT ON COLUMN operation_report.net_sales IS '매출 정렬·랭킹 키 (REQ-HQ-04/05). 산식 미확정';
CREATE INDEX idx_report_branch_month ON operation_report (branch_id, report_month DESC);
CREATE INDEX idx_report_status       ON operation_report (status);
CREATE INDEX idx_report_net_sales    ON operation_report (net_sales DESC);

CREATE TABLE report_input_item (
    report_id  BIGINT        NOT NULL REFERENCES operation_report (id) ON DELETE CASCADE,
    field_code VARCHAR(40)   NOT NULL REFERENCES report_input_field (code),
    amount     NUMERIC(14,0) NOT NULL,
    PRIMARY KEY (report_id, field_code)
);
COMMENT ON TABLE report_input_item IS '입력 원본. 미입력 선택 항목은 행을 만들지 않는다';

-- ---------- 위험도 분석 결과 (REQ-DATA-08 결과 계층) ----------
CREATE TABLE report_analysis (
    report_id       BIGINT      PRIMARY KEY REFERENCES operation_report (id) ON DELETE CASCADE,
    risk_score      SMALLINT    NOT NULL CHECK (risk_score BETWEEN 0 AND 100),
    risk_level      risk_level  NOT NULL,
    factors         JSONB       NOT NULL,
    risk_periods    JSONB       NOT NULL,
    recommendations JSONB       NOT NULL,
    rule_version    VARCHAR(20) NOT NULL,
    calculated_at   TIMESTAMPTZ NOT NULL
);
COMMENT ON COLUMN report_analysis.factors IS '4요소별 값·가중치·기여도 (REQ-RPT-03/05). 표시 전용이라 JSONB';
COMMENT ON COLUMN report_analysis.risk_periods IS '당월·3·6·12개월 (REQ-SRN-02)';
CREATE INDEX idx_analysis_level ON report_analysis (risk_level);

-- ---------- 알림 ----------
CREATE TABLE notification (
    id                BIGSERIAL    PRIMARY KEY,
    recipient_user_id BIGINT       NOT NULL REFERENCES user_account (id),
    report_id         BIGINT       NOT NULL REFERENCES operation_report (id) ON DELETE CASCADE,
    message           TEXT         NOT NULL,
    is_read           BOOLEAN      NOT NULL DEFAULT FALSE,
    read_at           TIMESTAMPTZ,
    email_status      email_status NOT NULL DEFAULT 'PENDING',
    email_sent_at     TIMESTAMPTZ,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON TABLE notification IS '위험 등급 알림 + 발송 이력 (REQ-HQ-07~10, REQ-SRN-08~12)';
CREATE INDEX idx_notification_unread
    ON notification (recipient_user_id, created_at DESC)
    WHERE is_read = FALSE;

-- ---------- 금융상품 ----------
CREATE TABLE financial_product (
    id                BIGSERIAL    PRIMARY KEY,
    target_risk_level risk_level   NOT NULL,
    name              VARCHAR(100) NOT NULL,
    description       TEXT,
    link_url          VARCHAR(500) NOT NULL,
    display_order     SMALLINT     NOT NULL DEFAULT 0,
    is_active         BOOLEAN      NOT NULL DEFAULT TRUE
);
CREATE INDEX idx_product_target
    ON financial_product (target_risk_level, display_order)
    WHERE is_active;

-- ---------- 인증 (D2: JWT Access + Refresh) ----------
CREATE TABLE refresh_token (
    id         BIGSERIAL    PRIMARY KEY,
    user_id    BIGINT       NOT NULL REFERENCES user_account (id) ON DELETE CASCADE,
    token_hash VARCHAR(255) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ  NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON TABLE refresh_token IS 'Refresh Token 해시 저장. 회전·폐기용 (D2). 원문은 저장하지 않는다';
CREATE INDEX idx_refresh_token_user ON refresh_token (user_id) WHERE revoked_at IS NULL;
CREATE INDEX idx_refresh_token_expires ON refresh_token (expires_at);

-- ---------- 감사에서 추가된 인덱스 ----------
-- F2: notification.report_id — 5-1의 3단 조인 및 ON DELETE CASCADE 성능
CREATE INDEX idx_notification_report ON notification (report_id);
-- F1: 위험도 순 정렬이 risk_score 기준일 때 필요
CREATE INDEX idx_analysis_score ON report_analysis (risk_score DESC);"""


def _statements(ddl: str) -> Iterator[str]:
    buf: list[str] = []
    in_string = False
    in_comment = False
    i = 0
    while i < len(ddl):
        ch = ddl[i]
        if in_comment:
            if ch == "\n":
                in_comment = False
                buf.append(ch)
            i += 1
            continue
        if in_string:
            buf.append(ch)
            if ch == "'":
                in_string = ddl[i + 1 : i + 2] == "'"
                if in_string:
                    buf.append("'")
                    i += 1
            i += 1
            continue
        if ch == "'":
            in_string = True
            buf.append(ch)
        elif ch == "-" and ddl[i + 1 : i + 2] == "-":
            in_comment = True
            i += 2
            continue
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                yield stmt
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        yield tail


# 생성문에서 대상 이름만 뽑는다. 문서 DDL 의 형태(`CREATE TABLE region (`,
# `CREATE INDEX idx_region_parent ON ...`)에 맞춘 최소 패턴이며, 여기에 걸리지
# 않는 문장은 건너뛰지 않고 그대로 실행한다.
_CREATE_TYPE = re.compile(r"\ACREATE\s+TYPE\s+([A-Za-z_][\w$]*)", re.IGNORECASE)
_CREATE_TABLE = re.compile(r"\ACREATE\s+TABLE\s+([A-Za-z_][\w$]*)", re.IGNORECASE)
_CREATE_INDEX = re.compile(
    r"\ACREATE\s+(?:UNIQUE\s+)?INDEX\s+([A-Za-z_][\w$]*)", re.IGNORECASE
)


def _already_present(stmt: str) -> bool:
    """이 문장이 만들려는 객체가 이미 있으면 True.

    `COMMENT ON ...` 은 절대 건너뛰지 않는다. 멱등이고, 대상이 없으면 실패해서
    스키마 표류를 드러내 주기 때문이다.
    """
    match = _CREATE_TYPE.match(stmt)
    if match:
        return type_exists(match.group(1))
    match = _CREATE_TABLE.match(stmt)
    if match:
        return relation_exists(match.group(1))
    match = _CREATE_INDEX.match(stmt)
    if match:
        # 인덱스도 pg_class 관계다.
        return relation_exists(match.group(1))
    return False


def upgrade() -> None:
    for stmt in _statements(DDL):
        if _already_present(stmt):
            continue
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS refresh_token CASCADE")
    op.execute("DROP TABLE IF EXISTS financial_product CASCADE")
    op.execute("DROP TABLE IF EXISTS notification CASCADE")
    op.execute("DROP TABLE IF EXISTS report_analysis CASCADE")
    op.execute("DROP TABLE IF EXISTS report_input_item CASCADE")
    op.execute("DROP TABLE IF EXISTS operation_report CASCADE")
    op.execute("DROP TABLE IF EXISTS report_input_field CASCADE")
    op.execute("DROP TABLE IF EXISTS branch CASCADE")
    op.execute("DROP TABLE IF EXISTS user_account CASCADE")
    op.execute("DROP TABLE IF EXISTS franchise CASCADE")
    op.execute("DROP TABLE IF EXISTS business_category CASCADE")
    op.execute("DROP TABLE IF EXISTS region CASCADE")
    op.execute("DROP TYPE IF EXISTS email_status")
    op.execute("DROP TYPE IF EXISTS input_source")
    op.execute("DROP TYPE IF EXISTS risk_level")
    op.execute("DROP TYPE IF EXISTS report_status")
    op.execute("DROP TYPE IF EXISTS region_level")
    op.execute("DROP TYPE IF EXISTS user_type")
