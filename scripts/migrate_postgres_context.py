"""보조 데이터와 입지 앵커를 PostgreSQL + PostGIS로 이식한다.

선행 조건:
  .venv/bin/python3 scripts/migrate_postgres.py --phase metadata
  .venv/bin/python3 scripts/migrate_postgres.py --phase reference
  .venv/bin/python3 scripts/migrate_postgres.py --phase areas

실행:
  .venv/bin/python3 scripts/migrate_postgres_context.py --phase crosswalks
  .venv/bin/python3 scripts/migrate_postgres_context.py --phase anchors
  .venv/bin/python3 scripts/migrate_postgres_context.py --phase context
  .venv/bin/python3 scripts/migrate_postgres_context.py --phase building
  .venv/bin/python3 scripts/migrate_postgres_context.py --phase evidence
  .venv/bin/python3 scripts/migrate_postgres_context.py --phase all

building 단계는 db/002_commercial_building.sql 적용(v2: commercial_building·building_register·
commercial_building_link·building_floor_use 4테이블 + 요약 matview)과 아래 이식을 선행한다.
  scripts/ingest_building_ledger.py                      → data/건축물대장/상가건물_서울.csv (Tier1)
  scripts/ingest_building_register.py --all --skip-floors → data/건축물대장/api/{표제부,건물링크}_*.csv (Tier2)
  scripts/ingest_building_register.py --sigungu <구>      → 위 + 층별용도_<구>.csv (선택, 무거움)
"""
from __future__ import annotations

import csv
import json
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from migrate_postgres import (  # noqa: E402
    TARGET_INDUSTRIES,
    copy_into,
    csv_rows,
    date_value,
    file_id,
    integer,
    number,
    point_wkt,
    psql_args,
    psql_env,
    run_sql,
    scalar,
    grain_for,
)


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def pg_array(values: Any) -> str:
    """COPY로 전달할 PostgreSQL text[] 리터럴을 만든다."""
    if not isinstance(values, list) or not values:
        return "{}"
    escaped = []
    for value in values:
        item = str(value).replace("\\", "\\\\").replace('"', '\\"')
        escaped.append(f'"{item}"')
    return "{" + ",".join(escaped) + "}"


def as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"y", "yes", "true", "1"}


def as_date(value: Any) -> str | None:
    text = scalar(value)
    if not text:
        return None
    if len(text) == 7 and text[4] == "-":
        candidate = text + "-01"
    elif len(text) == 8 and text.isdigit():
        candidate = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    else:
        candidate = text[:10]
    try:
        datetime.strptime(candidate, "%Y-%m-%d")
    except ValueError:
        return None
    return candidate


def area_id(unit: str, code: Any) -> str | None:
    code = scalar(code)
    return f"{unit}:{code}" if code else None


def point_wkt(x: Any, y: Any) -> str | None:
    x, y = number(x), number(y)
    return f"POINT({x} {y})" if x is not None and y is not None else None


def load_crosswalks() -> None:
    rows: list[list[Any]] = []
    configs = [
        ("crosswalk_trdar_dong.csv", "commercial_to_admin_overlap"),
        ("crosswalk_trdar_alley.csv", "commercial_to_hinterland_containment"),
        ("crosswalk_trdar_alley_spatial.csv", "commercial_to_hinterland_overlap"),
        ("crosswalk_alley_dong.csv", "hinterland_to_admin_overlap"),
        ("crosswalk_alley_self_overlap.csv", "hinterland_self_overlap"),
        ("crosswalk_trdar_self_overlap.csv", "commercial_self_overlap"),
        ("crosswalk_rone_trdar.csv", "rone_to_commercial_proxy"),
    ]
    folder = ROOT / "output" / "crosswalks"
    for filename, relation in configs:
        path = folder / filename
        if not path.is_file():
            continue
        enc, header, records = csv_rows(path)
        source_id = file_id(path.relative_to(ROOT).as_posix())
        for row in records:
            if relation.startswith("commercial_to_admin"):
                source = area_id("commercial_area", row.get("TRDAR_CD"))
                target = area_id("admin_dong", row.get("ADSTRD_CD"))
                overlap = row.get("overlap_area")
                source_ratio = row.get("ratio_of_trdar")
                target_ratio = row.get("ratio_of_dong")
                eligible = True
                proxy = False
            elif relation.startswith("commercial_to_hinterland"):
                source = area_id("commercial_area", row.get("TRDAR_CD"))
                target = area_id("hinterland", row.get("ALLEY_TRDA"))
                overlap = row.get("overlap_area")
                source_ratio = row.get("ratio_of_trdar") or row.get("containment_ratio")
                target_ratio = row.get("ratio_of_alley")
                eligible = bool(source and target)
                proxy = False
            elif relation.startswith("hinterland_to_admin"):
                source = area_id("hinterland", row.get("ALLEY_TRDA"))
                target = area_id("admin_dong", row.get("ADSTRD_CD"))
                overlap = row.get("overlap_area")
                source_ratio = row.get("ratio_of_alley")
                target_ratio = row.get("ratio_of_dong")
                eligible = bool(source and target)
                proxy = False
            elif relation == "hinterland_self_overlap":
                source = area_id("hinterland", row.get("ALLEY_TRDA_A"))
                target = area_id("hinterland", row.get("ALLEY_TRDA_B"))
                overlap = row.get("overlap_area")
                source_ratio = row.get("ratio_of_a")
                target_ratio = row.get("ratio_of_b")
                eligible = bool(source and target)
                proxy = False
            elif relation == "commercial_self_overlap":
                source = area_id("commercial_area", row.get("PARENT_TRDAR_CD"))
                target = area_id("commercial_area", row.get("CHILD_TRDAR_CD"))
                overlap = row.get("overlap_area")
                source_ratio = None
                target_ratio = row.get("child_containment_ratio")
                eligible = bool(source and target)
                proxy = False
            else:
                source_name = scalar(row.get("R_ONE_상권"))
                target = area_id("commercial_area", row.get("TRDAR_CD"))
                source = f"rone:{source_name}" if source_name else None
                overlap = None
                source_ratio = None
                target_ratio = None
                eligible = str(row.get("join_eligible") or "").lower() == "yes"
                proxy = True
            if not source or not target:
                continue
            rows.append([
                relation, source, target, number(overlap), number(source_ratio), number(target_ratio),
                eligible, proxy, scalar(row.get("mapping_method")) or "spatial_crosswalk", source_id,
            ])
    sql = """
CREATE TEMP TABLE _stage_crosswalk (
    relation_type text, source_area_id text, target_area_id text,
    overlap_m2 numeric, source_ratio numeric, target_ratio numeric,
    join_eligible boolean, grain_is_proxy boolean, method text, source_file_id bigint
);
COPY _stage_crosswalk FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO location.area_crosswalk
    (relation_type, source_area_id, target_area_id, overlap_m2, source_ratio,
     target_ratio, join_eligible, grain_is_proxy, method, source_file_id)
SELECT relation_type, source_area_id, target_area_id, overlap_m2, source_ratio,
       target_ratio, join_eligible, grain_is_proxy, method, source_file_id
FROM _stage_crosswalk
ON CONFLICT (relation_type, source_area_id, target_area_id) DO UPDATE SET
    overlap_m2 = EXCLUDED.overlap_m2,
    source_ratio = EXCLUDED.source_ratio,
    target_ratio = EXCLUDED.target_ratio,
    join_eligible = EXCLUDED.join_eligible,
    grain_is_proxy = EXCLUDED.grain_is_proxy,
    method = EXCLUDED.method,
    source_file_id = EXCLUDED.source_file_id;
"""
    print("crosswalks:", copy_into(sql, rows))


