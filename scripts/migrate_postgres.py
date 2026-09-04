"""프로젝트 데이터를 PostgreSQL + PostGIS로 단계 이식한다.

DB 드라이버를 별도 설치하지 않고 로컬 ``psql``의 COPY STDIN을 사용한다.
실행 전 ``db/001_location_schema.sql``이 적용되어 있어야 한다.

예시:
  .venv/bin/python3 scripts/migrate_postgres.py --phase metadata
  .venv/bin/python3 scripts/migrate_postgres.py --phase reference
  .venv/bin/python3 scripts/migrate_postgres.py --phase areas
  .venv/bin/python3 scripts/migrate_postgres.py --phase core --years 2026

``--phase core`` 기본값은 현재 최신 2026 파일의 점포·추정매출과 전체 유동인구다.
과거 분기까지 이식할 때는 ``--years 2021 2022 ... 2026``을 명시한다.
원천 파일은 수정하지 않는다.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DB_DDL = ROOT / "db" / "001_location_schema.sql"
TARGET_INDUSTRIES = {f"CS10000{i}" for i in range(1, 10)} | {"CS100010"}

ENG2KOR = {
    "stdr_yyqu_cd": "기준_년분기_코드",
    "trdar_se_cd": "상권_구분_코드",
    "trdar_se_cd_nm": "상권_구분_코드_명",
    "trdar_cd": "상권_코드",
    "trdar_cd_nm": "상권_코드_명",
    "relm_cd": "상권배후지_코드",
    "relm_cd_nm": "상권배후지_코드_명",
    "adstrd_cd": "행정동_코드",
    "adstrd_cd_nm": "행정동_코드_명",
    "svc_induty_cd": "서비스_업종_코드",
    "svc_induty_cd_nm": "서비스_업종_코드_명",
    "stor_co": "점포_수",
    "similr_induty_stor_co": "유사_업종_점포_수",
    "opbiz_rt": "개업_율",
    "opbiz_stor_co": "개업_점포_수",
    "clsbiz_rt": "폐업_률",
    "clsbiz_stor_co": "폐업_점포_수",
    "frc_stor_co": "프랜차이즈_점포_수",
    "thsmon_selng_amt": "당월_매출_금액",
    "thsmon_selng_co": "당월_매출_건수",
}


def load_project_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_project_env()


def psql_args() -> list[str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in {"postgresql", "postgres"} or not parsed.hostname:
            raise RuntimeError("DATABASE_URL 형식이 올바르지 않습니다")
        args = ["psql", "-h", parsed.hostname]
        if parsed.port:
            args.extend(["-p", str(parsed.port)])
        if parsed.username:
            args.extend(["-U", unquote(parsed.username)])
        if parsed.path and parsed.path != "/":
            args.extend(["-d", unquote(parsed.path.lstrip("/"))])
        return [*args, "-v", "ON_ERROR_STOP=1"]
    args = ["psql", "-h", os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            "-p", os.environ.get("POSTGRES_PORT", "5432"),
            "-U", os.environ.get("POSTGRES_USER", os.environ.get("USER", "")),
            "-d", os.environ.get("POSTGRES_DB", "ideaton"),
            "-v", "ON_ERROR_STOP=1"]
    return args


def psql_env() -> dict[str, str]:
    env = os.environ.copy()
    password = env.get("POSTGRES_PASSWORD", "")
    url = env.get("DATABASE_URL", "").strip()
    if url:
        parsed = urlparse(url)
        if parsed.password:
            env["PGPASSWORD"] = unquote(parsed.password)
        query = dict(part.split("=", 1) for part in parsed.query.split("&") if "=" in part)
        if query.get("sslmode"):
            env["PGSSLMODE"] = query["sslmode"]
    if password:
        env["PGPASSWORD"] = password
    return env


def run_sql(sql: str, *, capture: bool = True) -> str:
    try:
        timeout_s = float(os.environ.get("POSTGRES_PSQL_TIMEOUT_SECONDS", "300"))
    except ValueError:
        timeout_s = 300.0
    timeout_s = max(1.0, min(3600.0, timeout_s))
    try:
        proc = subprocess.run(
            [*psql_args(), "-Atc", sql],
            cwd=ROOT,
            env=psql_env(),
            text=True,
            capture_output=capture,
            check=False,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("psql 실행 파일을 찾을 수 없습니다") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"psql timeout ({timeout_s:g}s)") from exc
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "psql command failed")
    return proc.stdout.strip() if capture else ""


def copy_into(sql: str, rows: Iterable[Iterable[Any]]) -> str:
    """COPY STDIN으로 rows를 UTF-8 CSV로 스트리밍하고 후속 INSERT를 실행한다."""
    sql = f"BEGIN;\n{sql}\nCOMMIT;"
    try:
        timeout_s = float(os.environ.get("POSTGRES_PSQL_TIMEOUT_SECONDS", "300"))
    except ValueError:
        timeout_s = 300.0
    timeout_s = max(1.0, min(3600.0, timeout_s))
    proc = subprocess.Popen(
        [*psql_args(), "-c", sql],
        cwd=ROOT,
        env=psql_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    count = 0
    try:
        for row in rows:
            writer.writerow(["\\N" if value is None else value for value in row])
            count += 1
            if buf.tell() >= 1024 * 1024:
                proc.stdin.write(buf.getvalue().encode("utf-8"))
                buf.seek(0)
                buf.truncate(0)
        if buf.tell():
            proc.stdin.write(buf.getvalue().encode("utf-8"))
        proc.stdin.close()
    except Exception:
        proc.kill()
        proc.wait()
        raise
    try:
        rc = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.wait()
        raise RuntimeError(f"psql COPY timeout ({timeout_s:g}s)") from exc
    stdout = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
    stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if rc:
        raise RuntimeError(stderr.strip() or "psql COPY failed")
    return f"{count} rows; {stdout.strip()}".strip()


def detect_encoding(path: Path) -> str:
    raw = path.read_bytes()[:200000]
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    # Most Seoul open-data CSVs are UTF-8, while some older downloads are
    # CP949.  CP949 can decode arbitrary UTF-8 byte sequences into mojibake,
    # so UTF-8 must be attempted first; otherwise Korean headers are silently
    # misread and the corresponding rows are filtered out.
    for enc in ("utf-8", "cp949"):
        # A fixed-size prefix can end halfway through a multibyte character.
        # Retry a few bytes shorter so a valid file is not misclassified as
        # latin-1 solely because the sampling boundary split a character.
        for trim in range(4):
            sample = raw[:-trim] if trim else raw
            try:
                sample.decode(enc)
                return enc
            except UnicodeDecodeError:
                continue
    return "latin-1"


def normalized_header(header: str) -> str:
    return ENG2KOR.get(header.strip().strip('"'), header.strip().strip('"'))


def csv_rows(path: Path) -> tuple[str, list[str], Iterator[dict[str, str]]]:
    encoding = detect_encoding(path)
    handle = path.open("r", encoding=encoding, newline="")
    reader = csv.reader(handle)
    raw_header = next(reader)
    header = [normalized_header(x) for x in raw_header]

    def iterator() -> Iterator[dict[str, str]]:
        try:
            for row_number, values in enumerate(reader, 2):
                if len(values) != len(header):
                    raise ValueError(
                        f"CSV 행 길이 불일치: {path} {row_number}행 "
                        f"(헤더 {len(header)}개, 실제 {len(values)}개)"
                    )
                yield {header[i]: values[i].strip().strip('"') for i in range(len(header))}
        finally:
            handle.close()

    return encoding, header, iterator()


def scalar(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def number(value: Any) -> float | None:
    text = scalar(value)
    if text is None or text in {"-", "NA", "N/A", "null", "None"}:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def integer(value: Any) -> int | None:
    n = number(value)
    return int(n) if n is not None else None


def date_value(value: Any) -> str | None:
    text = scalar(value)
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    text = text[:10]
    try:
        dt.datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


def point_wkt(x: Any, y: Any) -> str | None:
    x_num, y_num = number(x), number(y)
    return f"POINT({x_num} {y_num})" if x_num is not None and y_num is not None else None


def source_type_for(path: Path) -> str:
    rel = path.relative_to(ROOT).as_posix()
    if rel.startswith("output/"):
        return "project_generated" if "generated_evidence" in rel else "derived"
    if rel.startswith("data/뉴스/"):
        return "observed_news_metadata"
    if rel.startswith("data/카카오POI/"):
        return "observed_poi_snapshot"
    if rel.endswith(".json") or rel.endswith(".jsonl"):
        return "official_api_snapshot"
    return "official_file"


def dataset_id_for(path: Path) -> str:
    rel = path.relative_to(ROOT)
    if rel.parts[0] == "data":
        return rel.parts[1]
    return "output"


def all_source_files() -> list[Path]:
    paths: list[Path] = []
    for base in (DATA, ROOT / "output" / "crosswalks"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.name != ".DS_Store" and path.suffix.lower() in {".csv", ".json", ".jsonl", ".shp", ".dbf", ".shx", ".prj"}:
                paths.append(path)
    return sorted(paths)


def header_columns(path: Path) -> list[str]:
    try:
        if path.suffix.lower() == ".csv":
            enc = detect_encoding(path)
            with path.open("r", encoding=enc, newline="") as handle:
                return next(csv.reader(handle))
        if path.suffix.lower() == ".jsonl":
            with path.open(encoding="utf-8-sig") as handle:
                for line in handle:
                    if line.strip():
                        return list(json.loads(line).keys())
        if path.suffix.lower() == ".json":
            obj = json.loads(path.read_text(encoding="utf-8"))
            return list(obj.keys()) if isinstance(obj, dict) else []
    except Exception:
        return []
    return []


def sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def register_metadata() -> None:
    rows = []
    for path in all_source_files():
        rel = path.relative_to(ROOT).as_posix()
        rows.append([
            dataset_id_for(path), rel, dataset_id_for(path), source_type_for(path),
            detect_encoding(path) if path.suffix.lower() in {".csv", ".json", ".jsonl"} else None,
            sha256(path), path.stat().st_size, None, None, None, None,
            json.dumps(header_columns(path), ensure_ascii=False),
        ])
    sql = """
