-- 서울 창업 입지 추천 PostgreSQL + PostGIS 기본 스키마
-- 작성: GPT(Codex), 2026-09-03
-- 주의: 이 파일은 DB 구조만 생성한다. 원천 CSV 적재는 별도 ETL에서 수행한다.

BEGIN;

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS meta;
CREATE SCHEMA IF NOT EXISTS location;
CREATE SCHEMA IF NOT EXISTS context;
CREATE SCHEMA IF NOT EXISTS evidence;

CREATE TABLE IF NOT EXISTS meta.dataset_file (
    file_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id text NOT NULL,
    relative_path text NOT NULL UNIQUE,
    source_name text NOT NULL,
    source_type text NOT NULL CHECK (source_type IN (
        'official_file', 'official_api_snapshot', 'observed_poi_snapshot',
        'observed_news_metadata', 'project_generated', 'derived'
    )),
    encoding text,
    sha256 char(64),
    file_bytes bigint,
    row_count bigint,
    observed_period_start text,
    observed_period_end text,
    retrieved_at timestamptz,
    manifest_path text,
    source_columns jsonb NOT NULL DEFAULT '[]'::jsonb,
    imported_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS meta.dataset_run (
    run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_type text NOT NULL CHECK (run_type IN ('schema', 'migration', 'refresh', 'rollback')),
    data_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('started', 'completed', 'failed', 'rolled_back')),
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    notes text
);

CREATE TABLE IF NOT EXISTS location.area (
    area_id text PRIMARY KEY,
    spatial_unit_type text NOT NULL CHECK (spatial_unit_type IN (
        'commercial_area', 'hinterland', 'admin_dong', 'sigungu', 'region'
    )),
    spatial_unit_code text NOT NULL,
    spatial_unit_name text NOT NULL,
    sigungu_code text,
    sigungu_name text,
    admin_dong_code text,
    admin_dong_name text,
    centroid geometry(Point, 5181),
    geom geometry(MultiPolygon, 5181),
    area_m2 numeric,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    UNIQUE (spatial_unit_type, spatial_unit_code)
);

CREATE INDEX IF NOT EXISTS area_geom_gix ON location.area USING gist (geom);
CREATE INDEX IF NOT EXISTS area_centroid_gix ON location.area USING gist (centroid);
CREATE INDEX IF NOT EXISTS area_sigungu_idx ON location.area (sigungu_code);
CREATE INDEX IF NOT EXISTS area_dong_idx ON location.area (admin_dong_code);

CREATE TABLE IF NOT EXISTS location.industry (
    industry_code text PRIMARY KEY,
    industry_name text NOT NULL,
    food_terms jsonb NOT NULL DEFAULT '[]'::jsonb,
    search_keywords jsonb NOT NULL DEFAULT '[]'::jsonb,
    license_categories jsonb NOT NULL DEFAULT '[]'::jsonb,
    boundary_note text,
    ontology_version text NOT NULL,
    source_file_id bigint REFERENCES meta.dataset_file(file_id)
);

CREATE TABLE IF NOT EXISTS location.area_crosswalk (
    crosswalk_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    relation_type text NOT NULL,
    source_area_id text NOT NULL,
    target_area_id text NOT NULL,
    overlap_m2 numeric,
    source_ratio numeric,
    target_ratio numeric,
    join_eligible boolean NOT NULL DEFAULT false,
    grain_is_proxy boolean NOT NULL DEFAULT false,
    method text NOT NULL,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    UNIQUE (relation_type, source_area_id, target_area_id)
);

CREATE INDEX IF NOT EXISTS area_crosswalk_source_idx ON location.area_crosswalk (source_area_id);
CREATE INDEX IF NOT EXISTS area_crosswalk_target_idx ON location.area_crosswalk (target_area_id);

CREATE TABLE IF NOT EXISTS location.store_quarter (
    period text NOT NULL CHECK (period ~ '^[0-9]{5}$'),
    spatial_unit_type text NOT NULL CHECK (spatial_unit_type IN ('commercial_area', 'hinterland', 'admin_dong')),
    spatial_unit_code text NOT NULL,
    spatial_unit_name text,
    industry_code text NOT NULL REFERENCES location.industry(industry_code),
    industry_name text,
    total_store_count integer,
    general_store_count integer,
    franchise_store_count integer,
    open_rate numeric,
    open_store_count integer,
    close_rate numeric,
    close_store_count integer,
    source_file_id bigint NOT NULL REFERENCES meta.dataset_file(file_id),
    source_row_number bigint,
    source_schema_version text,
    is_partial_latest boolean NOT NULL DEFAULT false,
    missing_reason text,
    PRIMARY KEY (period, spatial_unit_type, spatial_unit_code, industry_code)
);