def anchor_rows() -> Iterator[list[Any]]:
    # 역사
    station = DATA / "도시철도역사" / "역사정보_서울.csv"
    if station.is_file():
        _, _, records = csv_rows(station)
        source_id = file_id(station.relative_to(ROOT).as_posix())
        for row in records:
            attrs = {k: v for k, v in row.items() if v}
            yield [
                f"STN-{scalar(row.get('역번호'))}-{scalar(row.get('노선명')) or 'unknown'}-"
                f"{scalar(row.get('노선명_원본')) or 'unknown'}", "station",
                scalar(row.get("역사명")) or "역",
                point_wkt(row.get("X_5181"), row.get("Y_5181")), scalar(row.get("경도")), scalar(row.get("위도")),
                scalar(row.get("자치구코드")), scalar(row.get("행정동_코드")),
                area_id("commercial_area", row.get("상권_코드")), "official_file", source_id,
                as_date(row.get("데이터기준일자")), "verified", False, json_text(attrs),
            ]

    # 버스 정류소
    bus = DATA / "버스정류장" / "버스정류소_서울.csv"
    if bus.is_file():
        _, _, records = csv_rows(bus)
        source_id = file_id(bus.relative_to(ROOT).as_posix())
        for row in records:
            attrs = {k: v for k, v in row.items() if v}
            yield [
                f"BUS-{scalar(row.get('NODE_ID'))}", "bus_stop", scalar(row.get("정류소명")) or "정류소",
                point_wkt(row.get("X_5181"), row.get("Y_5181")), scalar(row.get("경도")), scalar(row.get("위도")),
                scalar(row.get("자치구코드")), scalar(row.get("행정동_코드")),
                area_id("commercial_area", row.get("상권_코드")), "official_file", source_id,
                "2026-08-01", "conditional", False, json_text(attrs),
            ]

    # 아파트
    apartment = DATA / "공동주택" / "아파트단지_서울.csv"
    if apartment.is_file():
        _, _, records = csv_rows(apartment)
        source_id = file_id(apartment.relative_to(ROOT).as_posix())
        for row in records:
            attrs = {k: v for k, v in row.items() if v}
            confidence = scalar(row.get("geocode_신뢰도")) or "conditional"
            confidence = "verified" if confidence.lower() in {"high", "medium", "verified"} else "conditional"
            yield [
                f"APT-{scalar(row.get('단지코드'))}", "apartment", scalar(row.get("단지명")) or "아파트",
                point_wkt(row.get("X_5181"), row.get("Y_5181")), scalar(row.get("경도")), scalar(row.get("위도")),
                None, scalar(row.get("행정동_코드")), area_id("commercial_area", row.get("상권_코드")),
                "official_api_snapshot", source_id, "2026-08-01", confidence, False, json_text(attrs),
            ]

    # Kakao POI는 root/context 파일 모두 anchor로 활용할 수 있다.
    for path in sorted((DATA / "카카오POI").rglob("*.csv")):
        _, _, records = csv_rows(path)
        source_id = file_id(path.relative_to(ROOT).as_posix())
        for row in records:
            attrs = {k: v for k, v in row.items() if v}
            yield [
                f"POI-{scalar(row.get('poi_id'))}", "kakao_poi", scalar(row.get("place_name")) or "POI",
                point_wkt(row.get("x_5181"), row.get("y_5181")), scalar(row.get("x_wgs84")), scalar(row.get("y_wgs84")),
                None, None, None, "observed_poi_snapshot", source_id,
                scalar(row.get("retrieved_at_utc")), "verified", False, json_text(attrs),
            ]


def anchor_snapshot_rows() -> Iterator[list[Any]]:
    """anchor 원천 CSV 를 행 단위로 verbatim (빈 값 포함). row_seq = 파일 내 순서."""
    sources = [
        (DATA / "도시철도역사" / "역사정보_서울.csv", "station"),
        (DATA / "버스정류장" / "버스정류소_서울.csv", "bus_stop"),
        (DATA / "공동주택" / "아파트단지_서울.csv", "apartment"),
    ]
    sources += [(p, "kakao_poi") for p in sorted((DATA / "카카오POI").rglob("*.csv"))]
    for path, anchor_type in sources:
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        source_id = _file_id_or_none(rel)
        _, _, records = csv_rows(path)
        for seq, row in enumerate(records):
            yield [rel, seq, anchor_type, json_text(dict(row)), source_id]


def load_anchor_snapshot() -> None:
    sql = """
CREATE TEMP TABLE _stage_anchor_snap (
    source_file text, row_seq integer, anchor_type text, attributes jsonb, source_file_id bigint
);
COPY _stage_anchor_snap FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO context.anchor_snapshot (source_file, row_seq, anchor_type, attributes, source_file_id)
    SELECT DISTINCT ON (source_file, row_seq) source_file, row_seq, anchor_type, attributes, source_file_id
    FROM _stage_anchor_snap ORDER BY source_file, row_seq
ON CONFLICT (source_file, row_seq) DO UPDATE SET
    anchor_type = EXCLUDED.anchor_type, attributes = EXCLUDED.attributes, source_file_id = EXCLUDED.source_file_id;
"""
    print("anchor_snapshot:", copy_into(sql, anchor_snapshot_rows()))