CREATE TEMP TABLE _stage_dataset_file (
    dataset_id text, relative_path text, source_name text, source_type text,
    encoding text, sha256 text, file_bytes bigint, row_count bigint,
    observed_period_start text, observed_period_end text, retrieved_at timestamptz,
    source_columns jsonb
);
COPY _stage_dataset_file FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO meta.dataset_file
    (dataset_id, relative_path, source_name, source_type, encoding, sha256,
     file_bytes, row_count, observed_period_start, observed_period_end,
     retrieved_at, source_columns)
SELECT dataset_id, relative_path, source_name, source_type, encoding, sha256,
       file_bytes, row_count, observed_period_start, observed_period_end,
       retrieved_at, source_columns
FROM _stage_dataset_file
ON CONFLICT (relative_path) DO UPDATE SET
    sha256 = EXCLUDED.sha256,
    file_bytes = EXCLUDED.file_bytes,
    source_columns = EXCLUDED.source_columns,
    imported_at = now();
"""
    print("metadata:", copy_into(sql, rows))


def file_id(rel_path: str) -> int:
    result = run_sql("SELECT file_id FROM meta.dataset_file WHERE relative_path = " + sql_quote(rel_path) + ";")
    if not result:
        raise RuntimeError(f"meta.dataset_file not registered: {rel_path}")
    return int(result.splitlines()[0])


def sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def load_industry() -> None:
    path = DATA / "ontology" / "업종_검색키워드.json"
    obj = json.loads(path.read_text(encoding="utf-8"))
    version = str(obj.get("_갱신") or "2026-09-01")
    rows = []
    for code, item in obj.get("업종", {}).items():
        rows.append([
            code, item.get("명") or code,
            json.dumps(item.get("세부음식", []), ensure_ascii=False),
            json.dumps(item.get("검색키워드", []), ensure_ascii=False),
            json.dumps(item.get("인허가_업태", []), ensure_ascii=False),
            json.dumps(item.get("경계"), ensure_ascii=False) if item.get("경계") is not None else None,
            version,
            file_id("data/ontology/업종_검색키워드.json"),
        ])
    sql = """
