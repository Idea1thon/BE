"""서빙 파이프라인용 읽기 전용 PostgreSQL 접근 (의존성 없는 psql 서브프로세스).

``recommendation_pipeline.py --source db`` 가 사용한다. 드라이버를 설치하지 않고
``migrate_postgres.py`` 와 동일하게 ``psql`` 의 ``COPY (SELECT …) TO STDOUT`` 을 쓴다.

접속 대상은 ``.env`` 의 ``DATABASE_URL`` (없으면 ``POSTGRES_*``). 표준 대상은
``compose.yaml`` 의 Docker 컨테이너(``ideaton-db``, 127.0.0.1:55432)다.
"""
from __future__ import annotations

import csv
import io
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:  # 스크립트로 직접 실행될 때와 패키지 import 모두 지원
    from _env import load_env
except ImportError:  # pragma: no cover
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _env import load_env


class ServingDbError(RuntimeError):
    """DB 접속 또는 질의 실패."""


def _dsn_args() -> list[str]:
    load_env()
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return ["--dbname", url]
    return [
        "-h", os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "-p", os.environ.get("POSTGRES_PORT", "5432"),
        "-U", os.environ.get("POSTGRES_USER", os.environ.get("USER", "")),
        "-d", os.environ.get("POSTGRES_DB", "ideaton"),
    ]


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    password = env.get("POSTGRES_PASSWORD", "")
    if password:
        env["PGPASSWORD"] = password
    return env


def _run(args: list[str], *, stdin_text: str | None = None) -> str:
    proc = subprocess.run(
        ["psql", *_dsn_args(), "-v", "ON_ERROR_STOP=1", *args],
        cwd=ROOT,
        env=_subprocess_env(),
        text=True,
        input=stdin_text,
        capture_output=True,
    )
    if proc.returncode:
        raise ServingDbError(proc.stderr.strip() or "psql 명령 실패")
    return proc.stdout


def target() -> str:
    """접속 대상 설명 (로그용). DATABASE_URL 이 있으면 호스트:포트만 노출(암호 제외)."""
    load_env()
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        tail = url.split("@", 1)[-1] if "@" in url else url
        return f"DATABASE_URL(...@{tail})"
    return f"{os.environ.get('POSTGRES_HOST', '127.0.0.1')}:{os.environ.get('POSTGRES_PORT', '5432')}/{os.environ.get('POSTGRES_DB', 'ideaton')}"


def ping() -> str:
    """접속 확인. 실패 시 ServingDbError. 서버 주소(컨테이너 내부 IP 포함)를 돌려준다."""
    return _run([
        "-Atc",
        "SELECT current_database() || ' @ ' || coalesce(host(inet_server_addr()), 'local')",
    ]).strip()


def query(sql: str) -> list[dict[str, str]]:
    """``COPY (<sql>) TO STDOUT CSV HEADER`` → ``list[dict[str, str]]``.

    NULL 은 빈 문자열로 나오며(원천 CSV 와 동일 의미) 파이프라인의
    ``num()``/``scalar()`` 가 결측으로 처리한다. 모든 값은 문자열이다 —
    숫자 캐스팅은 호출부가 한다.
    """
    copy = f"COPY (\n{sql}\n) TO STDOUT WITH (FORMAT csv, HEADER true, NULL '')"
    out = _run(["-c", copy])
    reader = csv.DictReader(io.StringIO(out))
    return [dict(row) for row in reader]


def data_version() -> dict[str, str]:
    """meta.dataset_run 의 최신 완료 이식 정보."""
    rows = query(
        "SELECT data_version, run_type, completed_at::text AS completed_at, notes "
        "FROM meta.dataset_run WHERE status = 'completed' "
        "ORDER BY completed_at DESC NULLS LAST LIMIT 1"
    )
    return rows[0] if rows else {}


if __name__ == "__main__":
    print("대상:", target())
    print("연결:", ping())
    print("data_version:", data_version())