def load_anchors() -> None:
    sql = """
CREATE TEMP TABLE _stage_anchor (
    anchor_id text, anchor_type text, place_name text, point_wkt text,
    x_wgs84 numeric, y_wgs84 numeric, sigungu_code text, admin_dong_code text,
    host_area_id text, source_type text, source_file_id bigint, observed_at timestamptz,
    coordinate_confidence text, synthetic_anchor boolean, source_attributes jsonb
);
COPY _stage_anchor FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO location.anchor_point
    (anchor_id, anchor_type, place_name, point, x_wgs84, y_wgs84, sigungu_code,
     admin_dong_code, host_area_id, source_type, source_file_id, observed_at,
     coordinate_confidence, synthetic_anchor, source_attributes)
SELECT anchor_id, anchor_type, place_name,
       CASE WHEN point_wkt IS NULL THEN NULL ELSE ST_GeomFromText(point_wkt, 5181) END,
       x_wgs84, y_wgs84, sigungu_code, admin_dong_code, host_area_id, source_type,
       source_file_id, observed_at, coordinate_confidence, synthetic_anchor, source_attributes
FROM (
    SELECT DISTINCT ON (anchor_id) *
    FROM _stage_anchor
    ORDER BY anchor_id, observed_at DESC NULLS LAST
) AS deduped
ON CONFLICT (anchor_id) DO UPDATE SET
    anchor_type = EXCLUDED.anchor_type,
    place_name = EXCLUDED.place_name,
    point = EXCLUDED.point,
    x_wgs84 = EXCLUDED.x_wgs84,
    y_wgs84 = EXCLUDED.y_wgs84,
    sigungu_code = EXCLUDED.sigungu_code,
    admin_dong_code = EXCLUDED.admin_dong_code,
    host_area_id = EXCLUDED.host_area_id,
    source_type = EXCLUDED.source_type,
    source_file_id = EXCLUDED.source_file_id,
    observed_at = EXCLUDED.observed_at,
    coordinate_confidence = EXCLUDED.coordinate_confidence,
    synthetic_anchor = EXCLUDED.synthetic_anchor,
    source_attributes = EXCLUDED.source_attributes;
"""
    print("anchors:", copy_into(sql, anchor_rows()))


def poi_rows() -> Iterator[list[Any]]:
    for path in sorted((DATA / "카카오POI").rglob("*.csv")):
        _, _, records = csv_rows(path)
        source_id = file_id(path.relative_to(ROOT).as_posix())
        for row in records:
            base = {"source", "poi_id", "retrieved_at_utc", "place_name", "category_group_code", "category_group_name",
                    "x_wgs84", "y_wgs84", "x_5181", "y_5181", "road_address_name", "address_name", "place_url",
                    "search_mode", "search_query", "search_center_x", "search_center_y", "search_radius_m"}
            yield [
                scalar(row.get("source")) or "Kakao Local REST API", scalar(row.get("poi_id")),
                scalar(row.get("retrieved_at_utc")), scalar(row.get("place_name")) or "POI",
                scalar(row.get("category_group_code")), scalar(row.get("category_group_name")),
                point_wkt(row.get("x_5181"), row.get("y_5181")), scalar(row.get("x_wgs84")), scalar(row.get("y_wgs84")),
                scalar(row.get("road_address_name")), scalar(row.get("address_name")), scalar(row.get("place_url")),
                scalar(row.get("search_mode")), scalar(row.get("search_query")), number(row.get("search_center_x")),
                number(row.get("search_center_y")), number(row.get("search_radius_m")),
                json_text({k: v for k, v in row.items() if k not in base and v}), source_id,
            ]


def load_poi() -> None:
    sql = """
CREATE TEMP TABLE _stage_poi (
    source text, poi_id text, retrieved_at timestamptz, place_name text,
    category_group_code text, category_group_name text, point_wkt text,
    x_wgs84 numeric, y_wgs84 numeric, road_address text, address text, place_url text,
    search_mode text, search_query text, search_center_x numeric, search_center_y numeric,
    search_radius_m numeric, source_attributes jsonb, source_file_id bigint
);
COPY _stage_poi FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO context.poi_snapshot
    (source, poi_id, retrieved_at, place_name, category_group_code, category_group_name,
     point, x_wgs84, y_wgs84, road_address, address, place_url, search_mode, search_query,
     search_center_x, search_center_y, search_radius_m, source_attributes)
SELECT source, poi_id, retrieved_at, place_name, category_group_code, category_group_name,
       CASE WHEN point_wkt IS NULL THEN NULL ELSE ST_GeomFromText(point_wkt, 5181) END,
       x_wgs84, y_wgs84, road_address, address, place_url, search_mode, search_query,
       search_center_x, search_center_y, search_radius_m, source_attributes
FROM (
    SELECT DISTINCT ON (source, poi_id, retrieved_at) *
    FROM _stage_poi
    ORDER BY source, poi_id, retrieved_at DESC
) AS deduped
ON CONFLICT (source, poi_id, retrieved_at) DO UPDATE SET
    place_name = EXCLUDED.place_name,
    category_group_code = EXCLUDED.category_group_code,
    category_group_name = EXCLUDED.category_group_name,
    point = EXCLUDED.point,
    x_wgs84 = EXCLUDED.x_wgs84,
    y_wgs84 = EXCLUDED.y_wgs84,
    road_address = EXCLUDED.road_address,
    address = EXCLUDED.address,
    place_url = EXCLUDED.place_url,
    source_attributes = EXCLUDED.source_attributes;
"""
    print("poi:", copy_into(sql, poi_rows()))


def news_rows() -> Iterator[list[Any]]:
    paths = [DATA / "뉴스" / "naver_news_snapshot.jsonl"]
    paths += sorted(DATA.glob("뉴스/bigkinds_news_*.jsonl"))
    for path in paths:
        if not path.is_file():
            continue
        source_id = file_id(path.relative_to(ROOT).as_posix())
        with path.open(encoding="utf-8-sig") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                query = row.get("query_label")
                if not query and row.get("query_labels"):
                    query = row["query_labels"][0]
                tags = lambda key: row.get(key) if isinstance(row.get(key), list) else []
                known = {"source", "news_id", "published_date", "publisher", "title", "url", "query_label",
                         "query_labels", "search_period", "sigungu_tags", "dong_tags", "topic_tags", "topic_match",
                         "retrieved_at_utc"}
                yield [
                    scalar(row.get("source")), scalar(row.get("news_id")), as_date(row.get("published_date")),
                    scalar(row.get("publisher")), scalar(row.get("title")) or "제목 없음", scalar(row.get("url")),
                    scalar(query), json_text(row.get("search_period") or {}), pg_array(tags("sigungu_tags")),
                    pg_array(tags("dong_tags")), pg_array(tags("topic_tags")), row.get("topic_match"),
                    scalar(row.get("retrieved_at_utc")),
                    json_text({k: v for k, v in row.items() if k not in known}), source_id,
                ]


def load_news() -> None:
    sql = """
CREATE TEMP TABLE _stage_news (
    source text, news_id text, published_date date, publisher text, title text, url text,
    query_label text, search_period jsonb, sigungu_tags text[], dong_tags text[], topic_tags text[],
    topic_match boolean, retrieved_at timestamptz, source_attributes jsonb, source_file_id bigint
);
COPY _stage_news FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO context.news_snapshot
    (source, news_id, published_date, publisher, title, url, query_label, search_period,
     sigungu_tags, dong_tags, topic_tags, topic_match, retrieved_at, source_attributes)
SELECT source, news_id, published_date, publisher, title, url, query_label, search_period,
       sigungu_tags, dong_tags, topic_tags, topic_match, retrieved_at, source_attributes
FROM _stage_news
ON CONFLICT (source, news_id) DO UPDATE SET
    published_date = EXCLUDED.published_date,
    publisher = EXCLUDED.publisher,
    title = EXCLUDED.title,
    url = EXCLUDED.url,
    query_label = EXCLUDED.query_label,
    search_period = EXCLUDED.search_period,
    sigungu_tags = EXCLUDED.sigungu_tags,
    dong_tags = EXCLUDED.dong_tags,
    topic_tags = EXCLUDED.topic_tags,
    topic_match = EXCLUDED.topic_match,
    retrieved_at = EXCLUDED.retrieved_at,
    source_attributes = EXCLUDED.source_attributes;
"""
    print("news:", copy_into(sql, news_rows()))