CREATE TEMP TABLE _stage_industry (LIKE location.industry);
COPY _stage_industry FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO location.industry
    (industry_code, industry_name, food_terms, search_keywords,
     license_categories, boundary_note, ontology_version, source_file_id)
SELECT industry_code, industry_name, food_terms::jsonb, search_keywords::jsonb,
       license_categories::jsonb, boundary_note, ontology_version, source_file_id
FROM _stage_industry
ON CONFLICT (industry_code) DO UPDATE SET
    industry_name = EXCLUDED.industry_name,
    food_terms = EXCLUDED.food_terms,
    search_keywords = EXCLUDED.search_keywords,
    license_categories = EXCLUDED.license_categories,
    boundary_note = EXCLUDED.boundary_note,
    ontology_version = EXCLUDED.ontology_version,
    source_file_id = EXCLUDED.source_file_id;
"""
    # LIKE location.industry contains jsonb columns, while COPY receives JSON strings.
    sql = sql.replace("CREATE TEMP TABLE _stage_industry (LIKE location.industry);", """CREATE TEMP TABLE _stage_industry (
        industry_code text, industry_name text, food_terms text,
        search_keywords text, license_categories text, boundary_note text,
        ontology_version text, source_file_id bigint
    );""")
    print("industry:", copy_into(sql, rows))


def area_rows() -> Iterator[list[Any]]:
    try:
        import shapefile
        from shapely.geometry import MultiPolygon, Polygon, shape
    except ImportError as exc:
        raise RuntimeError("areas 이식에는 .venv의 pyshp와 shapely가 필요합니다") from exc

    configs = [
        ("상권", "commercial_area", "TRDAR_CD", "TRDAR_CD_N", "XCNTS_VALU", "YDNTS_VALU", "SIGNGU_CD", "SIGNGU_CD_", "ADSTRD_CD", "ADSTRD_CD_", "RELM_AR"),
        ("상권배후지", "hinterland", "ALLEY_TRDA", "ALLEY_TR_1", "XCNTS_VALU", "YDNTS_VALU", "SIGNGU_CD", "SIGNGU_CD_", "ADSTRD_CD", "ADSTRD_CD_", "RELM_AR"),
        ("행정동", "admin_dong", "ADSTRD_CD", "ADSTRD_NM", "XCNTS_VALU", "YDNTS_VALU", None, None, "ADSTRD_CD", "ADSTRD_NM", "RELM_AR"),
    ]
    for folder, unit, code_field, name_field, x_field, y_field, sg_code, sg_name, dong_code, dong_name, area_field in configs:
        shp = next((p for p in (DATA / "영역" / folder).glob("*.shp")), None)
        if shp is None:
            raise RuntimeError(f"영역 SHP 없음: {folder}")
        reader = shapefile.Reader(str(shp), encoding="utf-8")
        fields = [f[0] for f in reader.fields[1:]]
        rel_csv = f"data/영역/{folder}/서울시 상권분석서비스(영역-{folder}).csv"
        source_id = file_id(rel_csv)
        for record, shp_shape in zip(reader.records(), reader.shapes()):
            item = dict(zip(fields, record))
            code = scalar(item.get(code_field))
            name = scalar(item.get(name_field)) or code
            if not code:
                continue
            geom = shape(shp_shape.__geo_interface__)
            if isinstance(geom, Polygon):
                geom = MultiPolygon([geom])
            yield [
                f"{unit}:{code}", unit, code, name,
                scalar(item.get(sg_code)) if sg_code else None,
                scalar(item.get(sg_name)) if sg_name else None,
                scalar(item.get(dong_code)) if dong_code else None,
                scalar(item.get(dong_name)) if dong_name else None,
                number(item.get(x_field)), number(item.get(y_field)),
                geom.wkt, number(item.get(area_field)), source_id,
            ]


def load_areas() -> None:
    sql = """
