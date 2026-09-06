"""Siren source metadata, review storage, and nullable partial analysis results.

The Siren service reads source facts from the same database but does not write
those facts. The middle-backend owns analysis-result persistence. Partial Siren
responses keep ``risk_score``/``risk_level`` as NULL and expose their status in
the stored projection envelope instead of being converted to FAILED or NORMAL.
"""

from alembic import op

revision = "0005_siren_operational_storage"
down_revision = "0004_siren_integration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE franchise ADD COLUMN IF NOT EXISTS fixture_key VARCHAR(80)"
    )
    op.execute(
        "ALTER TABLE franchise ADD COLUMN IF NOT EXISTS synthetic BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE branch ADD COLUMN IF NOT EXISTS fixture_key VARCHAR(80)"
    )
    op.execute(
        "ALTER TABLE branch ADD COLUMN IF NOT EXISTS synthetic BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE operation_report ADD COLUMN IF NOT EXISTS fixture_key VARCHAR(120)"
    )
    op.execute(
        "ALTER TABLE operation_report ADD COLUMN IF NOT EXISTS source_status VARCHAR(20)"
    )
    op.execute(
        "ALTER TABLE operation_report ADD COLUMN IF NOT EXISTS synthetic BOOLEAN NOT NULL DEFAULT FALSE"
    )
    # Existing deployments may already have these tables without the fixture
    # columns. Keep synthetic imports idempotent by restoring their uniqueness
    # without rewriting or deleting any existing rows.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_franchise_fixture_key ON franchise (fixture_key) WHERE fixture_key IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_branch_fixture_key ON branch (fixture_key) WHERE fixture_key IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_operation_report_fixture_key ON operation_report (fixture_key) WHERE fixture_key IS NOT NULL"
    )
    op.execute("ALTER TABLE report_analysis ALTER COLUMN risk_score DROP NOT NULL")
    op.execute("ALTER TABLE report_analysis ALTER COLUMN risk_level DROP NOT NULL")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS siren_branch_location (
            branch_id        BIGINT PRIMARY KEY REFERENCES branch (id) ON DELETE CASCADE,
            trade_area_code  VARCHAR(40),
            admin_dong_code  VARCHAR(40),
            x_5181           NUMERIC(12, 2),
            y_5181           NUMERIC(12, 2),
            source_match     JSONB NOT NULL DEFAULT '{}'::jsonb,
            synthetic        BOOLEAN NOT NULL DEFAULT FALSE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS franchise_closure_year (
            id                      BIGSERIAL PRIMARY KEY,
            franchise_id            BIGINT NOT NULL REFERENCES franchise (id),
            year                    INTEGER NOT NULL,
            previous_year_end_count INTEGER NOT NULL CHECK (previous_year_end_count >= 0),
            new_openings            INTEGER NOT NULL CHECK (new_openings >= 0),
            closures                INTEGER NOT NULL CHECK (closures >= 0),
            source                  VARCHAR(120) NOT NULL,
            synthetic               BOOLEAN NOT NULL DEFAULT FALSE,
            UNIQUE (franchise_id, year)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS siren_review (
            review_id       VARCHAR(120) PRIMARY KEY,
            branch_id       BIGINT NOT NULL REFERENCES branch (id) ON DELETE CASCADE,
            written_at      DATE NOT NULL,
            rating          SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
            review_text     TEXT NOT NULL,
            sentiment_label VARCHAR(10) NOT NULL CHECK (sentiment_label IN ('긍정', '부정', '중립')),
            source          VARCHAR(40) NOT NULL CHECK (source IN ('synthetic_reviews', 'naver_place', 'kakao_map', 'delivery_app')),
            synthetic       BOOLEAN NOT NULL DEFAULT FALSE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_siren_branch_location_area ON siren_branch_location (trade_area_code)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_siren_closure_franchise_year ON franchise_closure_year (franchise_id, year DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_siren_review_branch_date ON siren_review (branch_id, written_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_operation_report_fixture_key")
    op.execute("DROP INDEX IF EXISTS uq_branch_fixture_key")
    op.execute("DROP INDEX IF EXISTS uq_franchise_fixture_key")
    op.execute("DROP TABLE IF EXISTS siren_review")
    op.execute("DROP TABLE IF EXISTS franchise_closure_year")
    op.execute("DROP TABLE IF EXISTS siren_branch_location")
    op.execute("ALTER TABLE report_analysis ALTER COLUMN risk_level SET NOT NULL")
    op.execute("ALTER TABLE report_analysis ALTER COLUMN risk_score SET NOT NULL")
    op.execute("ALTER TABLE operation_report DROP COLUMN IF EXISTS synthetic")
    op.execute("ALTER TABLE operation_report DROP COLUMN IF EXISTS source_status")
    op.execute("ALTER TABLE operation_report DROP COLUMN IF EXISTS fixture_key")
    op.execute("ALTER TABLE branch DROP COLUMN IF EXISTS synthetic")
    op.execute("ALTER TABLE branch DROP COLUMN IF EXISTS fixture_key")
    op.execute("ALTER TABLE franchise DROP COLUMN IF EXISTS synthetic")
    op.execute("ALTER TABLE franchise DROP COLUMN IF EXISTS fixture_key")
