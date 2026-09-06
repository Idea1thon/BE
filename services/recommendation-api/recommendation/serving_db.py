"""서빙 파이프라인용 읽기 전용 PostgreSQL 접근 (의존성 없는 psql 서브프로세스).

``recommendation_pipeline.py --source db`` 가 사용한다. 드라이버를 설치하지 않고
``migrate_postgres.py`` 와 동일하게 ``psql`` 의 ``COPY (SELECT …) TO STDOUT`` 을 쓴다.

접속 대상은 ``.env`` 의 ``DATABASE_URL`` (없으면 ``POSTGRES_*``)다. Docker를
사용하는지 여부는 배포 환경의 접속 설정으로 결정하며, 이 모듈은 특정 포트를
가정하지 않는다.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import os
import subprocess
import threading
import time
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


def _run(args: list[str], *, stdin_text: str | None = None, timeout_s: float | None = None) -> str:
    if timeout_s is None:
        try:
            timeout_s = float(os.environ.get("SERVING_DB_TIMEOUT_SECONDS", "30"))
        except ValueError:
            timeout_s = 30.0
    timeout_s = max(0.1, min(300.0, timeout_s))
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


def ping(timeout_s: float | None = None) -> str:
    """접속 확인. timeout_s는 readiness 같은 짧은 probe에서만 오버라이드한다."""
    return _run([
        "-Atc",
        "SELECT current_database() || ' @ ' || coalesce(host(inet_server_addr()), 'local')",
    ], timeout_s=timeout_s).strip()


def _raw_query(sql: str) -> list[dict[str, str]]:
    copy_sql = f"COPY (\n{sql}\n) TO STDOUT WITH (FORMAT csv, HEADER true, NULL '')"
    out = _run(["-c", copy_sql])
    reader = csv.DictReader(io.StringIO(out))
    return [dict(row) for row in reader]


# ── Seoul-wide 읽기 프로세스 캐시 ────────────────────────────────────
# DbSource 의 질의는 대부분 서울 전체 범위이고 data_version 이 바뀌기 전까지
# 결과가 불변이다. 추천 요청마다 psql 서브프로세스를 새로 띄우는 비용이 크므로
# (SSL 핸드셰이크 포함) 결과를 프로세스 메모리에 캐시한다. 자치구를 서울 전체로
# 확대해도 요청당 DB 부하가 늘지 않게 하는 것이 목적이다.
#
#   SERVING_CACHE_DISABLED=1      캐시 완전 우회 (A/B·디버깅)
#   SERVING_CACHE_TTL_SECONDS=300 data_version 재확인 주기
_CACHE: dict[str, list[dict[str, str]]] = {}
_CACHE_ORDER: list[str] = []
_CACHE_MAX = 512
_CACHE_LOCK = threading.Lock()
_DV_STATE: dict[str, object] = {"value": None, "checked_at": 0.0}


def _cache_enabled() -> bool:
    return os.environ.get("SERVING_CACHE_DISABLED", "").strip().lower() not in {"1", "true", "yes", "on"}


def _cache_ttl() -> float:
    try:
        return max(0.0, float(os.environ.get("SERVING_CACHE_TTL_SECONDS", "300")))
    except ValueError:
        return 300.0


def _latest_data_version() -> str:
    rows = _raw_query(
        "SELECT coalesce(max(data_version), '') AS v FROM meta.dataset_run WHERE status = 'completed'"
    )
    return (rows[0].get("v") if rows else "") or ""


def _cache_scope() -> str:
    """캐시 무효화 스탬프. TTL 마다 data_version 을 재확인하고, 바뀌었으면 캐시를 비운다."""
    now = time.monotonic()
    last = float(_DV_STATE["checked_at"])  # type: ignore[arg-type]
    if _DV_STATE["value"] is None or now - last >= _cache_ttl():
        try:
            dv = _latest_data_version()
        except ServingDbError:
            dv = _DV_STATE["value"] or ""  # 조회 실패 시 기존 스탬프 유지
        if dv != _DV_STATE["value"]:
            with _CACHE_LOCK:
                _CACHE.clear()
                _CACHE_ORDER.clear()
            _DV_STATE["value"] = dv
        _DV_STATE["checked_at"] = now
    return f"{target()}::{_DV_STATE['value']}"


def clear_cache() -> None:
    """테스트·이식 직후 강제 무효화용."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE_ORDER.clear()
    _DV_STATE["value"] = None
    _DV_STATE["checked_at"] = 0.0


def query(sql: str, *, use_cache: bool = True) -> list[dict[str, str]]:
    """``COPY (<sql>) TO STDOUT CSV HEADER`` → ``list[dict[str, str]]``.

    NULL 은 빈 문자열로 나오며(원천 CSV 와 동일 의미) 파이프라인의
    ``num()``/``scalar()`` 가 결측으로 처리한다. 모든 값은 문자열이다 —
    숫자 캐스팅은 호출부가 한다.

    결과는 (접속 대상 + data_version) 스탬프 기준으로 캐시된다. 캐시 적중·미스
    모두 깊은 복사본을 돌려주므로 호출부가 반환값을 변형해도 캐시는 안전하다.
    """
    if not (use_cache and _cache_enabled()):
        return _raw_query(sql)
    key = hashlib.sha1(f"{_cache_scope()}\n{sql}".encode("utf-8")).hexdigest()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit is not None:
        return copy.deepcopy(hit)
    rows = _raw_query(sql)
    with _CACHE_LOCK:
        if key not in _CACHE:
            _CACHE[key] = rows
            _CACHE_ORDER.append(key)
            while len(_CACHE_ORDER) > _CACHE_MAX:
                _CACHE.pop(_CACHE_ORDER.pop(0), None)
    return copy.deepcopy(rows)


def data_version() -> dict[str, str]:
    """meta.dataset_run 의 최신 완료 이식 정보."""
    rows = query(
        "SELECT data_version, run_type, completed_at::text AS completed_at, notes "
        "FROM meta.dataset_run WHERE status = 'completed' "
        "ORDER BY completed_at DESC NULLS LAST LIMIT 1",
        use_cache=False,
    )
    return rows[0] if rows else {}


if __name__ == "__main__":
    print("대상:", target())
    print("연결:", ping())
    print("data_version:", data_version())