CREATE TEMP TABLE _stage_area (
    area_id text, spatial_unit_type text, spatial_unit_code text,
    spatial_unit_name text, sigungu_code text, sigungu_name text,
    admin_dong_code text, admin_dong_name text, centroid_x numeric,
    centroid_y numeric, geom_wkt text, area_m2 numeric, source_file_id bigint
);
COPY _stage_area FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO location.area
    (area_id, spatial_unit_type, spatial_unit_code, spatial_unit_name,
     sigungu_code, sigungu_name, admin_dong_code, admin_dong_name,
     centroid, geom, area_m2, source_file_id)
SELECT area_id, spatial_unit_type, spatial_unit_code, spatial_unit_name,
       sigungu_code, sigungu_name, admin_dong_code, admin_dong_name,
       CASE WHEN centroid_x IS NULL OR centroid_y IS NULL THEN NULL
            ELSE ST_SetSRID(ST_MakePoint(centroid_x, centroid_y), 5181) END,
       CASE WHEN geom_wkt IS NULL THEN NULL
            ELSE ST_Multi(ST_GeomFromText(geom_wkt, 5181)) END,
       area_m2, source_file_id
FROM _stage_area
ON CONFLICT (area_id) DO UPDATE SET
    spatial_unit_name = EXCLUDED.spatial_unit_name,
    sigungu_code = EXCLUDED.sigungu_code,
    sigungu_name = EXCLUDED.sigungu_name,
    admin_dong_code = EXCLUDED.admin_dong_code,
    admin_dong_name = EXCLUDED.admin_dong_name,
    centroid = EXCLUDED.centroid,
    geom = EXCLUDED.geom,
    area_m2 = EXCLUDED.area_m2,
    source_file_id = EXCLUDED.source_file_id;
