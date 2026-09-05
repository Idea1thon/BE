"""누락된 서빙 테이블을 만들고 원천 스냅샷을 보충 적재한다.

현재 서비스가 실제로 요구하는 보완 대상은 다음과 같다.

* ``location.area_store_totals``: ``store_quarter``(대상 10개 업종)와
  분리된 전 업종 지역 배경값
* ``context.commercial_building``와 건축HUB Tier2 테이블 3종: 건축물대장 기반 seed
* ``context.anchor_snapshot``·``context.news_manifest``·``context.rent_index``·
  ``context.naver_seasonality``: DB 서빙 경량 맥락(임대료·공실률 포함)

이 스크립트는 같은 스냅샷을 다시 실행해도 중복되지 않도록 upsert하며,
원천 CSV를 수정하지 않는다. DB 접속은 recommendation.serving_db의
환경변수 해석을 재사용한다.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from recommendation import serving_db  # noqa: E402
from recommendation.env import load_env  # noqa: E402
from recommendation.paths import find_project_root  # noqa: E402


ROOT = find_project_root(__file__)
BASE_DDL_PATH = SERVICE_ROOT / "db/000_location_schema.sql"
DDL_PATH = SERVICE_ROOT / "db/001_missing_serving_tables.sql"
EN2KO = {
    "stdr_yyqu_cd": "기준_년분기_코드",
    "trdar_cd": "상권_코드",
    "relm_cd": "상권배후지_코드",
    "adstrd_cd": "행정동_코드",
    "svc_induty_cd": "서비스_업종_코드",
    "stor_co": "점포_수",
    "opbiz_stor_co": "개업_점포_수",
    "clsbiz_stor_co": "폐업_점포_수",
}


def _scalar(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _number(value: Any) -> float | None:
    text = _scalar(value)
    if not text or text in {"-", "N/A"}:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(round(number))


def _date(value: Any) -> str | None:
    text = _scalar(value)
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    elif len(text) == 7 and text[4] == "-":
        text += "-01"
    try:
        dt.date.fromisoformat(text[:10])
    except ValueError:
        return None
    return text[:10]


def _detect_encoding(path: Path) -> str:
    sample = path.read_bytes()[:200000]
    for encoding in ("utf-8-sig", "cp949", "utf-8"):
        for trim in range(4):
            try:
                sample[:-trim if trim else None].decode(encoding)
                return encoding
            except UnicodeDecodeError:
                continue
    raise RuntimeError(f"CSV 인코딩을 확인할 수 없습니다: {path}")


def _records(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding=_detect_encoding(path), newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            yield {EN2KO.get(str(key).strip(), str(key).strip()): value for key, value in row.items()}


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _run(args: list[str], *, stdin_text: str | None = None, timeout_s: float = 900) -> str:
    proc = subprocess.run(
        ["psql", *serving_db._dsn_args(), "-v", "ON_ERROR_STOP=1", *args],
        cwd=ROOT,
        env=serving_db._subprocess_env(),
        input=stdin_text,
        text=True,
        capture_output=True,
        timeout=timeout_s,
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "psql 명령 실패")
    return proc.stdout


def _run_sql(sql: str) -> str:
    return _run(["-Atc", sql]).strip()


def _copy_into(sql: str, rows: Iterable[Iterable[Any]]) -> tuple[int, str]:
    proc = subprocess.Popen(
        ["psql", *serving_db._dsn_args(), "-v", "ON_ERROR_STOP=1", "-c", sql],
        cwd=ROOT,
        env=serving_db._subprocess_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.stdin is None:
        raise RuntimeError("psql stdin을 열지 못했습니다.")
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    count = 0
    try:
        for row in rows:
            writer.writerow(["\\N" if value is None else value for value in row])
            count += 1
            if buffer.tell() >= 1024 * 1024:
                proc.stdin.write(buffer.getvalue().encode("utf-8"))
                buffer.seek(0)
                buffer.truncate(0)
        if buffer.tell():
            proc.stdin.write(buffer.getvalue().encode("utf-8"))
        proc.stdin.close()
    except Exception:
        proc.kill()
        proc.wait()
        raise
    stdout = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
    stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise RuntimeError(stderr.strip() or "psql COPY 실패")
    return count, stdout.strip()


def _file_id_map() -> dict[str, int]:
    try:
        rows = serving_db.query("SELECT relative_path, file_id FROM meta.dataset_file")
    except serving_db.ServingDbError:
        return {}
    return {row["relative_path"]: int(row["file_id"]) for row in rows if row.get("file_id")}


def _register_building_file() -> int | None:
    path = ROOT / "data/건축물대장/상가건물_서울.csv"
    if not path.is_file():
        raise RuntimeError(f"건축물대장 CSV가 없습니다: {path}")
    manifest_path = path.parent / "manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot = str((manifest.get("source") or {}).get("snapshot") or "unknown")
    row_count = int((manifest.get("counts") or {}).get("commercial_buildings") or 0)
    if not row_count:
        row_count = sum(1 for _ in _records(path))
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    header = next(_records(path), {})
    source_columns = json.dumps(list(header), ensure_ascii=False)
    relative = path.relative_to(ROOT).as_posix()
    source_name = str((manifest.get("source") or {}).get("name") or "GIS건물통합정보 파생 상가건물")
    sql = f"""
