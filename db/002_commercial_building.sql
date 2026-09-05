-- 상업용 건물 스키마 v2 — Tier1(GIS건물통합정보) + Tier2(건축HUB 건축물대장 API) 결합
-- 작성: 2026-09-05 (v1 전면 재작성 — v1은 Tier1만 모델링, 표제부·건물링크·층별용도 없었음)
-- 선행: db/001_location_schema.sql (schema context, location.area, meta.dataset_file)
-- 적용: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/002_commercial_building.sql
--
-- 이 테이블들은 개별 "매물"이 아니다. 임대료·보증금·권리금·공실·매물 여부는 없다.
-- Tier1은 GIS건물통합정보 파생 건물 모집단(좌표의 유일한 출처). Tier2는 건축HUB
-- 건축물대장 API(BldRgstHubService)로 확보한 도로명주소·주차·승강기·층별용도.
-- 두 소스는 지번(PNU) 단위로 연결하며, 한 지번에 건축물대장이 여러 개인 경우
-- (아파트단지+관리동/상가동 등) 상업 주용도를 우선 채택해 연결했다 — 그래도 남는
-- 미해결 건은 commercial_building_link.multi_candidate_unresolved=true로 표시.

BEGIN;

-- 이번 재작성 대상 삭제 (v1 잔존 시)
DROP MATERIALIZED VIEW IF EXISTS context.commercial_building_area_summary CASCADE;
DROP TABLE IF EXISTS context.building_floor_use CASCADE;
DROP TABLE IF EXISTS context.commercial_building_link CASCADE;
DROP TABLE IF EXISTS context.building_register CASCADE;
DROP TABLE IF EXISTS context.commercial_building CASCADE;

-- ── 1) Tier1: GIS건물통합정보 파생 상업용 건물 모집단 (좌표의 유일한 출처) ─────
CREATE TABLE context.commercial_building (
    building_pk            text NOT NULL,          -- GIS건물통합정보 건물관리번호(A1)
    snapshot               text NOT NULL,          -- 원천 파일 스냅샷 (예: 20260809)
    pnu                    text,
    sigungu_code           text,
    sigungu_name           text,
    legal_dong_code        text,
    lot_address            text,                   -- 대지위치(지번주소)
    lot_number             text,
    lot_kind               text,                   -- 지번구분 (일반/산)
    use_code               text NOT NULL,          -- 주용도 대분류 코드 (03000 등)
    use_name               text,
    use_group              text NOT NULL,          -- 근린생활1/근린생활2/판매시설/판매영업/근린생활
    floors_above           integer,
    floors_below           integer,
    building_area_m2       numeric,                -- 건축면적
    gross_floor_area_m2    numeric,                -- 연면적
    building_coverage_pct  numeric,                -- 건폐율
    floor_area_ratio_pct   numeric,                -- 용적률
    height_m               numeric,
    structure              text,
    approval_date          date,                   -- 사용승인일
    building_age_years     integer,
    footprint_m2           numeric,
    point                  geometry(Point, 5181),
    lon                    numeric,
    lat                    numeric,
    host_area_id           text,                   -- 포함 상권 area_id. location.area 참조(FK 아님 — 스냅샷 테이블)
    area_join_type         text NOT NULL CHECK (area_join_type IN ('내부', '근접', '미결합')),
    admin_dong_code        text,
    admin_dong_name        text,
    source_file_id         bigint REFERENCES meta.dataset_file(file_id),
    PRIMARY KEY (building_pk, snapshot)
);

CREATE INDEX commercial_building_gix ON context.commercial_building USING gist (point);
CREATE INDEX commercial_building_pnu_idx ON context.commercial_building (pnu);
CREATE INDEX commercial_building_area_idx ON context.commercial_building (host_area_id);
CREATE INDEX commercial_building_use_idx ON context.commercial_building (use_group);
CREATE INDEX commercial_building_sigungu_idx ON context.commercial_building (sigungu_code);

-- ── 2) Tier2 표제부: 건축HUB 건축물대장 API (getBrTitleInfo). mgm_bldrgst_pk 단위 ──
-- 서울 전체 25개 자치구 수집(2026-09-05). use_group=''(빈문자)는 주용도가 비상업이라는 뜻
-- (표제부 기준일 뿐 — 주상복합 저층상가는 building_floor_use로 확인).
CREATE TABLE context.building_register (
    mgm_bldrgst_pk          text PRIMARY KEY,
    pnu                     text,
    sigungu_name            text,
    lot_address             text,
    road_address            text,                  -- 하네스 매물 주소 자리를 채우는 실제 도로명주소
    building_name           text,
    dong_name               text,
    register_kind           text,                  -- 일반/집합
    use_code                text,
    use_name                text,
    use_detail              text,                  -- etcPurps (예: "제1종근린생활시설(소매점)")
    use_group               text,                  -- ''(비상업) 포함
    structure               text,
    roof                    text,
    site_area_m2            numeric,
    building_area_m2        numeric,
    gross_floor_area_m2     numeric,
    coverage_pct            numeric,
    floor_area_ratio_pct    numeric,
    height_m                numeric,
    floors_above            integer,
    floors_below            integer,
    elevators_passenger     integer,
    elevators_emergency     integer,
    unit_count              integer,               -- 호수(hoCnt)
    household_count         integer,               -- 세대수
    family_count            integer,               -- 가구수
    parking_indoor_mech     integer,
    parking_outdoor_mech    integer,
    parking_indoor_self     integer,
    parking_outdoor_self    integer,
    permit_date             date,
    construction_start_date date,
    approval_date           date,
    register_snapshot_date  date,                  -- crtnDay
    source_file_id          bigint REFERENCES meta.dataset_file(file_id)
);