"""
    print("areas:", copy_into(sql, area_rows()))


def grain_for(path: Path) -> tuple[str, str, str]:
    # macOS 파일명에는 NFD가 섞일 수 있어 NFC로 통일한 뒤 grain을 판정한다.
    name = unicodedata.normalize("NFC", path.name)
    if "행정동" in name:
        return "admin_dong", "행정동_코드", "행정동_코드_명"
    if "상권배후지" in name:
        return "hinterland", "상권배후지_코드", "상권배후지_코드_명"
    return "commercial_area", "상권_코드", "상권_코드_명"


def latest_core_paths(years: list[int], token: str) -> list[Path]:
    paths = []
    for year in years:
        folder = DATA / token / (f"{year}년" if token == "점포" else str(year))
        if not folder.exists():
            continue
        paths.extend(sorted(folder.glob("*.csv")))
    return paths


def store_rows(paths: list[Path]) -> Iterator[list[Any]]:
    for path in paths:
        enc, header, records = csv_rows(path)
        unit, code_field, name_field = grain_for(path)
        source_id = file_id(path.relative_to(ROOT).as_posix())
        for row_number, row in enumerate(records, 2):
            industry = scalar(row.get("서비스_업종_코드"))
            if industry not in TARGET_INDUSTRIES:
                continue
            # 2026 점포 배후지 파일은 컬럼명이 상권_코드로 잘못 표기되어 있어
            # 경로로 grain을 판정하고 같은 위치의 값만 배후지 코드로 취급한다.
            actual_code = row.get(code_field) or (row.get("상권_코드") if unit == "hinterland" else None)
            actual_name = row.get(name_field) or (row.get("상권_코드_명") if unit == "hinterland" else None)
            yield [
                scalar(row.get("기준_년분기_코드")), unit, scalar(actual_code),
                scalar(actual_name), industry, scalar(row.get("서비스_업종_코드_명")),
                integer(row.get("전체_점포_수") or row.get("점포_수")),
                integer(row.get("일반_점포_수")), integer(row.get("프랜차이즈_점포_수")),
                number(row.get("개업_율")), integer(row.get("개업_점포_수")),
                number(row.get("폐업_률")), integer(row.get("폐업_점포_수")),
                source_id, row_number, str(path.parent.name),
                False, None,
            ]


def load_store(paths: list[Path]) -> None:
    sql = """
CREATE TEMP TABLE _stage_store (LIKE location.store_quarter);
COPY _stage_store FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO location.store_quarter
    SELECT DISTINCT ON (period, spatial_unit_type, spatial_unit_code, industry_code) *
    FROM _stage_store
    ORDER BY period, spatial_unit_type, spatial_unit_code, industry_code,
             (NULLIF(regexp_replace(source_schema_version, '\\D', '', 'g'), '')::int = left(period, 4)::int) DESC,
             NULLIF(regexp_replace(source_schema_version, '\\D', '', 'g'), '')::int DESC NULLS LAST,
             source_row_number DESC
ON CONFLICT (period, spatial_unit_type, spatial_unit_code, industry_code) DO UPDATE SET
    spatial_unit_name = EXCLUDED.spatial_unit_name,
    industry_name = EXCLUDED.industry_name,
    total_store_count = EXCLUDED.total_store_count,
    general_store_count = EXCLUDED.general_store_count,
    franchise_store_count = EXCLUDED.franchise_store_count,
    open_rate = EXCLUDED.open_rate,
    open_store_count = EXCLUDED.open_store_count,
    close_rate = EXCLUDED.close_rate,
    close_store_count = EXCLUDED.close_store_count,
    source_file_id = EXCLUDED.source_file_id,
    source_row_number = EXCLUDED.source_row_number,
    source_schema_version = EXCLUDED.source_schema_version,
    is_partial_latest = EXCLUDED.is_partial_latest,
    missing_reason = EXCLUDED.missing_reason;
"""
    print("store files:", len(paths), "rows:", copy_into(sql, store_rows(paths)))


def detail_json(row: dict[str, str], exclude: set[str]) -> str:
    detail = {k: v for k, v in row.items() if k not in exclude and scalar(v) is not None}
    return json.dumps(detail, ensure_ascii=False, separators=(",", ":"))


def sales_rows(paths: list[Path]) -> Iterator[list[Any]]:
    base = {"기준_년분기_코드", "상권_구분_코드", "상권_구분_코드_명", "상권_코드", "상권_코드_명",
            "상권배후지_코드", "상권배후지_코드_명", "행정동_코드", "행정동_코드_명",
            "서비스_업종_코드", "서비스_업종_코드_명", "당월_매출_금액", "당월_매출_건수",
            "주중_매출_금액", "주말_매출_금액"}
    for path in paths:
        enc, header, records = csv_rows(path)
        unit, code_field, name_field = grain_for(path)
        source_id = file_id(path.relative_to(ROOT).as_posix())
        for row_number, row in enumerate(records, 2):
            industry = scalar(row.get("서비스_업종_코드"))
            if industry not in TARGET_INDUSTRIES:
                continue
            actual_code = row.get(code_field) or (row.get("상권_코드") if unit == "hinterland" else None)
            actual_name = row.get(name_field) or (row.get("상권_코드_명") if unit == "hinterland" else None)
            yield [
                scalar(row.get("기준_년분기_코드")), unit, scalar(actual_code), scalar(actual_name),
                industry, scalar(row.get("서비스_업종_코드_명")), number(row.get("당월_매출_금액")),
                number(row.get("당월_매출_건수")), number(row.get("주중_매출_금액")),
                number(row.get("주말_매출_금액")), detail_json(row, base), source_id,
                row_number, str(path.parent.name), False, None,
            ]


def load_sales(paths: list[Path]) -> None:
    sql = """