INSERT INTO meta.dataset_file
    (dataset_id, relative_path, source_name, source_type, encoding, sha256,
     file_bytes, row_count, observed_period_start, observed_period_end,
     manifest_path, source_columns)
VALUES
    ({_sql_literal('commercial_building')}, {_sql_literal(relative)}, {_sql_literal(source_name)},
     'derived', {_sql_literal(_detect_encoding(path))}, {_sql_literal(sha.hexdigest())},
     {path.stat().st_size}, {row_count}, {_sql_literal(snapshot)}, {_sql_literal(snapshot)},
     {_sql_literal('data/건축물대장/manifest.json')}, {_sql_literal(source_columns)}::jsonb)
ON CONFLICT (relative_path) DO UPDATE SET
    source_name = EXCLUDED.source_name, source_type = EXCLUDED.source_type,
    encoding = EXCLUDED.encoding, sha256 = EXCLUDED.sha256,
    file_bytes = EXCLUDED.file_bytes, row_count = EXCLUDED.row_count,
    observed_period_start = EXCLUDED.observed_period_start,
    observed_period_end = EXCLUDED.observed_period_end,
    manifest_path = EXCLUDED.manifest_path, source_columns = EXCLUDED.source_columns,
    imported_at = now()
RETURNING file_id;
"""
    result = _run_sql(sql)
    return int(result.splitlines()[0]) if result else None


def _building_api_paths(pattern: str) -> list[Path]:
    return sorted((ROOT / "data/건축물대장/api").glob(pattern))


def _register_building_api_sources() -> int:
    """Register Tier2 CSVs when the service DB is initialized on its own.

    The full migration can already have populated ``meta.dataset_file``. The
    upsert keeps that path idempotent while making the service-only loader
    preserve source lineage for its own building tables.
    """
    paths: list[Path] = []
    for pattern in ("표제부_*.csv", "건물링크_*.csv", "층별용도_*.csv"):
        paths.extend(_building_api_paths(pattern))
    registered = 0
    for path in sorted(set(paths)):
        relative = path.relative_to(ROOT).as_posix()
        sha = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                sha.update(chunk)
        header = next(_records(path), {})
        row_count = sum(1 for _ in _records(path))
        suffix = path.stem.split("_", 1)[-1]
        manifest = f"data/건축물대장/api/manifest_{suffix}.json"
        sql = f"""
INSERT INTO meta.dataset_file
    (dataset_id, relative_path, source_name, source_type, encoding, sha256,
     file_bytes, row_count, manifest_path, source_columns)
VALUES
    ({_sql_literal('building_register_tier2')}, {_sql_literal(relative)},
     {_sql_literal('국토교통부 건축HUB 건축물대장 API')}, 'official_api_snapshot',
     {_sql_literal(_detect_encoding(path))}, {_sql_literal(sha.hexdigest())},
     {path.stat().st_size}, {row_count}, {_sql_literal(manifest)},
     {_sql_literal(json.dumps(list(header), ensure_ascii=False))}::jsonb)
ON CONFLICT (relative_path) DO UPDATE SET
    sha256 = EXCLUDED.sha256, file_bytes = EXCLUDED.file_bytes,
    row_count = EXCLUDED.row_count, manifest_path = EXCLUDED.manifest_path,
    source_columns = EXCLUDED.source_columns, imported_at = now();