def news_manifest_rows() -> Iterator[list[Any]]:
    for path in sorted(DATA.glob("뉴스/*_manifest.json")):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        source = scalar(manifest.get("source"))
        if not source:
            continue
        yield [source, json_text(manifest), file_id(path.relative_to(ROOT).as_posix())]


def load_news_manifest() -> None:
    sql = """
CREATE TEMP TABLE _stage_news_manifest (source text, manifest jsonb, source_file_id bigint);
COPY _stage_news_manifest FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO context.news_manifest (source, manifest, source_file_id)
    SELECT DISTINCT ON (source) source, manifest, source_file_id FROM _stage_news_manifest ORDER BY source
ON CONFLICT (source) DO UPDATE SET
    manifest = EXCLUDED.manifest, source_file_id = EXCLUDED.source_file_id, ingested_at = now();
"""
    print("news_manifest:", copy_into(sql, news_manifest_rows()))


def rent_index_rows() -> Iterator[list[Any]]:
    # 임대료·임대가격지수(웹 다운로드 CSV 파싱) + 공실률(R-ONE API 실호출,
    # scripts/ingest_vacancy_rate.py) — 둘 다 같은 long 스키마라 같은 테이블에 적재.
    for filename in ("R-ONE_임대동향_분기.csv", "R-ONE_공실률_분기.csv"):
        path = DATA / "임대료" / filename
        if not path.is_file():
            continue
        source_id = file_id(path.relative_to(ROOT).as_posix())
        _, _, records = csv_rows(path)
        for row in records:
            grain = scalar(row.get("grain"))
            period = scalar(row.get("기준_년분기_코드"))
            store_type = scalar(row.get("상가유형"))
            indicator = scalar(row.get("지표"))
            if not (grain and period and store_type and indicator) or grain not in ("상권", "권역", "서울전체"):
                continue
            yield [
                period, store_type, indicator, grain,
                scalar(row.get("R_ONE_상권")) or "", scalar(row.get("권역")),
                number(row.get("값")), source_id,
            ]


def _file_id_or_none(rel_path: str) -> int | None:
    try:
        return file_id(rel_path)
    except RuntimeError:
        return None


def naver_seasonality_rows() -> Iterator[list[Any]]:
    path = ROOT / "output" / "feature_validation" / "naver_seasonality.csv"
    if not path.is_file():
        return
    source_id = _file_id_or_none(path.relative_to(ROOT).as_posix())
    _, _, records = csv_rows(path)
    for row in records:
        grain = scalar(row.get("grain"))
        key = scalar(row.get("key"))
        if not grain or not key:
            continue
        yield [grain, key, json_text(row), source_id]


def load_naver_seasonality() -> None:
    sql = """
CREATE TEMP TABLE _stage_seasonality (grain text, key text, attributes jsonb, source_file_id bigint);
COPY _stage_seasonality FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO context.naver_seasonality (grain, key, attributes, source_file_id)
    SELECT DISTINCT ON (grain, key) grain, key, attributes, source_file_id FROM _stage_seasonality ORDER BY grain, key
ON CONFLICT (grain, key) DO UPDATE SET
    attributes = EXCLUDED.attributes, source_file_id = EXCLUDED.source_file_id;
"""
    print("naver_seasonality:", copy_into(sql, naver_seasonality_rows()))


def load_rent_index() -> None:
    sql = """
CREATE TEMP TABLE _stage_rent (
    period text, store_type text, indicator text, grain text,
    rone_area text, zone text, value_numeric numeric, source_file_id bigint
);
COPY _stage_rent FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO context.rent_index
    SELECT DISTINCT ON (period, store_type, indicator, grain, rone_area)
           period, store_type, indicator, grain, rone_area, zone, value_numeric, source_file_id
    FROM _stage_rent
    ORDER BY period, store_type, indicator, grain, rone_area
ON CONFLICT (period, store_type, indicator, grain, rone_area) DO UPDATE SET
    zone = EXCLUDED.zone, value_numeric = EXCLUDED.value_numeric, source_file_id = EXCLUDED.source_file_id;
"""
    print("rent_index:", copy_into(sql, rent_index_rows()))


def population_rows() -> Iterator[list[Any]]:
    """상주·직장인구의 공간/분기별 수치와 구성 컬럼을 metric으로 보존한다."""
    configs = [("resident", "상주인구"), ("worker", "직장인구")]
    for prefix, folder in configs:
        for path in sorted((DATA / folder).glob("*.csv")):
            unit, code_field, name_field = grain_for(path)
            _, _, records = csv_rows(path)
            source_id = file_id(path.relative_to(ROOT).as_posix())
            structural = {
                "기준_년분기_코드", "상권_구분_코드", "상권_구분_코드_명",
                "상권_코드", "상권_코드_명", "상권배후지_코드", "상권배후지_코드_명",
                "행정동_코드", "행정동_코드_명",
            }
            for row in records:
                period = scalar(row.get("기준_년분기_코드"))
                code = scalar(row.get(code_field))
                name = scalar(row.get(name_field))
                if not period or not code:
                    continue
                for key, value in row.items():
                    if key in structural:
                        continue
                    numeric_value = number(value)
                    if numeric_value is None:
                        continue
                    unit_label = "가구" if "가구" in key else "명"
                    yield [f"{prefix}_{key}", period, "quarter", unit, code, name, None,
                           numeric_value, None, unit_label, False, None, "official_file",
                           source_id, period, None]