CREATE TEMP TABLE _stage_sales (LIKE location.sales_quarter);
COPY _stage_sales FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO location.sales_quarter
    SELECT DISTINCT ON (period, spatial_unit_type, spatial_unit_code, industry_code) *
    FROM _stage_sales
    ORDER BY period, spatial_unit_type, spatial_unit_code, industry_code,
             (NULLIF(regexp_replace(source_schema_version, '\\D', '', 'g'), '')::int = left(period, 4)::int) DESC,
             NULLIF(regexp_replace(source_schema_version, '\\D', '', 'g'), '')::int DESC NULLS LAST,
             source_row_number DESC
ON CONFLICT (period, spatial_unit_type, spatial_unit_code, industry_code) DO UPDATE SET
    spatial_unit_name = EXCLUDED.spatial_unit_name,
    industry_name = EXCLUDED.industry_name,
    sales_amount = EXCLUDED.sales_amount,
    sales_count = EXCLUDED.sales_count,
    weekday_sales_amount = EXCLUDED.weekday_sales_amount,
    weekend_sales_amount = EXCLUDED.weekend_sales_amount,
    detail_metrics = EXCLUDED.detail_metrics,
    source_file_id = EXCLUDED.source_file_id,
    source_row_number = EXCLUDED.source_row_number,
    source_schema_version = EXCLUDED.source_schema_version,
    is_partial_latest = EXCLUDED.is_partial_latest,
    missing_reason = EXCLUDED.missing_reason;
"""
    print("sales files:", len(paths), "rows:", copy_into(sql, sales_rows(paths)))


def area_store_totals_rows(paths: list[Path]) -> Iterator[list[Any]]:
    """점포 원천 CSV를 업종 필터 없이 흘려보낸다. 업종 합산은 INSERT 단계에서 한다.

    연도 폴더가 같은 분기를 중복 수록하면(H1), 서빙 파이프라인의
    ``build_environment`` 가 쓰는 규칙 — ``data/점포/{분기연도}년/`` 폴더의 값 —
    과 맞추기 위해 각 행에 ``folder_year`` 를 붙이고 INSERT 에서 "분기 연도와
    같은 폴더" 를 우선한다. seq 는 그다음 tie-break(마지막 파일 우선).
    """
    seq = 0
    for path in paths:
        enc, header, records = csv_rows(path)
        unit, code_field, name_field = grain_for(path)
        source_id = file_id(path.relative_to(ROOT).as_posix())
        folder_digits = "".join(ch for ch in path.parent.name if ch.isdigit())
        folder_year = int(folder_digits) if folder_digits else 0
        for row in records:
            seq += 1
            actual_code = row.get(code_field) or (row.get("상권_코드") if unit == "hinterland" else None)
            period = scalar(row.get("기준_년분기_코드"))
            code = scalar(actual_code)
            industry = scalar(row.get("서비스_업종_코드"))
            if not period or not code or not industry:
                continue
            yield [
                period, unit, code, industry,
                integer(row.get("전체_점포_수") or row.get("점포_수")),
                integer(row.get("개업_점포_수")),
                integer(row.get("폐업_점포_수")),
                source_id, folder_year, seq,
            ]


def load_area_store_totals(paths: list[Path]) -> None:
    sql = """
CREATE TEMP TABLE _stage_area_store (
    period text, spatial_unit_type text, spatial_unit_code text, industry_code text,
    total_store_count numeric, open_store_count numeric, close_store_count numeric,
    source_file_id bigint, folder_year int, seq bigint
);
COPY _stage_area_store FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO location.area_store_totals
    SELECT period, spatial_unit_type, spatial_unit_code,
           SUM(total_store_count), SUM(open_store_count), SUM(close_store_count),
           MAX(source_file_id)
    FROM (
        SELECT DISTINCT ON (period, spatial_unit_type, spatial_unit_code, industry_code)
               period, spatial_unit_type, spatial_unit_code, industry_code,
               total_store_count, open_store_count, close_store_count, source_file_id
        FROM _stage_area_store
        ORDER BY period, spatial_unit_type, spatial_unit_code, industry_code,
                 (folder_year = substr(period, 1, 4)::int) DESC, folder_year DESC, seq DESC
    ) deduped
    GROUP BY period, spatial_unit_type, spatial_unit_code
