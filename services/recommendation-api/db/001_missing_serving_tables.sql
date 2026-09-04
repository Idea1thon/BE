-- 추천 서빙 DB의 누락 보완 테이블
--
-- 적용:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/001_missing_serving_tables.sql
--
-- area_store_totals 는 대상 10개 업종용 store_quarter와 분리된 전 업종
-- 지역 배경값이다. commercial_building 은 GIS건물통합정보 파생 seed이며,
-- 나머지 context 테이블은 DB 모드의 경량 anchor·뉴스·임대료 보조 맥락이다.

BEGIN;

CREATE SCHEMA IF NOT EXISTS location;
CREATE SCHEMA IF NOT EXISTS context;
CREATE SCHEMA IF NOT EXISTS meta;

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

CREATE TABLE IF NOT EXISTS context.anchor_snapshot (
    source_file text NOT NULL,
    row_seq integer NOT NULL,
    anchor_type text NOT NULL CHECK (anchor_type IN ('apartment', 'station', 'bus_stop', 'kakao_poi')),
    attributes jsonb NOT NULL,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    PRIMARY KEY (source_file, row_seq)
);

CREATE INDEX IF NOT EXISTS anchor_snapshot_type_idx ON context.anchor_snapshot (anchor_type, source_file, row_seq);

CREATE TABLE IF NOT EXISTS context.news_manifest (
    source text PRIMARY KEY,
    manifest jsonb NOT NULL,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    ingested_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS context.rent_index (
    period text NOT NULL,
    store_type text NOT NULL,
    indicator text NOT NULL,
    grain text NOT NULL CHECK (grain IN ('상권', '권역', '서울전체')),
    rone_area text NOT NULL DEFAULT '',
    zone text,
    value_numeric numeric,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    PRIMARY KEY (period, store_type, indicator, grain, rone_area)
);

CREATE INDEX IF NOT EXISTS rent_index_lookup_idx
    ON context.rent_index (store_type, indicator, grain, rone_area, period);

CREATE TABLE IF NOT EXISTS context.naver_seasonality (
    grain text NOT NULL,
    key text NOT NULL,
    attributes jsonb NOT NULL,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    PRIMARY KEY (grain, key)
);

CREATE TABLE IF NOT EXISTS context.commercial_building (
    building_pk text NOT NULL,
    snapshot text NOT NULL,
    pnu text,
    sigungu_code text,
    sigungu_name text,
    legal_dong_code text,
    lot_address text,
    lot_number text,
    lot_kind text,
    use_code text NOT NULL,
    use_name text,
    use_group text NOT NULL,
    floors_above integer,
    floors_below integer,
    building_area_m2 numeric,
    gross_floor_area_m2 numeric,
    building_coverage_pct numeric,
    floor_area_ratio_pct numeric,
    height_m numeric,
    structure text,
    approval_date date,
    building_age_years integer,
    footprint_m2 numeric,
    point geometry(Point, 5181),
    lon numeric,
    lat numeric,
    host_area_id text,
    area_join_type text NOT NULL CHECK (area_join_type IN ('내부', '근접', '미결합')),
    admin_dong_code text,
    admin_dong_name text,
    source_file_id bigint REFERENCES meta.dataset_file(file_id),
    PRIMARY KEY (building_pk, snapshot)
);

CREATE INDEX IF NOT EXISTS commercial_building_gix
    ON context.commercial_building USING gist (point);
CREATE INDEX IF NOT EXISTS commercial_building_area_idx
    ON context.commercial_building (host_area_id);
CREATE INDEX IF NOT EXISTS commercial_building_use_idx
    ON context.commercial_building (use_group);
CREATE INDEX IF NOT EXISTS commercial_building_sigungu_idx
    ON context.commercial_building (sigungu_code);

CREATE MATERIALIZED VIEW IF NOT EXISTS context.commercial_building_area_summary AS
SELECT
    b.host_area_id AS area_id,
    a.spatial_unit_name AS area_name,
    b.snapshot,
    count(*) AS building_count,
    count(*) FILTER (WHERE b.use_group = '근린생활1') AS nsg1_count,
    count(*) FILTER (WHERE b.use_group = '근린생활2') AS nsg2_count,
    count(*) FILTER (WHERE b.use_group IN ('판매시설', '판매영업')) AS retail_count,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY b.gross_floor_area_m2)
        FILTER (WHERE b.gross_floor_area_m2 IS NOT NULL) AS gfa_median_m2,
    percentile_disc(0.5) WITHIN GROUP (ORDER BY b.floors_above)
        FILTER (WHERE b.floors_above IS NOT NULL) AS floors_above_median,
    count(*) FILTER (WHERE b.floors_below > 0) AS has_basement_count,
    percentile_disc(0.5) WITHIN GROUP (ORDER BY b.building_age_years)
        FILTER (WHERE b.building_age_years IS NOT NULL) AS age_median_years,
    count(*) FILTER (WHERE b.building_age_years <= 10) AS new_le10y_count,
    count(*) FILTER (WHERE b.building_age_years >= 30) AS old_ge30y_count
FROM context.commercial_building b
JOIN location.area a ON a.area_id = b.host_area_id
WHERE b.host_area_id IS NOT NULL
GROUP BY b.host_area_id, a.spatial_unit_name, b.snapshot;

CREATE UNIQUE INDEX IF NOT EXISTS commercial_building_area_summary_pk
    ON context.commercial_building_area_summary (area_id, snapshot);

COMMIT;