CREATE INDEX IF NOT EXISTS store_quarter_area_idx
    ON location.store_quarter (spatial_unit_type, spatial_unit_code, period);
CREATE INDEX IF NOT EXISTS store_quarter_industry_idx
    ON location.store_quarter (industry_code, period);

-- 전 업종 통합 지역 점포 배경 (entry_health_v1 / build_environment 입력).
-- store_quarter 는 프로젝트 대상 10개 음식 업종만 담으므로, 지역 전체 상권
-- 교체율을 계산하려면 업종 필터 없는 합계가 별도로 필요하다. 원천 CSV의
-- (분기 × 공간코드 × 업종) 행을 업종 축으로 합산해 적재한다.
CREATE TABLE IF NOT EXISTS location.area_store_totals (
    period text NOT NULL CHECK (period ~ '^[0-9]{5}$'),
    spatial_unit_type text NOT NULL CHECK (spatial_unit_type IN ('commercial_area', 'hinterland', 'admin_dong')),
    spatial_unit_code text NOT NULL,
    total_store_count numeric,
    open_store_count numeric,
    close_store_count numeric,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    PRIMARY KEY (period, spatial_unit_type, spatial_unit_code)
);

CREATE INDEX IF NOT EXISTS area_store_totals_area_idx
    ON location.area_store_totals (spatial_unit_type, spatial_unit_code, period);

CREATE TABLE IF NOT EXISTS location.sales_quarter (
    period text NOT NULL CHECK (period ~ '^[0-9]{5}$'),
    spatial_unit_type text NOT NULL CHECK (spatial_unit_type IN ('commercial_area', 'hinterland', 'admin_dong')),
    spatial_unit_code text NOT NULL,
    spatial_unit_name text,
    industry_code text NOT NULL REFERENCES location.industry(industry_code),
    industry_name text,
    sales_amount numeric,
    sales_count numeric,
    weekday_sales_amount numeric,
    weekend_sales_amount numeric,
    detail_metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_file_id bigint NOT NULL REFERENCES meta.dataset_file(file_id),
    source_row_number bigint,
    source_schema_version text,
    is_partial_latest boolean NOT NULL DEFAULT false,
    missing_reason text,
    PRIMARY KEY (period, spatial_unit_type, spatial_unit_code, industry_code)
);

CREATE INDEX IF NOT EXISTS sales_quarter_area_idx
    ON location.sales_quarter (spatial_unit_type, spatial_unit_code, period);
CREATE INDEX IF NOT EXISTS sales_quarter_industry_idx
    ON location.sales_quarter (industry_code, period);

CREATE TABLE IF NOT EXISTS location.flow_quarter (
    period text NOT NULL CHECK (period ~ '^[0-9]{5}$'),
    spatial_unit_type text NOT NULL CHECK (spatial_unit_type IN ('commercial_area', 'hinterland', 'admin_dong')),
    spatial_unit_code text NOT NULL,
    spatial_unit_name text,
    flow_total numeric,
    detail_metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_file_id bigint NOT NULL REFERENCES meta.dataset_file(file_id),
    source_row_number bigint,
    is_partial_latest boolean NOT NULL DEFAULT false,
    missing_reason text,
    PRIMARY KEY (period, spatial_unit_type, spatial_unit_code)
);

CREATE INDEX IF NOT EXISTS flow_quarter_area_idx
    ON location.flow_quarter (spatial_unit_type, spatial_unit_code, period);