def metric_rows() -> Iterator[list[Any]]:
    yield from population_rows()
    # 상권변화지표: 공식 LL/LH/HL/HH와 원천 개월값을 별도 metric으로 보존
    for path in sorted((DATA / "상권변화지표").glob("*.csv")):
        normalized_name = unicodedata.normalize("NFC", path.name)
        unit = "admin_dong" if "행정동" in normalized_name else "commercial_area"
        _, _, records = csv_rows(path)
        source_id = file_id(path.relative_to(ROOT).as_posix())
        for row in records:
            code_field = "행정동_코드" if unit == "admin_dong" else "상권_코드"
            name_field = "행정동_코드_명" if unit == "admin_dong" else "상권_코드_명"
            for metric_name, value, value_type in [
                ("change_indicator_code", row.get("상권_변화_지표"), "text"),
                ("change_indicator_name", row.get("상권_변화_지표_명"), "text"),
                ("operating_months_avg", row.get("운영_영업_개월_평균"), "numeric"),
                ("closing_months_avg", row.get("폐업_영업_개월_평균"), "numeric"),
                ("seoul_operating_months_avg", row.get("서울_운영_영업_개월_평균"), "numeric"),
                ("seoul_closing_months_avg", row.get("서울_폐업_영업_개월_평균"), "numeric"),
            ]:
                yield [metric_name, scalar(row.get("기준_년분기_코드")), "quarter", unit,
                       scalar(row.get(code_field)), scalar(row.get(name_field)), None,
                       number(value) if value_type == "numeric" else None,
                       scalar(value) if value_type == "text" else None,
                       "개월" if value_type == "numeric" else "코드", False, None,
                       "official_file", source_id, scalar(row.get("기준_년분기_코드")), None]

    # 외국인 생활인구: 월/분기 값을 metric별로 저장
    for path in sorted((DATA / "외국인생활인구").glob("*.csv")):
        period_type = "month" if "월" in path.name else "quarter"
        period_field = "기준_년월" if period_type == "month" else "기준_년분기_코드"
        _, _, records = csv_rows(path)
        source_id = file_id(path.relative_to(ROOT).as_posix())
        ignored = {period_field, "행정동_코드", "영역행정동_매칭"}
        for row in records:
            for key, value in row.items():
                if key in ignored or number(value) is None:
                    continue
                yield ["foreign_" + key, scalar(row.get(period_field)), period_type, "admin_dong",
                       scalar(row.get("행정동_코드")), None, None, number(value), None, "명/평균",
                       False, None, "official_api_snapshot", source_id, scalar(row.get(period_field)), None]

    # 자치구 고용률
    path = DATA / "고용률" / "자치구_고용률_반기.csv"
    if path.is_file():
        _, _, records = csv_rows(path)
        source_id = file_id(path.relative_to(ROOT).as_posix())
        for row in records:
            sex = scalar(row.get("성별")) or "all"
            yield [f"employment_rate_{sex}", scalar(row.get("기준_반기")), "half_year", "sigungu",
                   scalar(row.get("자치구_코드")), scalar(row.get("자치구")), None,
                   number(row.get("고용률")), None, "%", True, "자치구값을 상권·동에 대리하지 않음",
                   "official_file", source_id, scalar(row.get("기준_반기")), None]

    # R-ONE 임대동향·공실률 (임대료·지수는 웹 다운로드 CSV, 공실률은 API 실호출)
    for path in (DATA / "임대료" / "R-ONE_임대동향_분기.csv", DATA / "임대료" / "R-ONE_공실률_분기.csv"):
        if not path.is_file():
            continue
        _, _, records = csv_rows(path)
        source_id = file_id(path.relative_to(ROOT).as_posix())
        for row in records:
            code = "|".join(filter(None, [scalar(row.get("권역")), scalar(row.get("R_ONE_상권"))]))
            yield [f"rone_{scalar(row.get('지표'))}_{scalar(row.get('상가유형'))}",
                   scalar(row.get("기준_년분기_코드")), "quarter", "region", code, code, None,
                   number(row.get("값")), None, "원천단위", True, "R-ONE 조사권역·상권 proxy",
                   "official_api_snapshot", source_id, scalar(row.get("기준_년분기_코드")), None]

    # 네이버 검색 트렌드의 상대지수
    for path in sorted((DATA / "네이버트렌드").glob("*_검색트렌드_월.csv")):
        _, _, records = csv_rows(path)
        source_id = file_id(path.relative_to(ROOT).as_posix())
        if "업종" in path.name:
            for row in records:
                code = scalar(row.get("업종코드"))
                if code not in TARGET_INDUSTRIES:
                    continue
                yield ["naver_rel_index", scalar(row.get("기준_년월")), "month", "region", "seoul",
                       "서울시 전체", code, number(row.get("rel_index")), None, "상대지수", False, None,
                       "official_api_snapshot", source_id, scalar(row.get("기준_년월")), None]
        elif "자치구" in path.name:
            for row in records:
                name = scalar(row.get("자치구"))
                yield ["naver_rel_index", scalar(row.get("기준_년월")), "month", "sigungu", name, name,
                       None, number(row.get("rel_index")), None, "상대지수", False, "절대 검색량 아님",
                       "official_api_snapshot", source_id, scalar(row.get("기준_년월")), None]
        elif "행정동" in unicodedata.normalize("NFC", path.name):
            for row in records:
                yield ["naver_rel_index", scalar(row.get("기준_년월")), "month", "admin_dong",
                       scalar(row.get("행정동_코드")), scalar(row.get("행정동명")), None,
                       number(row.get("rel_index")), None, "상대지수", False, "통용지명 근사·절대 검색량 아님",
                       "official_api_snapshot", source_id, scalar(row.get("기준_년월")), None]


def load_metrics() -> None:
    sql = """
CREATE TEMP TABLE _stage_metric (
    metric_name text, period text, period_type text, spatial_unit_type text,
    spatial_unit_code text, spatial_unit_name text, industry_code text,
    value_numeric numeric, value_text text, unit text, grain_is_proxy boolean,
    proxy_note text, source_type text, source_file_id bigint, observed_at text,
    missing_reason text
);
COPY _stage_metric FROM STDIN WITH (FORMAT csv, NULL '\\N');
DELETE FROM context.metric_snapshot m
USING (
    SELECT DISTINCT metric_name, period, period_type, spatial_unit_type,
                    spatial_unit_code, industry_code
    FROM _stage_metric
) s
WHERE m.metric_name = s.metric_name
  AND m.period = s.period
  AND m.period_type = s.period_type
  AND m.spatial_unit_type = s.spatial_unit_type
  AND m.spatial_unit_code IS NOT DISTINCT FROM s.spatial_unit_code
  AND m.industry_code IS NOT DISTINCT FROM s.industry_code;
INSERT INTO context.metric_snapshot
    (metric_name, period, period_type, spatial_unit_type, spatial_unit_code,
     spatial_unit_name, industry_code, value_numeric, value_text, unit,
     grain_is_proxy, proxy_note, source_type, source_file_id, observed_at, missing_reason)
SELECT DISTINCT ON (metric_name, period, period_type, spatial_unit_type,
                    spatial_unit_code, industry_code)
       metric_name, period, period_type, spatial_unit_type, spatial_unit_code,
       spatial_unit_name, industry_code, value_numeric, value_text, unit,
       grain_is_proxy, proxy_note, source_type, source_file_id,
       CASE WHEN observed_at ~ '^[0-9]{8}$'
            THEN to_timestamp(observed_at, 'YYYYMMDD') ELSE NULL END,
       missing_reason
FROM _stage_metric
ORDER BY metric_name, period, period_type, spatial_unit_type,
         spatial_unit_code, industry_code, source_file_id DESC
ON CONFLICT (metric_name, period, period_type, spatial_unit_type, spatial_unit_code, industry_code)
DO UPDATE SET value_numeric = EXCLUDED.value_numeric, value_text = EXCLUDED.value_text,
    unit = EXCLUDED.unit, grain_is_proxy = EXCLUDED.grain_is_proxy,
    proxy_note = EXCLUDED.proxy_note, source_type = EXCLUDED.source_type,
    source_file_id = EXCLUDED.source_file_id, observed_at = EXCLUDED.observed_at,
    missing_reason = EXCLUDED.missing_reason;
"""
    print("metrics:", copy_into(sql, metric_rows()))


