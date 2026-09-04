-- 건축물대장(GIS건물통합정보 파생) 상업용 건물 — context 스키마 확장
-- 작성: 2026-09-04
-- 선행: db/001_location_schema.sql (schema context, location.area, meta.dataset_file)
-- 적용: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/002_commercial_building.sql
--
-- 이 테이블은 개별 "매물"이 아니다. GIS건물통합정보의 건물 주용도 기준이며
-- 층별 용도·전유부 호실·주차·공실·임대료는 없다. 격자 합성좌표 대체 seed 모집단과
-- 상권별 상가 건물 규모 배경으로만 사용한다.

BEGIN;

CREATE TABLE IF NOT EXISTS context.commercial_building (
    building_pk           text NOT NULL,          -- GIS건물통합정보 건물관리번호(A1)
    snapshot              text NOT NULL,          -- 원천 파일 스냅샷 (예: 20260809)
    pnu                   text,
    sigungu_code          text,
    sigungu_name          text,
    legal_dong_code       text,
    lot_address           text,                   -- 대지위치(지번주소)
    lot_number            text,
    lot_kind              text,                   -- 지번구분 (일반/산)
    use_code              text NOT NULL,          -- 주용도 대분류 코드 (03000 등)
    use_name              text,
    use_group             text NOT NULL,          -- 근린생활1/근린생활2/판매시설/판매영업/근린생활
    floors_above          integer,
    floors_below          integer,
    building_area_m2       numeric,               -- 건축면적
    gross_floor_area_m2    numeric,               -- 연면적
    building_coverage_pct  numeric,               -- 건폐율
    floor_area_ratio_pct   numeric,               -- 용적률
    height_m               numeric,
    structure              text,
    approval_date          date,                  -- 사용승인일
    building_age_years      integer,
    footprint_m2            numeric,
    point                  geometry(Point, 5181),
    lon                    numeric,
    lat                    numeric,
    host_area_id           text,                  -- 포함 상권 area_id (내부/근접), 미결합 시 NULL. location.area 참조(FK 아님 — 스냅샷 테이블)
    area_join_type         text NOT NULL CHECK (area_join_type IN ('내부', '근접', '미결합')),
    admin_dong_code        text,
    admin_dong_name        text,
    source_file_id         bigint REFERENCES meta.dataset_file(file_id),
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

-- 상권 × 스냅샷 요약 (파이프라인 배경 지표: 상권별 상가 건물 규모)
-- context.commercial_building 에서 파생 — 적재 후 REFRESH.
CREATE MATERIALIZED VIEW IF NOT EXISTS context.commercial_building_area_summary AS
SELECT
    b.host_area_id                                             AS area_id,
    a.spatial_unit_name                                        AS area_name,
    b.snapshot,
    count(*)                                                   AS building_count,
    count(*) FILTER (WHERE b.use_group = '근린생활1')          AS nsg1_count,
    count(*) FILTER (WHERE b.use_group = '근린생활2')          AS nsg2_count,
    count(*) FILTER (WHERE b.use_group IN ('판매시설', '판매영업')) AS retail_count,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY b.gross_floor_area_m2)
        FILTER (WHERE b.gross_floor_area_m2 IS NOT NULL)       AS gfa_median_m2,
    percentile_disc(0.5) WITHIN GROUP (ORDER BY b.floors_above)
        FILTER (WHERE b.floors_above IS NOT NULL)              AS floors_above_median,
    count(*) FILTER (WHERE b.floors_below > 0)                 AS has_basement_count,
    percentile_disc(0.5) WITHIN GROUP (ORDER BY b.building_age_years)
        FILTER (WHERE b.building_age_years IS NOT NULL)        AS age_median_years,
    count(*) FILTER (WHERE b.building_age_years <= 10)         AS new_le10y_count,
    count(*) FILTER (WHERE b.building_age_years >= 30)         AS old_ge30y_count
FROM context.commercial_building b
JOIN location.area a ON a.area_id = b.host_area_id
WHERE b.host_area_id IS NOT NULL
GROUP BY b.host_area_id, a.spatial_unit_name, b.snapshot;

CREATE UNIQUE INDEX IF NOT EXISTS commercial_building_area_summary_pk
    ON context.commercial_building_area_summary (area_id, snapshot);

COMMIT;