"""
        _run_sql(sql)
        registered += 1
    return registered


def _point_wkt(x: Any, y: Any) -> str | None:
    x_value, y_value = _number(x), _number(y)
    return f"POINT({x_value} {y_value})" if x_value is not None and y_value is not None else None


def _building_rows(source_file_id: int | None) -> Iterator[list[Any]]:
    path = ROOT / "data/건축물대장/상가건물_서울.csv"
    manifest = json.loads((path.parent / "manifest.json").read_text(encoding="utf-8"))
    snapshot = str((manifest.get("source") or {}).get("snapshot") or "unknown")
    for row in _records(path):
        building_pk = _scalar(row.get("건물관리번호"))
        use_code = _scalar(row.get("용도코드"))
        use_group = _scalar(row.get("용도군"))
        point = _point_wkt(row.get("x_5181"), row.get("y_5181"))
        if not building_pk or not use_code or not use_group or not point:
            continue
        area_code = _scalar(row.get("상권_코드"))
        join_type = _scalar(row.get("상권_결합")) or "미결합"
        if join_type not in {"내부", "근접", "미결합"}:
            join_type = "미결합"
        yield [
            building_pk, snapshot, _scalar(row.get("PNU")), _scalar(row.get("시군구코드")),
            _scalar(row.get("시군구명")), _scalar(row.get("법정동코드")), _scalar(row.get("대지위치")),
            _scalar(row.get("지번")), _scalar(row.get("지번구분")), use_code, _scalar(row.get("용도명")),
            use_group, _integer(row.get("지상층수")), _integer(row.get("지하층수")),
            _number(row.get("건축면적_㎡")), _number(row.get("연면적_㎡")), _number(row.get("건폐율_pct")),
            _number(row.get("용적률_pct")), _number(row.get("높이_m")), _scalar(row.get("구조")),
            _date(row.get("사용승인일")), _integer(row.get("건물연식_년")), _number(row.get("footprint_㎡")),
            point, _number(row.get("경도")), _number(row.get("위도")),
            f"commercial_area:{area_code}" if area_code and join_type != "미결합" else None,
            join_type, _scalar(row.get("행정동_코드")), _scalar(row.get("행정동_명")), source_file_id,
        ]


def load_commercial_buildings(source_file_id: int | None) -> int:
    sql = """
CREATE TEMP TABLE _stage_commercial_building (
    building_pk text, snapshot text, pnu text, sigungu_code text, sigungu_name text,
    legal_dong_code text, lot_address text, lot_number text, lot_kind text,
    use_code text, use_name text, use_group text, floors_above integer, floors_below integer,
    building_area_m2 numeric, gross_floor_area_m2 numeric, building_coverage_pct numeric,
    floor_area_ratio_pct numeric, height_m numeric, structure text, approval_date date,
    building_age_years integer, footprint_m2 numeric, point_wkt text, lon numeric, lat numeric,
    host_area_id text, area_join_type text, admin_dong_code text, admin_dong_name text,
    source_file_id bigint
);
COPY _stage_commercial_building FROM STDIN WITH (FORMAT csv, NULL '\\N');
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
FROM (SELECT DISTINCT ON (building_pk, snapshot) * FROM _stage_commercial_building
      ORDER BY building_pk, snapshot) d