def plan_rows() -> Iterator[list[Any]]:
    configs = [
        (DATA / "도시계획사업" / "도시계획사업_상권겹침.csv", "urban_project_overlap"),
        (DATA / "도시계획사업" / "정비사업조합_목록.csv", "redevelopment_association"),
        (DATA / "도시철도역사" / "도시철도망계획_노선.csv", "subway_network_plan"),
    ]
    for path, plan_type in configs:
        if not path.is_file():
            continue
        _, _, records = csv_rows(path)
        source_id = file_id(path.relative_to(ROOT).as_posix())
        for row in records:
            if plan_type == "urban_project_overlap":
                unit, code = "commercial_area", scalar(row.get("상권_코드"))
                name = scalar(row.get("사업장명"))
                category = scalar(row.get("사업유형")) or scalar(row.get("사업유형_대분류"))
                stage = scalar(row.get("추진단계"))
                overlap, ratio = number(row.get("겹침_면적")), number(row.get("겹침_상권비율"))
                observed = as_date(row.get("생성일")) or "2026-09-01"
            elif plan_type == "redevelopment_association":
                unit, code = "sigungu", scalar(row.get("자치구_코드"))
                name, category, stage = scalar(row.get("사업장명")), scalar(row.get("사업구분")), scalar(row.get("진행단계"))
                overlap, ratio, observed = None, None, "2026-09-01"
            else:
                unit, code = "region", scalar(row.get("노선명"))
                name, category, stage = scalar(row.get("노선명")), scalar(row.get("노선유형")), scalar(row.get("상태_2026"))
                overlap, ratio, observed = None, None, "2020-11-17"
            yield [plan_type, unit, code, name, category, stage, overlap, ratio, None, observed,
                   "official_file", source_id, json_text({k: v for k, v in row.items() if v})]


def load_plans() -> None:
    sql = """
CREATE TEMP TABLE _stage_plan (
    plan_type text, spatial_unit_type text, spatial_unit_code text, project_name text,
    project_category text, progress_stage text, overlap_m2 numeric, overlap_ratio numeric,
    plan_geom_wkt text, observed_at date, source_type text, source_file_id bigint, source_attributes jsonb
);
COPY _stage_plan FROM STDIN WITH (FORMAT csv, NULL '\\N');
DELETE FROM context.plan_snapshot p
USING (SELECT DISTINCT source_file_id FROM _stage_plan) s
WHERE p.source_file_id = s.source_file_id;
INSERT INTO context.plan_snapshot
    (plan_type, spatial_unit_type, spatial_unit_code, project_name, project_category,
     progress_stage, overlap_m2, overlap_ratio, plan_geom, observed_at, source_type,
     source_file_id, source_attributes)
SELECT DISTINCT plan_type, spatial_unit_type, spatial_unit_code, project_name, project_category,
       progress_stage, overlap_m2, overlap_ratio,
       CASE WHEN plan_geom_wkt IS NULL THEN NULL ELSE ST_GeomFromText(plan_geom_wkt, 5181) END,
       observed_at, source_type, source_file_id, source_attributes
FROM _stage_plan;
"""
    print("plans:", copy_into(sql, plan_rows()))


def evidence_rows() -> tuple[list[list[Any]], list[list[Any]]]:
    runs: list[list[Any]] = []
    candidates: list[list[Any]] = []
    base = ROOT / "output" / "recommendation_runs"
    for run_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        manifest_path = run_dir / "run-manifest.json"
        candidates_path = run_dir / "candidates.json"
        if not manifest_path.is_file() or not candidates_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        request_path = run_dir / "request.json"
        coverage_path = run_dir / "coverage-summary.json"
        request = json.loads(request_path.read_text(encoding="utf-8")) if request_path.is_file() else manifest.get("request", {})
        coverage = json.loads(coverage_path.read_text(encoding="utf-8")) if coverage_path.is_file() else {}
        run_id = str(uuid5(NAMESPACE_URL, f"data-analysis/recommendation_runs/{run_dir.name}"))
        data_version = str(request.get("quarter") or manifest.get("generated_at") or "unknown")
        runs.append([run_id, data_version, json_text(request), json_text(coverage), json_text(manifest)])
        raw_candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
        if not isinstance(raw_candidates, list):
            continue
        for candidate in raw_candidates:
            if not isinstance(candidate, dict) or not scalar(candidate.get("candidate_id")):
                continue
            candidates.append([
                run_id, scalar(candidate.get("candidate_id")), scalar(candidate.get("candidate_type")),
                scalar(candidate.get("fit_tier")), scalar(candidate.get("data_confidence")),
                json_text(candidate),
            ])
    return runs, candidates


def load_evidence() -> None:
    runs, candidates = evidence_rows()
    run_sql = """
CREATE TEMP TABLE _stage_evidence_run (
    run_id uuid, data_version text, request jsonb, coverage_summary jsonb, run_manifest jsonb
);
COPY _stage_evidence_run FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO evidence.recommendation_run
    (run_id, data_version, request, coverage_summary, run_manifest)
SELECT run_id, data_version, request, coverage_summary, run_manifest
FROM _stage_evidence_run
ON CONFLICT (run_id) DO UPDATE SET
    data_version = EXCLUDED.data_version,
    request = EXCLUDED.request,
    coverage_summary = EXCLUDED.coverage_summary,
    run_manifest = EXCLUDED.run_manifest;
"""
    candidate_sql = """
CREATE TEMP TABLE _stage_evidence_candidate (
    run_id uuid, candidate_id text, candidate_type text, fit_tier text,
    data_confidence text, candidate_json jsonb
);
COPY _stage_evidence_candidate FROM STDIN WITH (FORMAT csv, NULL '\\N');
DELETE FROM evidence.candidate c
USING (SELECT DISTINCT run_id FROM _stage_evidence_candidate) r
WHERE c.run_id = r.run_id;
INSERT INTO evidence.candidate
    (run_id, candidate_id, candidate_type, fit_tier, data_confidence, candidate_json)
SELECT run_id, candidate_id, candidate_type, fit_tier, data_confidence, candidate_json
FROM _stage_evidence_candidate
ON CONFLICT (run_id, candidate_id) DO UPDATE SET
    candidate_type = EXCLUDED.candidate_type,
    fit_tier = EXCLUDED.fit_tier,
    data_confidence = EXCLUDED.data_confidence,
    candidate_json = EXCLUDED.candidate_json;
"""
    print("evidence runs:", copy_into(run_sql, runs),
          "candidates:", copy_into(candidate_sql, candidates))


def _building_snapshot() -> str:
    manifest = DATA / "건축물대장" / "manifest.json"
    if manifest.is_file():
        try:
            return str(json.loads(manifest.read_text(encoding="utf-8"))["source"]["snapshot"])
        except (KeyError, ValueError):
            pass
    return "unknown"


