"""상주·직장·외국인 생활인구 스냅샷을 PostgreSQL(`context.population_snapshot`)에 적재한다 (이슈 #28).

DB 드라이버 없이 `psql` 의 COPY STDIN 을 쓴다(`migrate_postgres.py` 와 동일 패턴).
접속 대상은 `.env` 의 `DATABASE_URL`.

계단식 데이터라 **as_of 파티션만** 적재한다:
- 상주인구 = 20234 (상권·행정동)
- 직장인구 = 20244 (상권·행정동)
- 외국인생활인구 = 20262 (행정동, 20263 부분분기 제외)

원천 CSV 는 수정하지 않는다. as_of 상수가 바뀌면(`recommendation/population.py`) 재실행이 필요하다.

실행:
  .venv/bin/python services/recommendation-api/scripts/ingest_population.py
  .venv/bin/python services/recommendation-api/scripts/ingest_population.py --check   # 적재 결과만 확인
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from recommendation import population as pop  # noqa: E402
from recommendation.env import load_env  # noqa: E402
from recommendation.paths import find_project_root  # noqa: E402

ROOT = find_project_root(__file__)

# (dataset, grain, csv 상대경로, 코드 컬럼, as_of 분기)
SPECS = [
    ("resident", "commercial_area", f"{pop._RESIDENT_DIR}/{pop._RESIDENT_FILES['상권']}", "상권_코드", pop.RESIDENT_AS_OF),
    ("resident", "admin_dong", f"{pop._RESIDENT_DIR}/{pop._RESIDENT_FILES['행정동']}", "행정동_코드", pop.RESIDENT_AS_OF),
    ("worker", "commercial_area", f"{pop._WORKER_DIR}/{pop._WORKER_FILES['상권']}", "상권_코드", pop.WORKER_AS_OF),
    ("worker", "admin_dong", f"{pop._WORKER_DIR}/{pop._WORKER_FILES['행정동']}", "행정동_코드", pop.WORKER_AS_OF),
    ("foreign", "admin_dong", f"{pop._FOREIGN_DIR}/외국인생활인구_행정동_분기.csv", "행정동_코드", pop.FOREIGN_LATEST_COMPLETE),
]

DDL = """
CREATE SCHEMA IF NOT EXISTS context;
CREATE TABLE IF NOT EXISTS context.population_snapshot (
    dataset      text NOT NULL,
    grain        text NOT NULL,
    spatial_code text NOT NULL,
    period       text NOT NULL,
    attributes   jsonb NOT NULL,
    source_file  text,
    ingested_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset, grain, spatial_code, period)
);
"""


def _psql_args() -> list[str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return ["psql", "--dbname", url, "-v", "ON_ERROR_STOP=1"]
    return ["psql", "-h", os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            "-p", os.environ.get("POSTGRES_PORT", "5432"),
            "-U", os.environ.get("POSTGRES_USER", os.environ.get("USER", "")),
            "-d", os.environ.get("POSTGRES_DB", "ideaton"),
            "-v", "ON_ERROR_STOP=1"]


def _psql_env() -> dict[str, str]:
    env = os.environ.copy()
    if env.get("POSTGRES_PASSWORD"):
        env["PGPASSWORD"] = env["POSTGRES_PASSWORD"]
    return env


def run_sql(sql: str) -> str:
    proc = subprocess.run([*_psql_args(), "-Atc", sql], cwd=ROOT, env=_psql_env(),
                          text=True, capture_output=True, check=False)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "psql 실패")
    return proc.stdout.strip()


def copy_into(sql: str, rows) -> int:
    proc = subprocess.Popen([*_psql_args(), "-c", sql], cwd=ROOT, env=_psql_env(),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    n = 0
    try:
        for row in rows:
            writer.writerow(["\\N" if v is None else v for v in row])
            n += 1
            if buf.tell() >= 1 << 20:
                proc.stdin.write(buf.getvalue().encode("utf-8"))
                buf.seek(0), buf.truncate(0)
        if buf.tell():
            proc.stdin.write(buf.getvalue().encode("utf-8"))
        proc.stdin.close()
    except Exception:
        proc.kill(); proc.wait(); raise
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if proc.wait():
        raise RuntimeError(err.strip() or "COPY 실패")
    return n


def _rows():
    for dataset, grain, rel, code_col, period in SPECS:
        path = ROOT / rel
        matched = 0
        for r in pop._read_csv(path):
            if r.get("기준_년분기_코드") != period:
                continue
            code = str(r.get(code_col, "")).strip()
            if not code:
                continue
            matched += 1
            yield [dataset, grain, code, period,
                   json.dumps(r, ensure_ascii=False, separators=(",", ":")), rel]
        print(f"  {dataset:9s} {grain:15s} {period}  {matched:>5} 행  ({rel})")


def check() -> int:
    out = run_sql(
        "SELECT dataset||' '||grain||' '||period||' -> '||count(*) "
        "FROM context.population_snapshot GROUP BY dataset, grain, period "
        "ORDER BY dataset, grain, period"
    )
    print(out or "(context.population_snapshot 비어 있음)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="적재 없이 현재 DB 상태만 출력")
    args = ap.parse_args()
    load_env()
    if not os.environ.get("DATABASE_URL") and not os.environ.get("POSTGRES_HOST"):
        print("FAIL: DATABASE_URL(또는 POSTGRES_*) 미설정 (.env 확인)")
        return 1
    if args.check:
        return check()

    print("DDL 적용…")
    run_sql(DDL)
    stage = """
CREATE TEMP TABLE _stage_pop (dataset text, grain text, spatial_code text, period text, attributes jsonb, source_file text);
COPY _stage_pop FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO context.population_snapshot (dataset, grain, spatial_code, period, attributes, source_file)
    SELECT DISTINCT ON (dataset, grain, spatial_code, period)
           dataset, grain, spatial_code, period, attributes, source_file
    FROM _stage_pop ORDER BY dataset, grain, spatial_code, period
ON CONFLICT (dataset, grain, spatial_code, period) DO UPDATE SET
    attributes = EXCLUDED.attributes, source_file = EXCLUDED.source_file, ingested_at = now();
"""
    print("적재…")
    total = copy_into(stage, _rows())
    print(f"\ncontext.population_snapshot: {total} 행 upsert")
    check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
