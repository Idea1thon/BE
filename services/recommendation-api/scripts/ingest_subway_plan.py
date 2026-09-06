"""제2차 서울 도시철도망 구축계획을 PostgreSQL(`context.subway_network_plan`)에 적재한다 (이슈 #29, F46 후속).

`data/도시철도역사/도시철도망계획_{노선,자치구}.csv` 를 CSV 행 verbatim(jsonb)으로 담는다.
`urban_plan._load_subway_plan_from_db` 가 이걸 읽어 파일 소스와 동일 코어로 조립한다.

DB 드라이버 없이 `psql` COPY STDIN(`ingest_population.py` 와 동일 패턴). 접속 = `.env` `DATABASE_URL`.
원천 CSV 는 수정하지 않는다. 계획이 갱신되면(관보 개정) 재실행.

실행:
  .venv/bin/python services/recommendation-api/scripts/ingest_subway_plan.py
  .venv/bin/python services/recommendation-api/scripts/ingest_subway_plan.py --check
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

from recommendation import urban_plan as up  # noqa: E402
from recommendation.env import load_env  # noqa: E402
from recommendation.paths import find_project_root  # noqa: E402

ROOT = find_project_root(__file__)

# (kind, csv 상대경로, 행 → key 함수)
SPECS = [
    ("line", f"{up._SUBWAY_DIR}/{up._SUBWAY_LINE_FILE}", lambda r: str(r.get("노선명", "")).strip()),
    ("sigungu", f"{up._SUBWAY_DIR}/{up._SUBWAY_SGG_FILE}", lambda r: str(r.get("자치구", "")).strip()),
]

DDL = """
CREATE SCHEMA IF NOT EXISTS context;
CREATE TABLE IF NOT EXISTS context.subway_network_plan (
    kind        text NOT NULL,   -- 'line' | 'sigungu'
    key         text NOT NULL,   -- 노선명 | 자치구명
    attributes  jsonb NOT NULL,  -- 원천 CSV 행 verbatim
    source_file text,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, key)
);
"""


def _psql_args() -> list[str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return ["psql", "--dbname", url, "-v", "ON_ERROR_STOP=1"]
    return ["psql", "-h", os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            "-p", os.environ.get("POSTGRES_PORT", "5432"),
            "-U", os.environ.get("POSTGRES_USER", os.environ.get("USER", "")),
            "-d", os.environ.get("POSTGRES_DB", "ideaton"), "-v", "ON_ERROR_STOP=1"]


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
    w = csv.writer(buf, lineterminator="\n")
    n = 0
    try:
        for row in rows:
            w.writerow(["\\N" if v is None else v for v in row])
            n += 1
        proc.stdin.write(buf.getvalue().encode("utf-8"))
        proc.stdin.close()
    except Exception:
        proc.kill(); proc.wait(); raise
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if proc.wait():
        raise RuntimeError(err.strip() or "COPY 실패")
    return n


def _rows():
    for kind, rel, keyfn in SPECS:
        matched = 0
        for r in up._read_csv(ROOT / rel):
            key = keyfn(r)
            if not key:
                continue
            matched += 1
            yield [kind, key, json.dumps(r, ensure_ascii=False, separators=(",", ":")), rel]
        print(f"  {kind:8s} {matched:>3} 행  ({rel})")


def check() -> int:
    out = run_sql("SELECT kind||' -> '||count(*) FROM context.subway_network_plan GROUP BY kind ORDER BY kind")
    print(out or "(context.subway_network_plan 비어 있음)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    load_env()
    if not os.environ.get("DATABASE_URL") and not os.environ.get("POSTGRES_HOST"):
        print("FAIL: DATABASE_URL 미설정 (.env 확인)")
        return 1
    if args.check:
        return check()
    print("DDL 적용…")
    run_sql(DDL)
    stage = """
CREATE TEMP TABLE _stage_subway (kind text, key text, attributes jsonb, source_file text);
COPY _stage_subway FROM STDIN WITH (FORMAT csv, NULL '\\N');
INSERT INTO context.subway_network_plan (kind, key, attributes, source_file)
    SELECT DISTINCT ON (kind, key) kind, key, attributes, source_file
    FROM _stage_subway ORDER BY kind, key
ON CONFLICT (kind, key) DO UPDATE SET
    attributes = EXCLUDED.attributes, source_file = EXCLUDED.source_file, ingested_at = now();
"""
    print("적재…")
    total = copy_into(stage, _rows())
    print(f"\ncontext.subway_network_plan: {total} 행 upsert")
    check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