CREATE INDEX building_register_pnu_idx ON context.building_register (pnu);
CREATE INDEX building_register_use_idx ON context.building_register (use_group);
CREATE INDEX building_register_sigungu_idx ON context.building_register (sigungu_name);

-- ── 3) Tier1 ↔ Tier2 연결 (지번 단위, 다중후보 상업우선 정제 완료본) ────────────
CREATE TABLE context.commercial_building_link (
    pnu                         text NOT NULL,
    mgm_bldrgst_pk              text NOT NULL REFERENCES context.building_register(mgm_bldrgst_pk),
    lot_address                 text,
    road_address                text,
    use_group_tier1             text,
    use_name_register           text,
    match_kind                  text NOT NULL CHECK (match_kind IN ('지번', '본번')),
    candidate_count             integer,            -- 정제 전 이 지번의 원 후보 mgm_bldrgst_pk 수
    multi_candidate_unresolved  boolean NOT NULL DEFAULT false,  -- true면 상업 후보가 없어 정제 못함
    source_file_id              bigint REFERENCES meta.dataset_file(file_id),
    PRIMARY KEY (pnu, mgm_bldrgst_pk)
);

CREATE INDEX commercial_building_link_pk_idx ON context.commercial_building_link (mgm_bldrgst_pk);
CREATE INDEX commercial_building_link_unresolved_idx
    ON context.commercial_building_link (multi_candidate_unresolved) WHERE multi_candidate_unresolved;

-- ── 4) Tier2 층별용도 (getBrFlrOulnInfo) — 2026-09-05 기준 송파구만 수집, 나머지는 ──
-- 후속 확대 대상(서울 전체는 무료 API 일일 한도로 ~8-9일 소요, 보류 중).
CREATE TABLE context.building_floor_use (
    floor_use_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mgm_bldrgst_pk     text NOT NULL REFERENCES context.building_register(mgm_bldrgst_pk),
    pnu                text,
    lot_address        text,
    road_address       text,
    floor_division     text,                        -- 지상/지하
    floor_no           integer,
    floor_no_label     text,
    floor_area_m2      numeric,
    use_code           text,
    use_name           text,
    use_detail         text,
    use_group          text NOT NULL,               -- 상업 용도 행만 적재(이식 스크립트가 이미 필터)
    structure          text,
    source_file_id     bigint REFERENCES meta.dataset_file(file_id)
);

CREATE INDEX building_floor_use_pk_idx ON context.building_floor_use (mgm_bldrgst_pk);
CREATE INDEX building_floor_use_pnu_idx ON context.building_floor_use (pnu);

-- ── 상권 × 스냅샷 요약 (Tier1 물리적 규모 + Tier2 등록 커버리지) ────────────────
CREATE MATERIALIZED VIEW context.commercial_building_area_summary AS
WITH register_agg AS (
    SELECT l.pnu,
           bool_or(r.road_address IS NOT NULL AND r.road_address <> '') AS has_road_address,
           bool_or(r.use_group <> '')                                   AS register_confirms_commercial,
           sum(coalesce(r.parking_indoor_self, 0) + coalesce(r.parking_outdoor_self, 0)
               + coalesce(r.parking_indoor_mech, 0) + coalesce(r.parking_outdoor_mech, 0)) AS parking_total
    FROM context.commercial_building_link l
    JOIN context.building_register r ON r.mgm_bldrgst_pk = l.mgm_bldrgst_pk
    GROUP BY l.pnu
)
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
    count(*) FILTER (WHERE b.building_age_years >= 30)         AS old_ge30y_count,
    count(*) FILTER (WHERE ra.has_road_address)                AS road_address_count,
    count(*) FILTER (WHERE ra.register_confirms_commercial)    AS register_confirmed_count,
    round(avg(ra.parking_total) FILTER (WHERE ra.parking_total IS NOT NULL), 1) AS parking_mean
FROM context.commercial_building b
JOIN location.area a ON a.area_id = b.host_area_id
LEFT JOIN register_agg ra ON ra.pnu = b.pnu
WHERE b.host_area_id IS NOT NULL
GROUP BY b.host_area_id, a.spatial_unit_name, b.snapshot;

CREATE UNIQUE INDEX commercial_building_area_summary_pk
    ON context.commercial_building_area_summary (area_id, snapshot);

COMMIT;