ON CONFLICT (building_pk, snapshot) DO UPDATE SET
    pnu = EXCLUDED.pnu, sigungu_code = EXCLUDED.sigungu_code, sigungu_name = EXCLUDED.sigungu_name,
    legal_dong_code = EXCLUDED.legal_dong_code, lot_address = EXCLUDED.lot_address,
    lot_number = EXCLUDED.lot_number, lot_kind = EXCLUDED.lot_kind, use_code = EXCLUDED.use_code,
    use_name = EXCLUDED.use_name, use_group = EXCLUDED.use_group, floors_above = EXCLUDED.floors_above,
    floors_below = EXCLUDED.floors_below, building_area_m2 = EXCLUDED.building_area_m2,
    gross_floor_area_m2 = EXCLUDED.gross_floor_area_m2, building_coverage_pct = EXCLUDED.building_coverage_pct,
    floor_area_ratio_pct = EXCLUDED.floor_area_ratio_pct, height_m = EXCLUDED.height_m,
    structure = EXCLUDED.structure, approval_date = EXCLUDED.approval_date,
    building_age_years = EXCLUDED.building_age_years, footprint_m2 = EXCLUDED.footprint_m2,
    point = EXCLUDED.point, lon = EXCLUDED.lon, lat = EXCLUDED.lat, host_area_id = EXCLUDED.host_area_id,
    area_join_type = EXCLUDED.area_join_type, admin_dong_code = EXCLUDED.admin_dong_code,
    admin_dong_name = EXCLUDED.admin_dong_name, source_file_id = EXCLUDED.source_file_id;
    """
    count, _ = _copy_into(sql, _building_rows(source_file_id))
    return count


def _building_register_rows(source_ids: dict[str, int]) -> Iterator[list[Any]]:
    for path in _building_api_paths("표제부_*.csv"):
        source_id = source_ids.get(path.relative_to(ROOT).as_posix())
        for row in _records(path):
            pk = _scalar(row.get("mgmBldrgstPk"))
            if not pk:
                continue
            lot_address = _scalar(row.get("지번주소"))
            sigungu_name = ""
            if lot_address and "서울특별시 " in lot_address:
                parts = lot_address.split("서울특별시 ", 1)[1].split()
                sigungu_name = parts[0] if parts and parts[0].endswith("구") else ""
            yield [
                pk, _scalar(row.get("PNU")), sigungu_name, lot_address,
                _scalar(row.get("도로명주소")), _scalar(row.get("건물명")),
                _scalar(row.get("동명칭")), _scalar(row.get("대장종류")),
                _scalar(row.get("주용도코드")), _scalar(row.get("주용도")),
                _scalar(row.get("상세용도")), _scalar(row.get("용도군")) or "",
                _scalar(row.get("구조")), _scalar(row.get("지붕")),
                _number(row.get("대지면적_㎡")), _number(row.get("건축면적_㎡")),
                _number(row.get("연면적_㎡")), _number(row.get("건폐율_pct")),
                _number(row.get("용적률_pct")), _number(row.get("높이_m")),
                _integer(row.get("지상층수")), _integer(row.get("지하층수")),
                _integer(row.get("승용승강기")), _integer(row.get("비상용승강기")),
                _integer(row.get("호수")), _integer(row.get("세대수")),
                _integer(row.get("가구수")), _integer(row.get("옥내기계식_대수")),
                _integer(row.get("옥외기계식_대수")), _integer(row.get("옥내자주식_대수")),
                _integer(row.get("옥외자주식_대수")), _date(row.get("허가일")),
                _date(row.get("착공일")), _date(row.get("사용승인일")),
                _date(row.get("생성일")), source_id,
            ]


def load_building_register(source_ids: dict[str, int]) -> int:
    sql = """