ON CONFLICT (period, spatial_unit_type, spatial_unit_code) DO UPDATE SET
    total_store_count = EXCLUDED.total_store_count,
    open_store_count = EXCLUDED.open_store_count,
    close_store_count = EXCLUDED.close_store_count,
    source_file_id = EXCLUDED.source_file_id;
"""
    print("area_store_totals:", copy_into(sql, area_store_totals_rows(paths)))


def flow_rows() -> Iterator[list[Any]]:
    paths = sorted((DATA / "길단위인구").glob("*.csv"))
    base = {"기준_년분기_코드", "상권_구분_코드", "상권_구분_코드_명", "상권_코드", "상권_코드_명",
            "상권배후지_코드", "상권배후지_코드_명", "행정동_코드", "행정동_코드_명", "총_유동인구_수"}
    for path in paths:
        enc, header, records = csv_rows(path)
        unit, code_field, name_field = grain_for(path)
        source_id = file_id(path.relative_to(ROOT).as_posix())
        for row_number, row in enumerate(records, 2):
            yield [
                scalar(row.get("기준_년분기_코드")), unit, scalar(row.get(code_field)), scalar(row.get(name_field)),
                number(row.get("총_유동인구_수")), detail_json(row, base), source_id, row_number, False, None,
            ]


def load_flow() -> None:
    sql = """
CREATE TEMP TABLE _stage_flow (LIKE location.flow_quarter);
COPY _stage_flow FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO location.flow_quarter
    SELECT DISTINCT ON (period, spatial_unit_type, spatial_unit_code) *
    FROM _stage_flow
    ORDER BY period, spatial_unit_type, spatial_unit_code, source_row_number DESC
ON CONFLICT (period, spatial_unit_type, spatial_unit_code) DO UPDATE SET
    spatial_unit_name = EXCLUDED.spatial_unit_name,
    flow_total = EXCLUDED.flow_total,
    detail_metrics = EXCLUDED.detail_metrics,
    source_file_id = EXCLUDED.source_file_id,
    source_row_number = EXCLUDED.source_row_number,
    is_partial_latest = EXCLUDED.is_partial_latest,
    missing_reason = EXCLUDED.missing_reason;
"""
    print("flow:", copy_into(sql, flow_rows()))


def permitted_establishment_rows() -> Iterator[list[Any]]:
    path = DATA / "인허가" / "음식점_인허가_서울.csv"
    if not path.is_file():
        return
    _, _, records = csv_rows(path)
    source_id = file_id(path.relative_to(ROOT).as_posix())
    base = {"원천", "관리번호", "업태구분명", "업종코드", "인허가일자", "폐업일자",
            "영업상태명", "인허가_분기", "폐업_분기", "자치구", "좌표X_5181", "좌표Y_5181",
            "상권_코드", "상권_명"}
    for row in records:
        raw_industry = scalar(row.get("업종코드"))
        yield [
            scalar(row.get("관리번호")), raw_industry if raw_industry in TARGET_INDUSTRIES else None,
            scalar(row.get("업태구분명")), date_value(row.get("인허가일자")), date_value(row.get("폐업일자")),
            scalar(row.get("영업상태명")), scalar(row.get("자치구")), scalar(row.get("상권_코드")),
            scalar(row.get("상권_명")), point_wkt(row.get("좌표X_5181"), row.get("좌표Y_5181")),
            number(row.get("좌표X_5181")), number(row.get("좌표Y_5181")), "official_api_snapshot", source_id,
            json.dumps({k: v for k, v in row.items() if k not in base and scalar(v) is not None}, ensure_ascii=False,
                       separators=(",", ":")),
        ]


def load_permitted_establishments() -> None:
    sql = """
CREATE TEMP TABLE _stage_permitted_establishment (
    permit_id text, industry_code text, business_type text, licensed_at date,
    closed_at date, status text, sigungu_name text, spatial_unit_code text,
    spatial_unit_name text, point_wkt text, x_5181 numeric, y_5181 numeric,
    source_type text, source_file_id bigint, source_attributes jsonb
);
COPY _stage_permitted_establishment FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO location.permitted_establishment
    (permit_id, industry_code, business_type, licensed_at, closed_at, status,
     sigungu_name, spatial_unit_code, spatial_unit_name, point, x_5181, y_5181,
     source_type, source_file_id, source_attributes)
SELECT permit_id, industry_code, business_type, licensed_at, closed_at, status,
       sigungu_name, spatial_unit_code, spatial_unit_name,
       CASE WHEN point_wkt IS NULL THEN NULL ELSE ST_GeomFromText(point_wkt, 5181) END,
       x_5181, y_5181, source_type, source_file_id, source_attributes
