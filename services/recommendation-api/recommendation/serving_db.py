"""서빙 파이프라인용 읽기 전용 PostgreSQL 접근 (의존성 없는 psql 서브프로세스).

``recommendation_pipeline.py --source db`` 가 사용한다. 드라이버를 설치하지 않고
``migrate_postgres.py`` 와 동일하게 ``psql`` 의 ``COPY (SELECT …) TO STDOUT`` 을 쓴다.

접속 대상은 ``.env`` 의 ``DATABASE_URL`` (없으면 ``POSTGRES_*``)다. Docker를
사용하는지 여부는 배포 환경의 접속 설정으로 결정하며, 이 모듈은 특정 포트를
가정하지 않는다.
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
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
# DbSource 의 질의는 대부분 서울 전체 범위이고 최신 dataset_run 이 바뀌기 전까지
# 결과가 불변이다. 추천 요청마다 psql 서브프로세스를 새로 띄우는 비용이 크므로
# (SSL 핸드셰이크 포함) 결과를 프로세스 메모리에 캐시한다. 자치구를 서울 전체로
# 확대해도 요청당 DB 부하가 늘지 않게 하는 것이 목적이다.
#
#   SERVING_CACHE_DISABLED=1        캐시 완전 우회 (A/B·디버깅)
#   SERVING_CACHE_TTL_SECONDS=300   (1~86400 로 클램프) 두 가지를 동시에 뜻한다:
#                                   ① 스탬프 백스톱 재확인 주기
#                                   ② 개별 엔트리의 최대 유효 시간
#
# 무효화 경로:
#  - run_pipeline 은 매 요청 describe() → data_version() 으로 최신 dataset_run 행을
#    읽고, 그 행이 곧바로 캐시 스탬프를 갱신한다(_refresh_stamp). 캐시된 팩트와
#    candidates.json 매니페스트가 같은 버전을 보장한다.
#  - 스탬프가 안 바뀌어도(부분 적재 실패 등으로 완료행이 그대로) 엔트리는 삽입 후
#    TTL 이 지나면 만료 처리되어 재조회된다 — 갱신된 DB 값이 무기한 가려지지 않게.
#  - 초기화(clear/스탬프 변경)마다 세대(_CACHE_GEN)를 올린다. 조회 시작 세대와
#    저장 시점 세대가 다르면(진행 중 조회가 초기화를 가로지른 경우) 저장하지 않는다.
# 락 순서: _DV_LOCK → _CACHE_LOCK (역순 금지).
_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}  # key -> (insert_monotonic, rows)
_CACHE_ORDER: list[str] = []
_CACHE_MAX = 512
_CACHE_GEN = 0
_CACHE_LOCK = threading.Lock()
_DV_LOCK = threading.Lock()
_DV_STATE: dict[str, object] = {"stamp": None, "checked_at": 0.0}


def _reset_cache_locked() -> None:
    """_CACHE_LOCK 을 잡은 상태에서 캐시를 비우고 세대를 올린다."""
    global _CACHE_GEN
    _CACHE.clear()
    _CACHE_ORDER.clear()
    _CACHE_GEN += 1

_STAMP_SQL = (
    "SELECT data_version, run_type, completed_at::text AS completed_at, notes "
    "FROM meta.dataset_run WHERE status = 'completed' "
    "ORDER BY completed_at DESC NULLS LAST LIMIT 1"
)


def _cache_enabled() -> bool:
    load_env()
    return os.environ.get("SERVING_CACHE_DISABLED", "").strip().lower() not in {"1", "true", "yes", "on"}


def _cache_ttl() -> float:
    load_env()
    try:
        ttl = float(os.environ.get("SERVING_CACHE_TTL_SECONDS", "300"))
    except (TypeError, ValueError):
        return 300.0
    if not math.isfinite(ttl):
        return 300.0
    return min(86400.0, max(1.0, ttl))


def _copy_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """캐시 반환용 방어 복사. COPY 결과 값은 전부 str(불변)이고 호출부는 행 dict
    또는 리스트 레벨만 변형하므로(예: _building_rows 가 키 추가) 행 단위 얕은
    복사로 충분하다 — deepcopy 는 11만행 규모에서 B1ms CPU 를 크게 먹는다."""
    return [dict(r) for r in rows]


def _latest_stamp_row() -> dict[str, str]:
    """meta.dataset_run 최신 완료 행. data_version() 과 동일 쿼리 —
    캐시 스탬프와 candidates.json 매니페스트가 어긋나지 않게 한 곳에서만 정의한다."""
    rows = _raw_query(_STAMP_SQL)
    return rows[0] if rows else {}


def _stamp_of(row: dict[str, str]) -> str:
    return "::".join((
        target(),
        row.get("data_version", ""),
        row.get("completed_at", ""),
        row.get("run_type", ""),
    ))


def _refresh_stamp(row: dict[str, str] | None = None, *, force: bool = False) -> str | None:
    """현재 캐시 스탬프를 반환하고, 최신 dataset_run 이 바뀌었으면 캐시를 비운다.

    ``row`` 를 주면(describe() 처럼 호출부가 이미 조회한 경우) 그 행으로 즉시 갱신하고,
    없으면 TTL 이 지났을 때만 새로 조회한다. 스탬프를 한 번도 잡지 못했고 조회도
    실패하면 ``None`` — 호출부는 이번 요청에 한해 캐시를 건너뛴다.
    """
    now = time.monotonic()
    with _DV_LOCK:
        cur = _DV_STATE["stamp"]
        due = force or row is not None or cur is None \
            or (now - float(_DV_STATE["checked_at"])) >= _cache_ttl()
        if not due:
            return cur  # type: ignore[return-value]
        if row is None:
            try:
                row = _latest_stamp_row()
            except ServingDbError:
                _DV_STATE["checked_at"] = now
                return cur  # type: ignore[return-value]  # 실패 시 기존 스탬프 유지
        new_stamp = _stamp_of(row)
        _DV_STATE["checked_at"] = now
        if new_stamp != cur:
            _DV_STATE["stamp"] = new_stamp
            with _CACHE_LOCK:
                _reset_cache_locked()
        return new_stamp


def clear_cache() -> None:
    """테스트·이식 직후 강제 무효화용."""
    with _DV_LOCK:
        _DV_STATE["stamp"] = None
        _DV_STATE["checked_at"] = 0.0
        with _CACHE_LOCK:
            _reset_cache_locked()


def query(sql: str, *, use_cache: bool = True) -> list[dict[str, str]]:
    """``COPY (<sql>) TO STDOUT CSV HEADER`` → ``list[dict[str, str]]``.

    NULL 은 빈 문자열로 나오며(원천 CSV 와 동일 의미) 파이프라인의
    ``num()``/``scalar()`` 가 결측으로 처리한다. 모든 값은 문자열이다 —
    숫자 캐스팅은 호출부가 한다.

    결과는 (접속 대상 + 최신 dataset_run) 스탬프 기준으로 캐시되며, 개별 엔트리는
    삽입 후 TTL 이 지나면 만료된다. 적중·미스 모두 행 단위 얕은 복사본을 돌려주므로
    호출부가 반환값을 변형해도 캐시는 안전하다. 스탬프를 확보하지 못하면 캐시 없이
    조회한다. 조회 도중 초기화가 있었으면(_CACHE_GEN 변경) 결과를 저장하지 않는다.
    """
    if not (use_cache and _cache_enabled()):
        return _raw_query(sql)
    stamp = _refresh_stamp()
    if stamp is None:
        return _raw_query(sql)
    ttl = _cache_ttl()
    key = hashlib.sha1(f"{stamp}\n{sql}".encode("utf-8")).hexdigest()
    with _CACHE_LOCK:
        gen0 = _CACHE_GEN
        entry = _CACHE.get(key)
        if entry is not None and (time.monotonic() - entry[0]) < ttl:
            hit = entry[1]
        else:
            hit = None
            if entry is not None:  # 만료 — 즉시 제거(느슨한 순서 목록은 방출 시 정리)
                _CACHE.pop(key, None)
    if hit is not None:
        return _copy_rows(hit)
    rows = _raw_query(sql)
    with _CACHE_LOCK:
        if _CACHE_GEN == gen0 and key not in _CACHE:
            _CACHE[key] = (time.monotonic(), rows)
            _CACHE_ORDER.append(key)
            while len(_CACHE_ORDER) > _CACHE_MAX:
                _CACHE.pop(_CACHE_ORDER.pop(0), None)
    return _copy_rows(rows)


def data_version() -> dict[str, str]:
    """meta.dataset_run 의 최신 완료 이식 정보. 반환 직전 이 행으로 캐시 스탬프를
    갱신한다 — run_pipeline 이 매 요청 describe() 로 호출하므로, 캐시가 매니페스트
    버전과 어긋나는 창을 '요청 도중 이식 완료' 수준으로 좁힌다(캐시 도입 전과 동일)."""
    row = _latest_stamp_row()
    _refresh_stamp(row)
    return row


if __name__ == "__main__":
    print("대상:", target())
    print("연결:", ping())
    print("data_version:", data_version())