CREATE TEMP TABLE _stage_building_register (
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
COPY _stage_building_register FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO context.building_register
    (mgm_bldrgst_pk, pnu, sigungu_name, lot_address, road_address, building_name, dong_name,
     register_kind, use_code, use_name, use_detail, use_group, structure, roof, site_area_m2,
     building_area_m2, gross_floor_area_m2, coverage_pct, floor_area_ratio_pct, height_m,
     floors_above, floors_below, elevators_passenger, elevators_emergency, unit_count,
     household_count, family_count, parking_indoor_mech, parking_outdoor_mech,
     parking_indoor_self, parking_outdoor_self, permit_date, construction_start_date,
     approval_date, register_snapshot_date, source_file_id)
SELECT * FROM (
    SELECT DISTINCT ON (mgm_bldrgst_pk) * FROM _stage_building_register
    ORDER BY mgm_bldrgst_pk
) d
ON CONFLICT (mgm_bldrgst_pk) DO UPDATE SET
    pnu = EXCLUDED.pnu, sigungu_name = EXCLUDED.sigungu_name,
    lot_address = EXCLUDED.lot_address, road_address = EXCLUDED.road_address,
    building_name = EXCLUDED.building_name, dong_name = EXCLUDED.dong_name,
    register_kind = EXCLUDED.register_kind, use_code = EXCLUDED.use_code,
    use_name = EXCLUDED.use_name, use_detail = EXCLUDED.use_detail,
    use_group = EXCLUDED.use_group, structure = EXCLUDED.structure, roof = EXCLUDED.roof,
    site_area_m2 = EXCLUDED.site_area_m2, building_area_m2 = EXCLUDED.building_area_m2,
    gross_floor_area_m2 = EXCLUDED.gross_floor_area_m2, coverage_pct = EXCLUDED.coverage_pct,
    floor_area_ratio_pct = EXCLUDED.floor_area_ratio_pct, height_m = EXCLUDED.height_m,
    floors_above = EXCLUDED.floors_above, floors_below = EXCLUDED.floors_below,
    elevators_passenger = EXCLUDED.elevators_passenger,
    elevators_emergency = EXCLUDED.elevators_emergency, unit_count = EXCLUDED.unit_count,
    household_count = EXCLUDED.household_count, family_count = EXCLUDED.family_count,
    parking_indoor_mech = EXCLUDED.parking_indoor_mech,
    parking_outdoor_mech = EXCLUDED.parking_outdoor_mech,
    parking_indoor_self = EXCLUDED.parking_indoor_self,
    parking_outdoor_self = EXCLUDED.parking_outdoor_self,
    permit_date = EXCLUDED.permit_date,
    construction_start_date = EXCLUDED.construction_start_date,
    approval_date = EXCLUDED.approval_date,
    register_snapshot_date = EXCLUDED.register_snapshot_date,
    source_file_id = EXCLUDED.source_file_id;
"""
    count, _ = _copy_into(sql, _building_register_rows(source_ids))
    return count


def _commercial_building_link_rows(source_ids: dict[str, int]) -> Iterator[list[Any]]:
    for path in _building_api_paths("건물링크_*.csv"):
        source_id = source_ids.get(path.relative_to(ROOT).as_posix())
        for row in _records(path):
            pnu = _scalar(row.get("PNU"))
            pk = _scalar(row.get("mgmBldrgstPk"))
            match_kind = _scalar(row.get("매칭"))
            if not pnu or not pk or match_kind not in {"지번", "본번"}:
                continue
            yield [
                pnu, pk, _scalar(row.get("지번주소")), _scalar(row.get("도로명주소")),
                _scalar(row.get("용도군_tier1")), _scalar(row.get("대장_주용도")),
                match_kind, _integer(row.get("원후보수")),
                str(row.get("다중후보_미해결") or "").strip().lower() in {"true", "1", "t", "y"},
                source_id,
            ]


def load_commercial_building_links(source_ids: dict[str, int]) -> int:
    sql = """
CREATE TEMP TABLE _stage_commercial_building_link (
    pnu text, mgm_bldrgst_pk text, lot_address text, road_address text,
    use_group_tier1 text, use_name_register text, match_kind text,
    candidate_count integer, multi_candidate_unresolved boolean, source_file_id bigint
);
COPY _stage_commercial_building_link FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO context.commercial_building_link
    (pnu, mgm_bldrgst_pk, lot_address, road_address, use_group_tier1, use_name_register,
     match_kind, candidate_count, multi_candidate_unresolved, source_file_id)
SELECT d.* FROM (
    SELECT DISTINCT ON (pnu, mgm_bldrgst_pk) * FROM _stage_commercial_building_link
    ORDER BY pnu, mgm_bldrgst_pk
) d
WHERE EXISTS (
    SELECT 1 FROM context.building_register r
    WHERE r.mgm_bldrgst_pk = d.mgm_bldrgst_pk
)
ON CONFLICT (pnu, mgm_bldrgst_pk) DO UPDATE SET
    lot_address = EXCLUDED.lot_address, road_address = EXCLUDED.road_address,
    use_group_tier1 = EXCLUDED.use_group_tier1, use_name_register = EXCLUDED.use_name_register,
    match_kind = EXCLUDED.match_kind, candidate_count = EXCLUDED.candidate_count,
    multi_candidate_unresolved = EXCLUDED.multi_candidate_unresolved,
    source_file_id = EXCLUDED.source_file_id;
"""
    count, _ = _copy_into(sql, _commercial_building_link_rows(source_ids))
    return count


def _building_floor_use_rows(source_ids: dict[str, int]) -> Iterator[list[Any]]:
    patterns = ("층별용도_*.csv",)
    paths = sorted({path for pattern in patterns for path in _building_api_paths(pattern)})
    for path in paths:
        source_id = source_ids.get(path.relative_to(ROOT).as_posix())
        for row in _records(path):
            pk = _scalar(row.get("mgmBldrgstPk"))
            use_group = _scalar(row.get("용도군"))
            if not pk or not use_group:
                continue
            yield [
                pk, _scalar(row.get("PNU")), _scalar(row.get("지번주소")),
                _scalar(row.get("도로명주소")), _scalar(row.get("층구분")),
                _integer(row.get("층번호")), _scalar(row.get("층번호명")),
                _number(row.get("층면적_㎡")), _scalar(row.get("용도코드")),
                _scalar(row.get("용도")), _scalar(row.get("상세용도")), use_group,
                _scalar(row.get("구조")), source_id,
            ]


def load_building_floor_use(source_ids: dict[str, int]) -> int:
    sql = """
CREATE TEMP TABLE _stage_building_floor_use (
    mgm_bldrgst_pk text, pnu text, lot_address text, road_address text,
    floor_division text, floor_no integer, floor_no_label text, floor_area_m2 numeric,
    use_code text, use_name text, use_detail text, use_group text, structure text,
    source_file_id bigint
);
COPY _stage_building_floor_use FROM STDIN WITH (FORMAT csv, NULL '\\N');
DELETE FROM context.building_floor_use f
USING (SELECT DISTINCT mgm_bldrgst_pk FROM _stage_building_floor_use) s
WHERE f.mgm_bldrgst_pk = s.mgm_bldrgst_pk;
INSERT INTO context.building_floor_use
    (mgm_bldrgst_pk, pnu, lot_address, road_address, floor_division, floor_no,
     floor_no_label, floor_area_m2, use_code, use_name, use_detail, use_group,
     structure, source_file_id)
SELECT s.mgm_bldrgst_pk, s.pnu, s.lot_address, s.road_address, s.floor_division, s.floor_no,
       s.floor_no_label, s.floor_area_m2, s.use_code, s.use_name, s.use_detail, s.use_group,
       s.structure, s.source_file_id
FROM _stage_building_floor_use s
WHERE EXISTS (
    SELECT 1 FROM context.building_register r
    WHERE r.mgm_bldrgst_pk = s.mgm_bldrgst_pk
);
"""
    count, _ = _copy_into(sql, _building_floor_use_rows(source_ids))
    return count


def _anchor_rows(source_ids: dict[str, int]) -> Iterator[list[Any]]:
    sources = [
        (ROOT / "data/도시철도역사/역사정보_서울.csv", "station"),
        (ROOT / "data/버스정류장/버스정류소_서울.csv", "bus_stop"),
        (ROOT / "data/공동주택/아파트단지_서울.csv", "apartment"),
    ]
    sources.extend((path, "kakao_poi") for path in sorted((ROOT / "data/카카오POI").rglob("*.csv")))
    for path, anchor_type in sources:
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        for sequence, row in enumerate(_records(path)):
            yield [relative, sequence, anchor_type, json.dumps(row, ensure_ascii=False), source_ids.get(relative)]


def load_anchor_snapshot(source_ids: dict[str, int]) -> int:
    sql = """
CREATE TEMP TABLE _stage_anchor_snapshot (
    source_file text, row_seq integer, anchor_type text, attributes jsonb, source_file_id bigint
);
COPY _stage_anchor_snapshot FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO context.anchor_snapshot (source_file, row_seq, anchor_type, attributes, source_file_id)
SELECT source_file, row_seq, anchor_type, attributes, source_file_id
FROM (SELECT DISTINCT ON (source_file, row_seq) * FROM _stage_anchor_snapshot
      ORDER BY source_file, row_seq) d
ON CONFLICT (source_file, row_seq) DO UPDATE SET
    anchor_type = EXCLUDED.anchor_type, attributes = EXCLUDED.attributes,
    source_file_id = EXCLUDED.source_file_id;
"""
    count, _ = _copy_into(sql, _anchor_rows(source_ids))
    return count


def load_news_manifests(source_ids: dict[str, int]) -> int:
    rows: list[list[Any]] = []
    for path in sorted((ROOT / "data/뉴스").glob("*_manifest.json")):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        source = _scalar(manifest.get("source"))
        if not source:
            continue
        rows.append([source, json.dumps(manifest, ensure_ascii=False), source_ids.get(path.relative_to(ROOT).as_posix())])
    sql = """
CREATE TEMP TABLE _stage_news_manifest (source text, manifest jsonb, source_file_id bigint);
COPY _stage_news_manifest FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO context.news_manifest (source, manifest, source_file_id)
SELECT source, manifest, source_file_id FROM _stage_news_manifest
ON CONFLICT (source) DO UPDATE SET
    manifest = EXCLUDED.manifest, source_file_id = EXCLUDED.source_file_id, ingested_at = now();
"""
    count, _ = _copy_into(sql, rows)
    return count


def load_rent_index(source_ids: dict[str, int]) -> int:
    rows: list[list[Any]] = []
    paths = (
        ROOT / "data/임대료/R-ONE_임대동향_분기.csv",
        ROOT / "data/임대료/R-ONE_공실률_분기.csv",
    )
    for path in paths:
        if not path.is_file():
            continue
        source_id = source_ids.get(path.relative_to(ROOT).as_posix())
        for row in _records(path):
            grain = _scalar(row.get("grain"))
            period = _scalar(row.get("기준_년분기_코드"))
            store_type = _scalar(row.get("상가유형"))
            indicator = _scalar(row.get("지표"))
            if grain not in {"상권", "권역", "서울전체"} or not (period and store_type and indicator):
                continue
            rows.append([
                period, store_type, indicator, grain, _scalar(row.get("R_ONE_상권")) or "",
                _scalar(row.get("권역")), _number(row.get("값")), source_id,
            ])
    sql = """
CREATE TEMP TABLE _stage_rent_index (
    period text, store_type text, indicator text, grain text, rone_area text,
    zone text, value_numeric numeric, source_file_id bigint
);
COPY _stage_rent_index FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO context.rent_index
    (period, store_type, indicator, grain, rone_area, zone, value_numeric, source_file_id)
SELECT period, store_type, indicator, grain, rone_area, zone, value_numeric, source_file_id
FROM (SELECT DISTINCT ON (period, store_type, indicator, grain, rone_area) * FROM _stage_rent_index
      ORDER BY period, store_type, indicator, grain, rone_area) d
ON CONFLICT (period, store_type, indicator, grain, rone_area) DO UPDATE SET
    zone = EXCLUDED.zone, value_numeric = EXCLUDED.value_numeric, source_file_id = EXCLUDED.source_file_id;
"""
    count, _ = _copy_into(sql, rows)
    return count


def load_naver_seasonality(source_ids: dict[str, int]) -> int:
    path = ROOT / "output/feature_validation/naver_seasonality.csv"
    rows: list[list[Any]] = []
    if path.is_file():
        source_id = source_ids.get(path.relative_to(ROOT).as_posix())
        for row in _records(path):
            grain, key = _scalar(row.get("grain")), _scalar(row.get("key"))
            if grain and key:
                rows.append([grain, key, json.dumps(row, ensure_ascii=False), source_id])
    sql = """
CREATE TEMP TABLE _stage_naver_seasonality (
    grain text, key text, attributes jsonb, source_file_id bigint
);
COPY _stage_naver_seasonality FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO context.naver_seasonality (grain, key, attributes, source_file_id)
SELECT grain, key, attributes, source_file_id FROM _stage_naver_seasonality
ON CONFLICT (grain, key) DO UPDATE SET
    attributes = EXCLUDED.attributes, source_file_id = EXCLUDED.source_file_id;
"""
    count, _ = _copy_into(sql, rows)
    return count


def _store_paths() -> list[Path]:
    return sorted((ROOT / "data/점포/2026년").glob("*.csv"))


def _store_rows(source_ids: dict[str, int]) -> Iterator[list[Any]]:
    for path in _store_paths():
        name = path.name
        unit = "admin_dong" if "행정동" in name else "hinterland" if "배후지" in name else "commercial_area"
        relative = path.relative_to(ROOT).as_posix()
        source_id = source_ids.get(relative)
        for row in _records(path):
            period = _scalar(row.get("기준_년분기_코드"))
            code_key = "행정동_코드" if unit == "admin_dong" else "상권_코드"
            code = _scalar(row.get(code_key))
            industry = _scalar(row.get("서비스_업종_코드"))
            if not period or not code or not industry:
                continue
            yield [
                period, unit, code, industry,
                _number(row.get("전체_점포_수") or row.get("점포_수")),
                _number(row.get("개업_점포_수")), _number(row.get("폐업_점포_수")), source_id,
            ]


def load_area_store_totals(source_ids: dict[str, int]) -> int:
    sql = """
CREATE TEMP TABLE _stage_area_store (
    period text, spatial_unit_type text, spatial_unit_code text, industry_code text,
    total_store_count numeric, open_store_count numeric, close_store_count numeric,
    source_file_id bigint
);
COPY _stage_area_store FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO location.area_store_totals
    (period, spatial_unit_type, spatial_unit_code, total_store_count, open_store_count,
     close_store_count, source_file_id)
SELECT period, spatial_unit_type, spatial_unit_code,
       SUM(total_store_count), SUM(open_store_count), SUM(close_store_count), MAX(source_file_id)
FROM (SELECT DISTINCT ON (period, spatial_unit_type, spatial_unit_code, industry_code) *
      FROM _stage_area_store
      ORDER BY period, spatial_unit_type, spatial_unit_code, industry_code) d
GROUP BY period, spatial_unit_type, spatial_unit_code
ON CONFLICT (period, spatial_unit_type, spatial_unit_code) DO UPDATE SET
    total_store_count = EXCLUDED.total_store_count, open_store_count = EXCLUDED.open_store_count,
    close_store_count = EXCLUDED.close_store_count, source_file_id = EXCLUDED.source_file_id;
"""
    count, _ = _copy_into(sql, _store_rows(source_ids))
    return count


def main() -> int:
    parser = __import__("argparse").ArgumentParser(description=__doc__)
    parser.add_argument("--skip-buildings", action="store_true")
    parser.add_argument("--skip-area-store-totals", action="store_true")
    args = parser.parse_args()
    # 서비스 전용 .env를 우선 사용한다. 로컬 마이그레이션 작업공간에서는
    # 루트 .env만 있는 경우가 많으므로 접속 설정이 비어 있을 때만 fallback한다.
    load_env(SERVICE_ROOT / ".env")
    if not os.getenv("DATABASE_URL") and not os.getenv("POSTGRES_HOST"):
        load_env(ROOT / ".env")
    if not BASE_DDL_PATH.is_file():
        raise RuntimeError(f"기본 스키마 파일이 없습니다: {BASE_DDL_PATH}")
    _run(["-f", str(BASE_DDL_PATH)])
    _run(["-f", str(DDL_PATH)])
    registered = _register_building_api_sources()
    if registered:
        print(f"building Tier2 source catalog: {registered} files")
    source_ids = _file_id_map()
    print(f"anchor_snapshot: source rows {load_anchor_snapshot(source_ids)}")
    print(f"news_manifest: source rows {load_news_manifests(source_ids)}")
    print(f"rent_index+vacancy: source rows {load_rent_index(source_ids)}")
    print(f"naver_seasonality: source rows {load_naver_seasonality(source_ids)}")
    if not args.skip_area_store_totals:
        loaded = load_area_store_totals(source_ids)
        print(f"area_store_totals: source rows {loaded}")
    if not args.skip_buildings:
        building_id = _register_building_file()
        loaded = load_commercial_buildings(building_id)
        print(f"commercial_building: source rows {loaded}")
        register_loaded = load_building_register(source_ids)
        print(f"building_register: source rows {register_loaded}")
        links_loaded = load_commercial_building_links(source_ids)
        print(f"commercial_building_link: source rows {links_loaded}")
        floors_loaded = load_building_floor_use(source_ids)
        print(f"building_floor_use: source rows {floors_loaded}")
        _run_sql("REFRESH MATERIALIZED VIEW context.commercial_building_area_summary;")
        print("commercial_building_area_summary: refreshed")
    version = dt.date.today().isoformat()
    _run_sql(
        "INSERT INTO meta.dataset_run (run_type, data_version, status, completed_at, notes) "
        f"VALUES ('migration', {_sql_literal(version)}, 'completed', now(), "
        f"{_sql_literal('serving table supplement: area_store_totals, anchor/context snapshots, rent/vacancy, commercial_building Tier1/Tier2')});"
    )
    print(f"dataset_run: {version}")
    print("supplement: complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