CREATE TABLE IF NOT EXISTS location.permitted_establishment (
    permit_id text PRIMARY KEY,
    industry_code text REFERENCES location.industry(industry_code),
    business_type text,
    licensed_at date,
    closed_at date,
    status text,
    sigungu_name text,
    spatial_unit_code text,
    spatial_unit_name text,
    point geometry(Point, 5181),
    x_5181 numeric,
    y_5181 numeric,
    source_type text NOT NULL,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    source_attributes jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS permitted_establishment_gix
    ON location.permitted_establishment USING gist (point);
CREATE INDEX IF NOT EXISTS permitted_establishment_industry_idx
    ON location.permitted_establishment (industry_code, status);
CREATE INDEX IF NOT EXISTS permitted_establishment_area_idx
    ON location.permitted_establishment (spatial_unit_code);

CREATE TABLE IF NOT EXISTS location.permit_quarter (
    period text NOT NULL CHECK (period ~ '^[0-9]{5}$'),
    spatial_unit_type text NOT NULL CHECK (spatial_unit_type = 'commercial_area'),
    spatial_unit_code text NOT NULL,
    industry_code text NOT NULL REFERENCES location.industry(industry_code),
    operating_count integer,
    new_count integer,
    closed_count integer,
    open_rate numeric,
    close_rate numeric,
    quarter_status text,
    source_type text NOT NULL,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    source_attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (period, spatial_unit_type, spatial_unit_code, industry_code)
);

CREATE INDEX IF NOT EXISTS permit_quarter_industry_idx
    ON location.permit_quarter (industry_code, period);
CREATE INDEX IF NOT EXISTS permit_quarter_area_idx
    ON location.permit_quarter (spatial_unit_code, period);

CREATE TABLE IF NOT EXISTS context.metric_snapshot (
    metric_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    metric_name text NOT NULL,
    period text NOT NULL,
    period_type text NOT NULL CHECK (period_type IN ('quarter', 'month', 'half_year', 'snapshot')),
    spatial_unit_type text NOT NULL CHECK (spatial_unit_type IN ('commercial_area', 'hinterland', 'admin_dong', 'sigungu', 'region', 'point')),
    spatial_unit_code text,
    spatial_unit_name text,
    industry_code text REFERENCES location.industry(industry_code),
    value_numeric numeric,
    value_text text,
    unit text,
    grain_is_proxy boolean NOT NULL DEFAULT false,
    proxy_note text,
    source_type text NOT NULL,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    source_column text,
    observed_at timestamptz,
    missing_reason text,
    UNIQUE (metric_name, period, period_type, spatial_unit_type, spatial_unit_code, industry_code)
);

CREATE INDEX IF NOT EXISTS metric_snapshot_lookup_idx
    ON context.metric_snapshot (spatial_unit_type, spatial_unit_code, period);
CREATE INDEX IF NOT EXISTS metric_snapshot_metric_idx
    ON context.metric_snapshot (metric_name, period);

CREATE TABLE IF NOT EXISTS location.anchor_point (
    anchor_id text PRIMARY KEY,
    anchor_type text NOT NULL CHECK (anchor_type IN ('apartment', 'station', 'bus_stop', 'kakao_poi', 'generated_grid')),
    place_name text NOT NULL,
    point geometry(Point, 5181),
    x_wgs84 numeric,
    y_wgs84 numeric,
    sigungu_code text,
    admin_dong_code text,
    host_area_id text,
    source_type text NOT NULL,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    observed_at timestamptz,
    coordinate_confidence text NOT NULL CHECK (coordinate_confidence IN ('verified', 'conditional', 'unmatched')),
    synthetic_anchor boolean NOT NULL DEFAULT false,
    source_attributes jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS anchor_point_gix ON location.anchor_point USING gist (point);
CREATE INDEX IF NOT EXISTS anchor_point_area_idx ON location.anchor_point (host_area_id);

-- anchor 원천 CSV 를 행 단위로 verbatim 보존 (아파트·역·버스·카카오 POI).
-- anchor_point 는 spatial join 용으로 dedup·정규화되지만, 서빙의 seed·반경 로직은
-- "CSV 행을 그대로 순회" 하므로 여기서 원본 행(빈 값 포함)을 읽어 파일 모드와
-- 동일한 결과를 낸다. row_seq 는 파일 내 원래 순서.
CREATE TABLE IF NOT EXISTS context.anchor_snapshot (
    source_file text NOT NULL,
    row_seq integer NOT NULL,
    anchor_type text NOT NULL CHECK (anchor_type IN ('apartment', 'station', 'bus_stop', 'kakao_poi')),
    attributes jsonb NOT NULL,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    PRIMARY KEY (source_file, row_seq)
);

CREATE INDEX IF NOT EXISTS anchor_snapshot_type_idx ON context.anchor_snapshot (anchor_type, source_file, row_seq);

CREATE TABLE IF NOT EXISTS context.poi_snapshot (
    source text NOT NULL,
    poi_id text NOT NULL,
    retrieved_at timestamptz NOT NULL,
    place_name text NOT NULL,
    category_group_code text,
    category_group_name text,
    point geometry(Point, 5181),
    x_wgs84 numeric,
    y_wgs84 numeric,
    road_address text,
    address text,
    place_url text,
    search_mode text,
    search_query text,
    search_center_x numeric,
    search_center_y numeric,
    search_radius_m numeric,
    source_attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    PRIMARY KEY (source, poi_id, retrieved_at)
);

CREATE INDEX IF NOT EXISTS poi_snapshot_gix ON context.poi_snapshot USING gist (point);
CREATE INDEX IF NOT EXISTS poi_snapshot_category_idx ON context.poi_snapshot (category_group_code, retrieved_at);

CREATE TABLE IF NOT EXISTS context.plan_snapshot (
    plan_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plan_type text NOT NULL,
    spatial_unit_type text NOT NULL,
    spatial_unit_code text,
    project_name text,
    project_category text,
    progress_stage text,
    overlap_m2 numeric,
    overlap_ratio numeric,
    plan_geom geometry(MultiPolygon, 5181),
    observed_at date,
    source_type text NOT NULL,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    source_attributes jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS plan_snapshot_gix ON context.plan_snapshot USING gist (plan_geom);
CREATE INDEX IF NOT EXISTS plan_snapshot_area_idx ON context.plan_snapshot (spatial_unit_type, spatial_unit_code);

CREATE TABLE IF NOT EXISTS context.news_snapshot (
    source text NOT NULL,
    news_id text NOT NULL,
    published_date date,
    publisher text,
    title text NOT NULL,
    url text,
    query_label text,
    search_period jsonb NOT NULL DEFAULT '{}'::jsonb,
    sigungu_tags text[] NOT NULL DEFAULT '{}',
    dong_tags text[] NOT NULL DEFAULT '{}',
    topic_tags text[] NOT NULL DEFAULT '{}',
    topic_match boolean,
    retrieved_at timestamptz,
    source_attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    PRIMARY KEY (source, news_id)
);

CREATE INDEX IF NOT EXISTS news_snapshot_date_idx ON context.news_snapshot (published_date);
CREATE INDEX IF NOT EXISTS news_snapshot_sigungu_gin ON context.news_snapshot USING gin (sigungu_tags);
CREATE INDEX IF NOT EXISTS news_snapshot_dong_gin ON context.news_snapshot USING gin (dong_tags);

-- 뉴스 snapshot 의 수집 메타데이터 (원본 *_manifest.json). 서빙이 FC-51 근거의
-- 검색어·기간·행수·중복제거·주제적합 카운트를 이 blob 에서 읽는다.
CREATE TABLE IF NOT EXISTS context.news_manifest (
    source text PRIMARY KEY,
    manifest jsonb NOT NULL,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    ingested_at timestamptz NOT NULL DEFAULT now()
);

-- R-ONE 임대동향 — grain(상권/권역/서울전체) 을 보존한다. context.metric_snapshot 의
-- rone_* 는 grain 을 region 으로 뭉갰으므로 서빙 FC-20 은 이 테이블을 쓴다.
CREATE TABLE IF NOT EXISTS context.rent_index (
    period text NOT NULL,
    store_type text NOT NULL,          -- 소규모상가 / 중대형상가 / 집합상가 / 통합상가
    indicator text NOT NULL,           -- 임대가격지수 / 임대료(천원㎡) 등
    grain text NOT NULL CHECK (grain IN ('상권', '권역', '서울전체')),
    rone_area text NOT NULL DEFAULT '',  -- grain=상권 일 때 R-ONE 상권명, 아니면 ''
    zone text,                          -- R-ONE 권역
    value_numeric numeric,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    PRIMARY KEY (period, store_type, indicator, grain, rone_area)
);

CREATE INDEX IF NOT EXISTS rent_index_lookup_idx
    ON context.rent_index (store_type, indicator, grain, rone_area, period);

-- 네이버 검색트렌드 계절성 파생 (analyze_naver_seasonality.py 산출). 서빙 FC-42 가
-- 전년 동월 대비 배수·계절 피크월·미검증 급등 플래그를 여기서 읽는다.
CREATE TABLE IF NOT EXISTS context.naver_seasonality (
    grain text NOT NULL,          -- 업종 / 자치구 / 행정동
    key text NOT NULL,            -- 네이버 업종명 또는 지역명
    attributes jsonb NOT NULL,    -- 원본 행 전체
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    PRIMARY KEY (grain, key)
);

CREATE TABLE IF NOT EXISTS evidence.recommendation_run (
    run_id uuid PRIMARY KEY,
    data_version text NOT NULL,
    request jsonb NOT NULL,
    coverage_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    run_manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
    generated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS evidence.candidate (
    run_id uuid NOT NULL REFERENCES evidence.recommendation_run(run_id),
    candidate_id text NOT NULL,
    candidate_type text,
    fit_tier text,
    data_confidence text,
    candidate_json jsonb NOT NULL,
    PRIMARY KEY (run_id, candidate_id)
);

COMMIT;