def commercial_building_rows() -> Iterator[list[Any]]:
    path = DATA / "건축물대장" / "상가건물_서울.csv"
    if not path.is_file():
        raise RuntimeError(f"{path} 없음 — scripts/ingest_building_ledger.py 를 먼저 실행하세요")
    snapshot = _building_snapshot()
    source_id = file_id(path.relative_to(ROOT).as_posix())
    for row in csv_rows(path)[2]:
        area_code = scalar(row.get("상권_코드"))
        join = scalar(row.get("상권_결합")) or "미결합"
        yield [
            scalar(row.get("건물관리번호")), snapshot,
            scalar(row.get("PNU")), scalar(row.get("시군구코드")), scalar(row.get("시군구명")),
            scalar(row.get("법정동코드")), scalar(row.get("대지위치")), scalar(row.get("지번")),
            scalar(row.get("지번구분")), scalar(row.get("용도코드")), scalar(row.get("용도명")),
            scalar(row.get("용도군")), integer(row.get("지상층수")), integer(row.get("지하층수")),
            number(row.get("건축면적_㎡")), number(row.get("연면적_㎡")),
            number(row.get("건폐율_pct")), number(row.get("용적률_pct")), number(row.get("높이_m")),
            scalar(row.get("구조")), date_value(row.get("사용승인일")), integer(row.get("건물연식_년")),
            number(row.get("footprint_㎡")), point_wkt(row.get("x_5181"), row.get("y_5181")),
            number(row.get("경도")), number(row.get("위도")),
            f"commercial_area:{area_code}" if area_code and join != "미결합" else None,
            join, scalar(row.get("행정동_코드")), scalar(row.get("행정동_명")), source_id,
        ]


def load_commercial_building() -> None:
    sql = """
CREATE TEMP TABLE _stage_cb (
    building_pk text, snapshot text, pnu text, sigungu_code text, sigungu_name text,
    legal_dong_code text, lot_address text, lot_number text, lot_kind text,
    use_code text, use_name text, use_group text, floors_above integer, floors_below integer,
    building_area_m2 numeric, gross_floor_area_m2 numeric, building_coverage_pct numeric,
    floor_area_ratio_pct numeric, height_m numeric, structure text, approval_date date,
    building_age_years integer, footprint_m2 numeric, point_wkt text, lon numeric, lat numeric,
    host_area_id text, area_join_type text, admin_dong_code text, admin_dong_name text,
    source_file_id bigint
);
COPY _stage_cb FROM STDIN WITH (FORMAT csv, NULL '\\N');
DELETE FROM context.commercial_building b
USING (SELECT DISTINCT snapshot FROM _stage_cb) s
WHERE b.snapshot = s.snapshot;
INSERT INTO context.commercial_building
    (building_pk, snapshot, pnu, sigungu_code, sigungu_name, legal_dong_code, lot_address,
     lot_number, lot_kind, use_code, use_name, use_group, floors_above, floors_below,
     building_area_m2, gross_floor_area_m2, building_coverage_pct, floor_area_ratio_pct,
     height_m, structure, approval_date, building_age_years, footprint_m2, point, lon, lat,
     host_area_id, area_join_type, admin_dong_code, admin_dong_name, source_file_id)
SELECT building_pk, snapshot, pnu, sigungu_code, sigungu_name, legal_dong_code, lot_address,
       lot_number, lot_kind, use_code, use_name, use_group, floors_above, floors_below,
       building_area_m2, gross_floor_area_m2, building_coverage_pct, floor_area_ratio_pct,
       height_m, structure, approval_date, building_age_years, footprint_m2,
       CASE WHEN point_wkt IS NULL THEN NULL ELSE ST_GeomFromText(point_wkt, 5181) END,
       lon, lat, host_area_id, area_join_type, admin_dong_code, admin_dong_name, source_file_id
FROM (SELECT DISTINCT ON (building_pk, snapshot) * FROM _stage_cb
      ORDER BY building_pk, snapshot) d
ON CONFLICT (building_pk, snapshot) DO NOTHING;
"""
    print("commercial_building:", copy_into(sql, commercial_building_rows()))


def _gu_of(addr: str) -> str:
    if "서울특별시 " in addr:
        parts = addr.split("서울특별시 ", 1)[1].split()
        if parts and parts[0].endswith("구"):
            return parts[0]
    return ""


def building_register_rows() -> Iterator[list[Any]]:
    paths = sorted((DATA / "건축물대장" / "api").glob("표제부_*.csv"))
    if not paths:
        raise RuntimeError(
            f"{DATA / '건축물대장' / 'api'}/표제부_*.csv 없음 — "
            "scripts/ingest_building_register.py --all --skip-floors 를 먼저 실행하세요"
        )
    for path in paths:
        source_id = file_id(path.relative_to(ROOT).as_posix())
        for row in csv_rows(path)[2]:
            yield [
                scalar(row.get("mgmBldrgstPk")), scalar(row.get("PNU")),
                _gu_of(row.get("지번주소", "")), scalar(row.get("지번주소")), scalar(row.get("도로명주소")),
                scalar(row.get("건물명")), scalar(row.get("동명칭")), scalar(row.get("대장종류")),
                scalar(row.get("주용도코드")), scalar(row.get("주용도")), scalar(row.get("상세용도")),
                scalar(row.get("용도군")), scalar(row.get("구조")), scalar(row.get("지붕")),
                number(row.get("대지면적_㎡")), number(row.get("건축면적_㎡")), number(row.get("연면적_㎡")),
                number(row.get("건폐율_pct")), number(row.get("용적률_pct")), number(row.get("높이_m")),
                integer(row.get("지상층수")), integer(row.get("지하층수")),
                integer(row.get("승용승강기")), integer(row.get("비상용승강기")),
                integer(row.get("호수")), integer(row.get("세대수")), integer(row.get("가구수")),
                integer(row.get("옥내기계식_대수")), integer(row.get("옥외기계식_대수")),
                integer(row.get("옥내자주식_대수")), integer(row.get("옥외자주식_대수")),
                date_value(row.get("허가일")), date_value(row.get("착공일")),
                date_value(row.get("사용승인일")), date_value(row.get("생성일")), source_id,
            ]


