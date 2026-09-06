-- Siren synthetic source fixture tables for the shared IDEATON database.
--
-- Scope:
--   * creates only the FMP/source tables required by services/siren
--   * preserves fixture keys and synthetic provenance
--   * never drops or truncates existing data
--
-- The accompanying import command must be run only after the target schema has
-- been inspected. This file is intentionally idempotent for an empty target
-- schema and fails on incompatible pre-existing tables during inserts.

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typnamespace = 'public'::regnamespace AND typname = 'user_type'
    ) THEN
        CREATE TYPE public.user_type AS ENUM ('HQ', 'OWNER');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typnamespace = 'public'::regnamespace AND typname = 'region_level'
    ) THEN
        CREATE TYPE public.region_level AS ENUM ('SIDO', 'SIGUNGU', 'DONG');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typnamespace = 'public'::regnamespace AND typname = 'report_status'
    ) THEN
        CREATE TYPE public.report_status AS ENUM ('DRAFT', 'ANALYZING', 'COMPLETED', 'FAILED');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typnamespace = 'public'::regnamespace AND typname = 'input_source'
    ) THEN
        CREATE TYPE public.input_source AS ENUM ('MANUAL', 'POS');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typnamespace = 'public'::regnamespace AND typname = 'risk_level'
    ) THEN
        CREATE TYPE public.risk_level AS ENUM ('NORMAL', 'CAUTION', 'DANGER');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typnamespace = 'public'::regnamespace AND typname = 'email_status'
    ) THEN
        CREATE TYPE public.email_status AS ENUM ('PENDING', 'SENT', 'FAILED');
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS public.region (
    code        VARCHAR(20) PRIMARY KEY,
    parent_code VARCHAR(20) REFERENCES public.region (code),
    level       public.region_level NOT NULL,
    name        VARCHAR(50) NOT NULL
);

CREATE TABLE IF NOT EXISTS public.business_category (
    code VARCHAR(20) PRIMARY KEY,
    name VARCHAR(50) NOT NULL
);

