"""서빙 파이프라인용 읽기 전용 PostgreSQL 접근 (의존성 없는 psql 서브프로세스).

``recommendation_pipeline.py --source db`` 가 사용한다. 드라이버를 설치하지 않고
``migrate_postgres.py`` 와 동일하게 ``psql`` 의 ``COPY (SELECT …) TO STDOUT`` 을 쓴다.

접속 대상은 ``.env`` 의 ``DATABASE_URL`` (없으면 ``POSTGRES_*``)다. FastAPI나
CLI를 EC2 호스트에서 실행할 때는 Docker DB 공개 주소인 ``127.0.0.1:55432``를
사용한다.
"""
from __future__ import annotations

import csv
import io
import os
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

from .env import load_env
from .paths import find_project_root

ROOT = find_project_root(__file__)


class ServingDbError(RuntimeError):
    """DB 접속 또는 질의 실패."""


def _dsn_args() -> list[str]:
    load_env()
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in {"postgresql", "postgres"} or not parsed.hostname:
            raise ServingDbError("DATABASE_URL 형식이 올바르지 않습니다.")
        args: list[str] = []
        if parsed.hostname:
            args.extend(["-h", parsed.hostname])
        if parsed.port:
            args.extend(["-p", str(parsed.port)])
        if parsed.username:
            args.extend(["-U", unquote(parsed.username)])
        if parsed.path and parsed.path != "/":
            args.extend(["-d", unquote(parsed.path.lstrip("/"))])
        return args
    return [
        "-h", os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "-p", os.environ.get("POSTGRES_PORT", "5432"),
        "-U", os.environ.get("POSTGRES_USER", os.environ.get("USER", "")),
        "-d", os.environ.get("POSTGRES_DB", "ideaton"),
    ]


def _subprocess_env() -> dict[str, str]:
    load_env()
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


def _run(args: list[str], *, stdin_text: str | None = None) -> str:
    try:
        timeout_s = float(os.environ.get("SERVING_DB_TIMEOUT_SECONDS", "30"))
    except ValueError:
        timeout_s = 30.0
    timeout_s = max(1.0, min(300.0, timeout_s))
    try:
        proc = subprocess.run(
            ["psql", *_dsn_args(), "-v", "ON_ERROR_STOP=1", *args],
            cwd=ROOT,
            env=_subprocess_env(),
            text=True,
            input=stdin_text,
            capture_output=True,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise ServingDbError("psql 실행 파일을 찾을 수 없습니다.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ServingDbError(f"psql timeout ({timeout_s:g}s)") from exc
    if proc.returncode:
        raise ServingDbError(proc.stderr.strip() or "psql 명령 실패")
    return proc.stdout


def target() -> str:
    """접속 대상 설명 (로그용). DATABASE_URL 이 있으면 호스트:포트만 노출(암호 제외)."""
    load_env()
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        parsed = urlparse(url)
        host = parsed.hostname or "?"
        port = parsed.port or 5432
        database = parsed.path.lstrip("/") or "?"
        return f"DATABASE_URL({host}:{port}/{database})"
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