FROM _stage_permitted_establishment
ON CONFLICT (permit_id) DO UPDATE SET
    industry_code = EXCLUDED.industry_code,
    business_type = EXCLUDED.business_type,
    licensed_at = EXCLUDED.licensed_at,
    closed_at = EXCLUDED.closed_at,
    status = EXCLUDED.status,
    sigungu_name = EXCLUDED.sigungu_name,
    spatial_unit_code = EXCLUDED.spatial_unit_code,
    spatial_unit_name = EXCLUDED.spatial_unit_name,
    point = EXCLUDED.point,
    x_5181 = EXCLUDED.x_5181,
    y_5181 = EXCLUDED.y_5181,
    source_type = EXCLUDED.source_type,
    source_file_id = EXCLUDED.source_file_id,
    source_attributes = EXCLUDED.source_attributes;
"""
    print("permitted establishments:", copy_into(sql, permitted_establishment_rows()))


def permit_panel_rows() -> Iterator[list[Any]]:
    path = DATA / "인허가" / "음식점_상권분기_패널.csv"
    if not path.is_file():
        return
    _, _, records = csv_rows(path)
    source_id = file_id(path.relative_to(ROOT).as_posix())
    for row in records:
        industry = scalar(row.get("업종코드"))
        if industry not in TARGET_INDUSTRIES:
            continue
        yield [
            scalar(row.get("기준_년분기_코드")), "commercial_area", scalar(row.get("상권_코드")),
            industry, integer(row.get("영업중_수")), integer(row.get("신규개업_수")),
            integer(row.get("폐업_수")), number(row.get("개업률")), number(row.get("폐업률")),
            scalar(row.get("분기_상태")), "derived", source_id,
            json.dumps({"source_definition": "공공 인허가 원본에서 산출한 상권×업종×분기 패널"}, ensure_ascii=False),
        ]


def load_permit_panel() -> None:
    sql = """
CREATE TEMP TABLE _stage_permit_quarter (
    period text, spatial_unit_type text, spatial_unit_code text, industry_code text,
    operating_count integer, new_count integer, closed_count integer,
    open_rate numeric, close_rate numeric, quarter_status text, source_type text,
    source_file_id bigint, source_attributes jsonb
);
COPY _stage_permit_quarter FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO location.permit_quarter
    (period, spatial_unit_type, spatial_unit_code, industry_code, operating_count,
     new_count, closed_count, open_rate, close_rate, quarter_status, source_type,
     source_file_id, source_attributes)
SELECT period, spatial_unit_type, spatial_unit_code, industry_code, operating_count,
       new_count, closed_count, open_rate, close_rate, quarter_status, source_type,
       source_file_id, source_attributes
FROM _stage_permit_quarter
ON CONFLICT (period, spatial_unit_type, spatial_unit_code, industry_code) DO UPDATE SET
    operating_count = EXCLUDED.operating_count,
    new_count = EXCLUDED.new_count,
    closed_count = EXCLUDED.closed_count,
    open_rate = EXCLUDED.open_rate,
    close_rate = EXCLUDED.close_rate,
    quarter_status = EXCLUDED.quarter_status,
    source_type = EXCLUDED.source_type,
    source_file_id = EXCLUDED.source_file_id,
    source_attributes = EXCLUDED.source_attributes;
"""
    print("permit panel:", copy_into(sql, permit_panel_rows()))


def load_core(years: list[int]) -> None:
    store_paths = latest_core_paths(years, "점포")
    sales_paths = latest_core_paths(years, "추정매출")
    if not store_paths or not sales_paths:
        raise RuntimeError("점포/추정매출 원천 파일을 찾지 못했습니다")
    load_store(store_paths)
    load_area_store_totals(store_paths)
    load_sales(sales_paths)
    load_flow()
    load_permitted_establishments()
    load_permit_panel()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("metadata", "reference", "areas", "core", "all"), required=True)
    parser.add_argument("--years", nargs="+", type=int, default=[2026], help="core 이식 연도. 기본 2026")
    args = parser.parse_args()

    if not DB_DDL.is_file():
        raise SystemExit(f"DDL 없음: {DB_DDL}")
    try:
        if args.phase in {"metadata", "all"}:
            register_metadata()
        if args.phase in {"reference", "all"}:
            load_industry()
        if args.phase in {"areas", "all"}:
            load_areas()
        if args.phase in {"core", "all"}:
            load_core(sorted(set(args.years)))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"migration failed: {exc}", file=sys.stderr)
        return 1
    print(f"migration phase complete: {args.phase}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