def load_building_register() -> None:
    sql = """
CREATE TEMP TABLE _stage_br (
    mgm_bldrgst_pk text, pnu text, sigungu_name text, lot_address text, road_address text,
    building_name text, dong_name text, register_kind text, use_code text, use_name text,
    use_detail text, use_group text, structure text, roof text, site_area_m2 numeric,
    building_area_m2 numeric, gross_floor_area_m2 numeric, coverage_pct numeric,
    floor_area_ratio_pct numeric, height_m numeric, floors_above integer, floors_below integer,
    elevators_passenger integer, elevators_emergency integer, unit_count integer,
    household_count integer, family_count integer, parking_indoor_mech integer,
    parking_outdoor_mech integer, parking_indoor_self integer, parking_outdoor_self integer,
    permit_date date, construction_start_date date, approval_date date,
    register_snapshot_date date, source_file_id bigint
);
COPY _stage_br FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO context.building_register
    (mgm_bldrgst_pk, pnu, sigungu_name, lot_address, road_address, building_name, dong_name,
     register_kind, use_code, use_name, use_detail, use_group, structure, roof, site_area_m2,
     building_area_m2, gross_floor_area_m2, coverage_pct, floor_area_ratio_pct, height_m,
     floors_above, floors_below, elevators_passenger, elevators_emergency, unit_count,
     household_count, family_count, parking_indoor_mech, parking_outdoor_mech,
     parking_indoor_self, parking_outdoor_self, permit_date, construction_start_date,
     approval_date, register_snapshot_date, source_file_id)
SELECT * FROM (
    SELECT DISTINCT ON (mgm_bldrgst_pk) * FROM _stage_br ORDER BY mgm_bldrgst_pk
) d
ON CONFLICT (mgm_bldrgst_pk) DO UPDATE SET
    pnu = EXCLUDED.pnu, road_address = EXCLUDED.road_address, use_group = EXCLUDED.use_group,
    gross_floor_area_m2 = EXCLUDED.gross_floor_area_m2, register_snapshot_date = EXCLUDED.register_snapshot_date;
"""
    print("building_register:", copy_into(sql, building_register_rows()))


def commercial_building_link_rows() -> Iterator[list[Any]]:
    paths = sorted((DATA / "건축물대장" / "api").glob("건물링크_*.csv"))
    for path in paths:
        source_id = file_id(path.relative_to(ROOT).as_posix())
        for row in csv_rows(path)[2]:
            pk = scalar(row.get("mgmBldrgstPk"))
            if not pk:
                continue
            yield [
                scalar(row.get("PNU")), pk, scalar(row.get("지번주소")), scalar(row.get("도로명주소")),
                scalar(row.get("용도군_tier1")), scalar(row.get("대장_주용도")), scalar(row.get("매칭")),
                integer(row.get("원후보수")),
                (row.get("다중후보_미해결") or "").strip().lower() in ("true", "1", "y"),
                source_id,
            ]


def load_commercial_building_link() -> None:
    sql = """
CREATE TEMP TABLE _stage_cbl (
    pnu text, mgm_bldrgst_pk text, lot_address text, road_address text, use_group_tier1 text,
    use_name_register text, match_kind text, candidate_count integer,
    multi_candidate_unresolved boolean, source_file_id bigint
);
COPY _stage_cbl FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO context.commercial_building_link
    (pnu, mgm_bldrgst_pk, lot_address, road_address, use_group_tier1, use_name_register,
     match_kind, candidate_count, multi_candidate_unresolved, source_file_id)
SELECT d.* FROM (
    SELECT DISTINCT ON (pnu, mgm_bldrgst_pk) * FROM _stage_cbl ORDER BY pnu, mgm_bldrgst_pk
) d
WHERE EXISTS (SELECT 1 FROM context.building_register r WHERE r.mgm_bldrgst_pk = d.mgm_bldrgst_pk)
ON CONFLICT (pnu, mgm_bldrgst_pk) DO UPDATE SET
    match_kind = EXCLUDED.match_kind, candidate_count = EXCLUDED.candidate_count,
    multi_candidate_unresolved = EXCLUDED.multi_candidate_unresolved;
"""
    print("commercial_building_link:", copy_into(sql, commercial_building_link_rows()))


def building_floor_use_rows() -> Iterator[list[Any]]:
    paths = sorted((DATA / "건축물대장" / "api").glob("층별용도_*.csv"))
    for path in paths:
        source_id = file_id(path.relative_to(ROOT).as_posix())
        for row in csv_rows(path)[2]:
            pk = scalar(row.get("mgmBldrgstPk"))
            grp = scalar(row.get("용도군"))
            if not pk or not grp:
                continue
            yield [
                pk, scalar(row.get("PNU")), scalar(row.get("지번주소")), scalar(row.get("도로명주소")),
                scalar(row.get("층구분")), integer(row.get("층번호")), scalar(row.get("층번호명")),
                number(row.get("층면적_㎡")), scalar(row.get("용도코드")), scalar(row.get("용도")),
                scalar(row.get("상세용도")), grp, scalar(row.get("구조")), source_id,
            ]


def load_building_floor_use() -> None:
    sql = """
CREATE TEMP TABLE _stage_bfu (
    mgm_bldrgst_pk text, pnu text, lot_address text, road_address text, floor_division text,
    floor_no integer, floor_no_label text, floor_area_m2 numeric, use_code text, use_name text,
    use_detail text, use_group text, structure text, source_file_id bigint
);
COPY _stage_bfu FROM STDIN WITH (FORMAT csv, NULL '\\N');
DELETE FROM context.building_floor_use f
USING (SELECT DISTINCT mgm_bldrgst_pk FROM _stage_bfu) s
WHERE f.mgm_bldrgst_pk = s.mgm_bldrgst_pk;
INSERT INTO context.building_floor_use
    (mgm_bldrgst_pk, pnu, lot_address, road_address, floor_division, floor_no, floor_no_label,
     floor_area_m2, use_code, use_name, use_detail, use_group, structure, source_file_id)
SELECT s.mgm_bldrgst_pk, s.pnu, s.lot_address, s.road_address, s.floor_division, s.floor_no,
       s.floor_no_label, s.floor_area_m2, s.use_code, s.use_name, s.use_detail, s.use_group,
       s.structure, s.source_file_id
FROM _stage_bfu s
WHERE EXISTS (SELECT 1 FROM context.building_register r WHERE r.mgm_bldrgst_pk = s.mgm_bldrgst_pk);
"""
    print("building_floor_use:", copy_into(sql, building_floor_use_rows()))


def refresh_commercial_building_views() -> None:
    run_sql("REFRESH MATERIALIZED VIEW context.commercial_building_area_summary;")
    print("commercial_building_area_summary refreshed")


def main() -> int:
    parser = __import__("argparse").ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("crosswalks", "anchors", "context", "plans", "building", "evidence", "all"), required=True)
    args = parser.parse_args()
    try:
        if args.phase in {"crosswalks", "all"}:
            load_crosswalks()
        if args.phase in {"anchors", "all"}:
            load_anchors()
            load_anchor_snapshot()
            load_poi()
        if args.phase in {"context", "all"}:
            load_metrics()
        if args.phase in {"context", "plans", "all"}:
            load_plans()
        if args.phase in {"building", "all"}:
            load_building_register()          # Tier2 먼저 (link·floor_use가 FK 참조)
            load_commercial_building()         # Tier1 (좌표 유일 출처)
            load_commercial_building_link()
            load_building_floor_use()
            refresh_commercial_building_views()
        if args.phase in {"context", "all"}:
            load_news()
            load_news_manifest()
            load_rent_index()
            load_naver_seasonality()
        if args.phase in {"evidence", "all"}:
            load_evidence()
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"context migration failed: {exc}", file=sys.stderr)
        return 1
    print(f"context migration phase complete: {args.phase}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
