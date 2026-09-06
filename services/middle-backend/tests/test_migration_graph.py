"""마이그레이션 그래프와 부분 적용 DB 복구 테스트.

두 가지를 잡는다.

1. **head 가 다시 갈라지는 것.** `0005_siren_operational_storage` 와
   `0005_seoul_all_regions` 가 같은 부모를 잡은 채 각각 병합되어 head 가 2개가 됐고,
   `alembic upgrade head` 가 실행조차 되지 않아 컨테이너 기동과 테스트 수집이 모두
   죽었다. 그래프 검사는 DB 없이 즉시 실패하므로 회귀를 빨리 잡는다.

2. **이미 스키마가 있는 DB 위에서 `upgrade` 가 죽는 것.** 운영 DB 를 `fmp` 에서
   `ideaton` 으로 옮겼을 때 `ideaton` 에는 `alembic_version` 이 없고 스키마 일부가
   이미 있었다. 그 상태를 실제 PostgreSQL 에 재현해서 `upgrade head` 가 성공하고
   **기존 행이 남아 있는지** 확인한다. 카탈로그만 보는 검사로는 이걸 잡을 수 없다.

여기서 쓰는 DB 는 `conftest` 의 `fmp_test` 와 별개다. 같은 DB 를 쓰면
`alembic_version` 을 지우는 조작이 다른 테스트를 오염시킨다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 이 모듈은 대상 DB 를 DROP/CREATE 한다. conftest 와 같은 안전장치를 둔다.
BASELINE_DB = "fmp_baseline_test"
PRUNE_DB = "fmp_prune_test"
SERIAL_DB = "fmp_serial_test"

# 0001 이 BIGSERIAL 로 만든 컬럼. `0007_repair_serial_defaults` 와 같은 목록이어야
# 한다 — 한쪽에만 추가하면 자동생성이 없는 테이블이 조용히 생긴다.
SERIAL_COLUMNS = (
    ("franchise", "id"),
    ("user_account", "id"),
    ("branch", "id"),
    ("operation_report", "id"),
    ("notification", "id"),
    ("financial_product", "id"),
    ("refresh_token", "id"),
)


def _script_directory() -> ScriptDirectory:
    # script_location 이 상대 경로("alembic")라 CWD 에 따라 달라진다. pytest 를 어디서
    # 부르든 같은 곳을 보도록 절대 경로로 덮어쓴다.
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


# --------------------------------------------------------------------------- #
# 그래프 검사 — DB 불필요
# --------------------------------------------------------------------------- #


def test_migration_graph_has_exactly_one_head():
    """head 가 하나여야 `alembic upgrade head` 가 실행된다.

    2개가 되면 alembic 은 DDL 을 시도하지도 않고
    "Multiple head revisions are present" 로 끝난다. 새 리비전을 만들 때 부모를
    현재 head 로 잡지 않으면 여기서 걸린다.
    """
    heads = _script_directory().get_heads()
    assert len(heads) == 1, (
        f"head 가 {len(heads)}개다: {heads}. "
        "가장 최근 head 를 down_revision 으로 잡거나 merge 리비전을 추가할 것."
    )


def test_every_revision_reaches_the_single_base():
    """모든 리비전이 하나의 base 에서 출발해 head 까지 이어진다.

    고아 리비전이나 base 가 둘인 상태는 `upgrade head` 가 일부 리비전을 조용히
    건너뛰게 만든다.
    """
    script = _script_directory()
    revisions = list(script.walk_revisions())
    assert list(script.get_bases()) == ["0001_initial"], (
        f"base 가 예상과 다르다: {script.get_bases()}"
    )
    # walk_revisions 는 head 에서 base 까지 도달 가능한 리비전만 돌려준다. 파일
    # 개수와 다르면 그래프에서 떨어진 리비전이 있다는 뜻이다.
    files = list((PROJECT_ROOT / "alembic" / "versions").glob("[0-9]*.py"))
    assert len(revisions) == len(files), (
        f"도달 가능 리비전 {len(revisions)}개, 파일 {len(files)}개 — "
        "그래프에서 떨어진 리비전이 있다."
    )


def test_merge_revision_declares_both_branches():
    """merge 리비전이 두 갈래를 모두 부모로 잡는다.

    한쪽만 남기면 다른 갈래가 그래프에서 떨어지고, 그 갈래의 DDL 이 실행되지 않는다.
    """
    merge = _script_directory().get_revision("0006_merge_siren_and_regions")
    assert set(merge.down_revision or ()) == {
        "0005_siren_operational_storage",
        "0005_seoul_all_regions",
    }


def test_revision_ids_fit_the_version_table_column():
    """리비전 id 가 32자를 넘지 않는다.

    `alembic_version.version_num` 은 alembic 기본값인 VARCHAR(32) 다. 더 긴 id 를
    쓰면 DDL 은 전부 성공한 뒤 마지막 이력 UPDATE 에서
    StringDataRightTruncationError 로 죽어, 원인이 DDL 처럼 보인다.
    """
    too_long = {
        r.revision: len(r.revision)
        for r in _script_directory().walk_revisions()
        if len(r.revision) > 32
    }
    assert not too_long, f"32자를 넘는 리비전 id: {too_long}"


# --------------------------------------------------------------------------- #
# 실제 PostgreSQL 이 필요한 검사
# --------------------------------------------------------------------------- #


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    done = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if done.returncode != 0:
        raise RuntimeError(
            f"{' '.join(cmd)} 실패 (exit {done.returncode})\n"
            f"--- stdout ---\n{done.stdout}\n--- stderr ---\n{done.stderr}"
        )
    return done


def _psql(db: str, sql: str) -> str:
    return _run(["psql", "-tAq", "-v", "ON_ERROR_STOP=1", "-d", db, "-c", sql]).stdout.strip()


def _alembic(db: str, *args: str) -> None:
    url = f"postgresql+asyncpg://localhost:5432/{db}"
    _run(
        [sys.executable, "-m", "alembic", *args],
        cwd=PROJECT_ROOT,
        env={**os.environ, "DATABASE_URL": url},
    )


@pytest.fixture
def scratch_db(request):
    """이름이 `_test` 로 끝나는 빈 DB 를 만들고 끝나면 지운다."""
    name = request.param
    assert name.endswith("_test"), f"테스트 DB 이름은 '_test' 로 끝나야 한다: {name!r}"
    _psql("postgres", f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    _psql("postgres", f"CREATE DATABASE {name}")
    try:
        yield name
    finally:
        _psql("postgres", f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


# 점포 한 곳을 만드는 최소 행 집합. region 코드는 인자로 받는다 — 0005 의
# prune 대상(구버전 10자리)과 유지 대상(8자리)을 같은 헬퍼로 시험한다.
def _seed_branch(db: str, *, region_code: str, region_level: str, parent_code: str) -> None:
    _psql(
        db,
        "INSERT INTO region (code, parent_code, level, name) "
        "VALUES ('11', NULL, 'SIDO', '서울특별시') ON CONFLICT (code) DO NOTHING;"
        f"INSERT INTO region (code, parent_code, level, name) "
        f"VALUES ('{parent_code}', '11', 'SIGUNGU', '테스트구') ON CONFLICT (code) DO NOTHING;"
        f"INSERT INTO region (code, parent_code, level, name) "
        f"VALUES ('{region_code}', '{parent_code}', '{region_level}', '테스트동') "
        "ON CONFLICT (code) DO NOTHING;"
        "INSERT INTO business_category (code, name) VALUES ('CS100001', '한식') "
        "ON CONFLICT (code) DO NOTHING;"
        "INSERT INTO franchise (id, name) VALUES (9001, '테스트본사') "
        "ON CONFLICT (id) DO NOTHING;"
        "INSERT INTO user_account (id, franchise_id, email, password_hash, user_type, name) "
        "VALUES (9001, 9001, 'keep@example.com', 'x', 'OWNER', '보존점주') "
        "ON CONFLICT (id) DO NOTHING;"
        "INSERT INTO branch (id, franchise_id, owner_user_id, name, address, "
        "region_code, business_category_code) "
        f"VALUES (9001, 9001, 9001, '보존점포', '주소', '{region_code}', 'CS100001') "
        "ON CONFLICT (id) DO NOTHING;"
        "INSERT INTO operation_report (id, branch_id, report_month, net_sales) "
        "VALUES (9001, 9001, DATE '2026-08-01', 1234) ON CONFLICT (id) DO NOTHING;",
    )


@pytest.mark.parametrize("scratch_db", [BASELINE_DB], indirect=True)
def test_upgrade_head_recovers_a_partially_applied_database(scratch_db):
    """`alembic_version` 이 없고 스키마가 이미 있는 DB 에서도 upgrade 가 끝난다.

    Azure `ideaton` 의 실제 상태를 재현한다 — 스키마는 있고, 이력 테이블은 없고,
    `region` 은 SIGUNGU 8행 / DONG 0행. 이전 코드에서는 0001 의 `CREATE TABLE` 이
    DuplicateTable 로 죽었다.

    기존 행이 그대로 남는지도 함께 본다. 마이그레이션이 스키마를 맞추려고 데이터를
    지우면 안 된다.
    """
    db = scratch_db
    _alembic(db, "upgrade", "head")
    _seed_branch(db, region_code="11680510", region_level="DONG", parent_code="11680")

    # ── Azure 상태 재현: 이력 테이블 제거 + region 축소
    _psql(db, "DROP TABLE alembic_version")
    _psql(db, "DELETE FROM region WHERE level = 'DONG' AND code <> '11680510'")
    _psql(
        db,
        "DELETE FROM region WHERE level = 'SIGUNGU' AND code NOT IN "
        "(SELECT code FROM region WHERE level = 'SIGUNGU' ORDER BY code LIMIT 8) "
        "AND code NOT IN (SELECT DISTINCT parent_code FROM region WHERE parent_code IS NOT NULL)",
    )
    assert _psql(db, "SELECT count(*) FROM pg_tables WHERE tablename = 'alembic_version'") == "0"
    before_sigungu = int(_psql(db, "SELECT count(*) FROM region WHERE level = 'SIGUNGU'"))
    assert before_sigungu <= 9, f"재현 상태가 예상과 다르다: SIGUNGU {before_sigungu}행"

    # ── 여기가 이전에 죽던 지점
    _alembic(db, "upgrade", "head")

    assert _psql(db, "SELECT version_num FROM alembic_version") == (
        "0007_repair_serial_defaults"
    )
    assert _psql(db, "SELECT count(*) FROM region WHERE level = 'SIGUNGU'") == "25"
    assert _psql(db, "SELECT count(*) FROM region WHERE level = 'DONG'") == "425"

    # 기존 데이터 보존
    assert _psql(db, "SELECT name FROM franchise WHERE id = 9001") == "테스트본사"
    assert _psql(db, "SELECT name FROM branch WHERE id = 9001") == "보존점포"
    assert _psql(db, "SELECT net_sales FROM operation_report WHERE id = 9001") == "1234"


@pytest.mark.parametrize("scratch_db", [PRUNE_DB], indirect=True)
def test_region_prune_keeps_legacy_codes_referenced_by_branch(scratch_db):
    """0005 의 DONG 정리가 참조된 코드를 지우지 않고 마이그레이션도 죽지 않는다.

    `branch.region_code` FK 에는 ON DELETE 절이 없다(0001). 참조된 행을 지우려 하면
    FK 위반으로 마이그레이션 전체가 실패하고, CASCADE 였다면 점포가 조용히 사라진다.
    참조가 없는 구버전 코드는 계속 정리되는지도 같이 확인한다.
    """
    db = scratch_db
    _alembic(db, "upgrade", "0004_siren_integration")
    # 구버전 10자리 코드 두 개: 하나는 점포가 참조, 하나는 미참조.
    _seed_branch(db, region_code="1168010100", region_level="DONG", parent_code="11680")
    _psql(
        db,
        "INSERT INTO region (code, parent_code, level, name) "
        "VALUES ('1168010200', '11680', 'DONG', '미참조동')",
    )

    _alembic(db, "upgrade", "head")

    assert _psql(db, "SELECT count(*) FROM region WHERE code = '1168010100'") == "1", (
        "점포가 참조하는 구버전 행정동 코드가 삭제됐다"
    )
    assert _psql(db, "SELECT count(*) FROM branch WHERE id = 9001") == "1"
    assert _psql(db, "SELECT count(*) FROM region WHERE code = '1168010200'") == "0", (
        "참조가 없는 구버전 코드는 정리돼야 한다"
    )
    assert _psql(db, "SELECT count(*) FROM region WHERE level = 'DONG'") == "426", (
        "425개 신규 코드 + 참조로 보존된 1개"
    )


def test_serial_column_list_matches_the_repair_revision():
    """복구 리비전의 BIGSERIAL 목록이 이 테스트의 기대와 같다.

    한쪽에만 컬럼을 추가하면 자동생성이 빠진 테이블을 아무도 확인하지 않게 된다.
    """
    import importlib.util

    path = (
        PROJECT_ROOT / "alembic" / "versions" / "0007_repair_serial_defaults.py"
    )
    spec = importlib.util.spec_from_file_location("repair_serial", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(module.SERIAL_COLUMNS) == set(SERIAL_COLUMNS)


@pytest.mark.parametrize("scratch_db", [SERIAL_DB], indirect=True)
def test_upgrade_head_restores_pk_autogeneration_and_sequence_position(scratch_db):
    """`franchise.id` 처럼 자동생성이 사라진 컬럼을 복구하고 기존 값과 충돌하지 않는다.

    Azure 에서 실제로 난 오류를 재현한다.

        NotNullViolationError: null value in column "id" of relation "franchise"

    0001 은 테이블이 있으면 `CREATE TABLE` 을 건너뛰고, 컬럼도 있으니
    `ADD COLUMN IF NOT EXISTS` 에도 걸리지 않는다. `DEFAULT nextval(...)` 과 시퀀스만
    없는 상태가 그대로 남아 애플리케이션 INSERT 가 실패한다.

    시퀀스 위치도 같이 본다. 기존 행이 큰 id 를 쓰고 있으면 시퀀스를 1 에서 시작하게
    두면 안 된다 — 자동생성을 고친 직후 중복 키로 죽는다.
    """
    db = scratch_db
    _alembic(db, "upgrade", "head")

    # 덤프 복원처럼 id 를 명시해서 넣은 기존 행. 시퀀스는 이 값을 모른다.
    _psql(db, "INSERT INTO franchise (id, name) VALUES (5000, '기존본사')")
    _psql(
        db,
        "INSERT INTO franchise (id, name) VALUES (7, '작은본사');"
        "INSERT INTO user_account (id, franchise_id, email, password_hash, user_type, name) "
        "VALUES (42, 7, 'drift@example.com', 'x', 'HQ', '기존관리자')",
    )

    # ── 고장 재현: 두 테이블의 자동생성을 제거한다.
    for table in ("franchise", "user_account"):
        _psql(db, f"ALTER TABLE {table} ALTER COLUMN id DROP DEFAULT")
        _psql(db, f"DROP SEQUENCE {table}_id_seq")
        assert _psql(
            db, f"SELECT pg_get_serial_sequence('{table}', 'id') IS NULL"
        ) == "t", f"{table}.id 고장 재현에 실패했다"
    # Azure 처럼 이력 테이블도 없앤다 — 0001 부터 다시 돌게 만든다.
    _psql(db, "DROP TABLE alembic_version")

    # ── 복구
    _alembic(db, "upgrade", "head")

    # 7개 BIGSERIAL 컬럼 전부 자동생성이 살아 있다.
    for table, column in SERIAL_COLUMNS:
        assert _psql(
            db, f"SELECT pg_get_serial_sequence('{table}', '{column}') IS NOT NULL"
        ) == "t", f"{table}.{column} 자동생성이 복구되지 않았다"

    # id 를 생략한 INSERT 가 성공하고, 기존 최대값(5000) 뒤를 받는다.
    new_id = int(_psql(db, "INSERT INTO franchise (name) VALUES ('신규본사') RETURNING id"))
    assert new_id > 5000, f"새 id 가 기존 최대값보다 작다: {new_id}"
    new_user_id = int(
        _psql(
            db,
            "INSERT INTO user_account (franchise_id, email, password_hash, user_type, name) "
            f"VALUES ({new_id}, 'new@example.com', 'x', 'HQ', '신규관리자') RETURNING id",
        )
    )
    assert new_user_id > 42, f"새 user id 가 기존 최대값보다 작다: {new_user_id}"

    # 기존 행은 그대로다.
    assert _psql(db, "SELECT name FROM franchise WHERE id = 5000") == "기존본사"
    assert _psql(db, "SELECT name FROM franchise WHERE id = 7") == "작은본사"
    assert _psql(db, "SELECT name FROM user_account WHERE id = 42") == "기존관리자"
    assert _psql(db, "SELECT count(*) FROM franchise") == "3"


@pytest.mark.parametrize("scratch_db", [SERIAL_DB], indirect=True)
def test_repair_does_not_rewind_a_sequence_that_is_already_ahead(scratch_db):
    """이미 앞서 있는 시퀀스를 되돌리지 않는다.

    `MAX(id)` 로 무조건 맞추면, 행이 지워져 최대값이 낮아진 테이블에서 시퀀스가
    뒤로 밀린다. 그러면 이후 발급 값이 살아 있는 행과 부딪힐 수 있다.
    """
    db = scratch_db
    _alembic(db, "upgrade", "head")
    _psql(db, "SELECT setval(pg_get_serial_sequence('franchise', 'id'), 900, true)")

    _alembic(db, "stamp", "0006_merge_siren_and_regions")
    _alembic(db, "upgrade", "head")

    new_id = int(_psql(db, "INSERT INTO franchise (name) VALUES ('앞선시퀀스') RETURNING id"))
    assert new_id > 900, f"시퀀스가 되돌려졌다: {new_id}"


@pytest.mark.parametrize("scratch_db", [SERIAL_DB], indirect=True)
def test_repair_refuses_to_invent_values_for_unrecoverable_drift(scratch_db):
    """기본값 없는 NOT NULL 컬럼이 없어진 경우는 조용히 넘기지 않고 실패한다.

    기존 행에 넣을 값을 지어내면 그게 그대로 운영 데이터가 된다. 자동 복구 대신
    어떤 컬럼이 문제인지 알리고 멈추는 편이 맞다.
    """
    db = scratch_db
    _alembic(db, "upgrade", "head")
    _psql(db, "INSERT INTO franchise (name) VALUES ('값있는본사')")
    # 기존 행이 있는 테이블에서 기본값 없는 NOT NULL 컬럼을 없앤다.
    _psql(db, "ALTER TABLE franchise DROP COLUMN name")
    _alembic(db, "stamp", "0006_merge_siren_and_regions")

    with pytest.raises(RuntimeError) as caught:
        _alembic(db, "upgrade", "head")

    message = str(caught.value)
    assert "franchise.name" in message, message
    assert "기존 행에 넣을 값이 없음" in message, message