CREATE TABLE IF NOT EXISTS public.franchise (
    id          BIGINT PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    fixture_key VARCHAR(80) UNIQUE,
    synthetic   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.user_account (
    id            BIGINT PRIMARY KEY,
    franchise_id  BIGINT NOT NULL REFERENCES public.franchise (id),
    email         VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    user_type     public.user_type NOT NULL,
    name          VARCHAR(50) NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    last_login_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.branch (
    id                     BIGINT PRIMARY KEY,
    franchise_id           BIGINT NOT NULL REFERENCES public.franchise (id),
    owner_user_id          BIGINT NOT NULL UNIQUE REFERENCES public.user_account (id),
    name                   VARCHAR(100) NOT NULL,
    address                VARCHAR(255) NOT NULL,
    region_code            VARCHAR(20) NOT NULL REFERENCES public.region (code),
    business_category_code VARCHAR(20) NOT NULL REFERENCES public.business_category (code),
    fixture_key            VARCHAR(80) UNIQUE,
    synthetic              BOOLEAN NOT NULL DEFAULT FALSE,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_siren_branch_franchise
    ON public.branch (franchise_id);
CREATE INDEX IF NOT EXISTS idx_siren_branch_region
    ON public.branch (region_code);

CREATE TABLE IF NOT EXISTS public.siren_branch_location (
    branch_id        BIGINT PRIMARY KEY REFERENCES public.branch (id) ON DELETE CASCADE,
    trade_area_code  VARCHAR(40),
    admin_dong_code  VARCHAR(40),
    x_5181           NUMERIC(12, 2),
    y_5181           NUMERIC(12, 2),
    source_match     JSONB NOT NULL DEFAULT '{}'::jsonb,
    synthetic        BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS public.report_input_field (
    code          VARCHAR(40) PRIMARY KEY,
    name          VARCHAR(50) NOT NULL,
    group_name    VARCHAR(30) NOT NULL,
    is_required   BOOLEAN NOT NULL DEFAULT FALSE,
    display_order SMALLINT NOT NULL
);

CREATE TABLE IF NOT EXISTS public.operation_report (
    id             BIGINT PRIMARY KEY,
    branch_id      BIGINT NOT NULL REFERENCES public.branch (id),
    report_month   DATE NOT NULL,
    status         public.report_status NOT NULL DEFAULT 'DRAFT',
    input_source   public.input_source NOT NULL DEFAULT 'MANUAL',
    net_sales      NUMERIC(14, 0),
    fixture_key    VARCHAR(120) UNIQUE,
    source_status  VARCHAR(20),
    synthetic      BOOLEAN NOT NULL DEFAULT FALSE,
    analysis_request_id UUID,
    analysis_error TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_siren_report_branch_month UNIQUE (branch_id, report_month)
);

CREATE INDEX IF NOT EXISTS idx_siren_report_branch_month
    ON public.operation_report (branch_id, report_month DESC);
CREATE INDEX IF NOT EXISTS idx_siren_report_status
    ON public.operation_report (status);

ALTER TABLE public.franchise
    ADD COLUMN IF NOT EXISTS fixture_key VARCHAR(80),
    ADD COLUMN IF NOT EXISTS synthetic BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE public.branch
    ADD COLUMN IF NOT EXISTS fixture_key VARCHAR(80),
    ADD COLUMN IF NOT EXISTS synthetic BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE public.operation_report
    ADD COLUMN IF NOT EXISTS fixture_key VARCHAR(120),
    ADD COLUMN IF NOT EXISTS source_status VARCHAR(20),
    ADD COLUMN IF NOT EXISTS synthetic BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS public.report_input_item (
    report_id  BIGINT NOT NULL REFERENCES public.operation_report (id) ON DELETE CASCADE,
    field_code VARCHAR(40) NOT NULL REFERENCES public.report_input_field (code),
    amount     NUMERIC(14, 0) NOT NULL,
    PRIMARY KEY (report_id, field_code)
);

CREATE TABLE IF NOT EXISTS public.franchise_closure_year (
    id                       BIGSERIAL PRIMARY KEY,
    franchise_id             BIGINT NOT NULL REFERENCES public.franchise (id),
    year                     INTEGER NOT NULL,
    previous_year_end_count  INTEGER NOT NULL CHECK (previous_year_end_count >= 0),
    new_openings             INTEGER NOT NULL CHECK (new_openings >= 0),
    closures                 INTEGER NOT NULL CHECK (closures >= 0),
    source                   VARCHAR(120) NOT NULL,
    synthetic                BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (franchise_id, year)
);

CREATE INDEX IF NOT EXISTS idx_siren_closure_franchise_year
    ON public.franchise_closure_year (franchise_id, year DESC);

CREATE TABLE IF NOT EXISTS public.report_analysis (
    report_id       BIGINT PRIMARY KEY REFERENCES public.operation_report (id) ON DELETE CASCADE,
    risk_score      SMALLINT CHECK (risk_score BETWEEN 0 AND 100),
    risk_level      public.risk_level,
    factors         JSONB NOT NULL,
    risk_periods    JSONB NOT NULL,
    recommendations JSONB NOT NULL,
    rule_version    VARCHAR(20) NOT NULL,
    calculated_at   TIMESTAMPTZ NOT NULL
);

ALTER TABLE public.report_analysis
    ALTER COLUMN risk_score DROP NOT NULL,
    ALTER COLUMN risk_level DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_siren_analysis_level
    ON public.report_analysis (risk_level);

CREATE TABLE IF NOT EXISTS public.notification (
    id                BIGSERIAL PRIMARY KEY,
    recipient_user_id BIGINT NOT NULL REFERENCES public.user_account (id),
    report_id         BIGINT NOT NULL REFERENCES public.operation_report (id) ON DELETE CASCADE,
    message           TEXT NOT NULL,
    is_read           BOOLEAN NOT NULL DEFAULT FALSE,
    read_at           TIMESTAMPTZ,
    email_status      public.email_status NOT NULL DEFAULT 'PENDING',
    email_sent_at     TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.financial_product (
    id                BIGSERIAL PRIMARY KEY,
    target_risk_level public.risk_level NOT NULL,
    name              VARCHAR(100) NOT NULL,
    description       TEXT,
    link_url          VARCHAR(500) NOT NULL,
    display_order     SMALLINT NOT NULL DEFAULT 0,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS public.refresh_token (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES public.user_account (id) ON DELETE CASCADE,
    token_hash VARCHAR(255) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.siren_review (
    review_id       VARCHAR(120) PRIMARY KEY,
    branch_id       BIGINT NOT NULL REFERENCES public.branch (id) ON DELETE CASCADE,
    written_at      DATE NOT NULL,
    rating          SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    review_text     TEXT NOT NULL,
    sentiment_label VARCHAR(10) NOT NULL CHECK (sentiment_label IN ('긍정', '부정', '중립')),
    source          VARCHAR(40) NOT NULL CHECK (source IN ('synthetic_reviews', 'naver_place', 'kakao_map', 'delivery_app')),
    synthetic       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_siren_review_branch_date
    ON public.siren_review (branch_id, written_at DESC);

COMMIT;
