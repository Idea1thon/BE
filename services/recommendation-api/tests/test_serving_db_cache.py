"""serving_db 의 Seoul-wide 읽기 프로세스 캐시 회귀 테스트.

캐시는 (접속 대상 + data_version) 스탬프 기준으로만 유효해야 하고, 반환값은
호출부가 변형해도 캐시를 오염시키지 않도록 매번 독립 복사본이어야 한다.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from recommendation import serving_db  # noqa: E402


class ServingDbCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        serving_db.clear_cache()
        self.addCleanup(serving_db.clear_cache)
        self._dv = "2026-09-05"
        # target() / data_version 조회는 고정, 실제 SQL 만 카운트한다.
        p_target = patch.object(serving_db, "target", lambda: "TESTDB")
        p_dv = patch.object(serving_db, "_latest_data_version", lambda: self._dv)
        p_target.start(); p_dv.start()
        self.addCleanup(p_target.stop); self.addCleanup(p_dv.stop)
        self.calls: list[str] = []

        def fake_raw(sql: str) -> list[dict[str, str]]:
            self.calls.append(sql)
            return [{"code": "1", "name": "가"}, {"code": "2", "name": "나"}]

        p_raw = patch.object(serving_db, "_raw_query", side_effect=fake_raw)
        p_raw.start(); self.addCleanup(p_raw.stop)

    def test_repeated_query_hits_cache(self) -> None:
        a = serving_db.query("SELECT 1")
        b = serving_db.query("SELECT 1")
        self.assertEqual(self.calls, ["SELECT 1"])  # 원본 조회는 1회
        self.assertEqual(a, b)
        self.assertIsNot(a, b)
        self.assertIsNot(a[0], b[0])  # 행 dict 도 독립 복사본

    def test_caller_mutation_does_not_poison_cache(self) -> None:
        first = serving_db.query("SELECT 1")
        first[0]["code"] = "MUT"
        first.append({"code": "x", "name": "x"})
        second = serving_db.query("SELECT 1")
        self.assertEqual(second, [{"code": "1", "name": "가"}, {"code": "2", "name": "나"}])

    def test_data_version_change_invalidates(self) -> None:
        serving_db.query("SELECT 1")
        self._dv = "2026-09-06"
        serving_db._DV_STATE["checked_at"] = 0.0  # TTL 만료 강제
        serving_db.query("SELECT 1")
        self.assertEqual(self.calls, ["SELECT 1", "SELECT 1"])  # 재조회됨

    def test_disabled_env_bypasses_cache(self) -> None:
        with patch.dict("os.environ", {"SERVING_CACHE_DISABLED": "1"}):
            serving_db.query("SELECT 1")
            serving_db.query("SELECT 1")
        self.assertEqual(self.calls, ["SELECT 1", "SELECT 1"])

    def test_use_cache_false_bypasses(self) -> None:
        serving_db.query("SELECT 1", use_cache=False)
        serving_db.query("SELECT 1", use_cache=False)
        self.assertEqual(len(self.calls), 2)

    def test_distinct_sql_cached_separately(self) -> None:
        serving_db.query("SELECT 1")
        serving_db.query("SELECT 2")
        serving_db.query("SELECT 1")
        self.assertEqual(self.calls, ["SELECT 1", "SELECT 2"])

    def test_eviction_bounds_cache_size(self) -> None:
        with patch.object(serving_db, "_CACHE_MAX", 4):
            for i in range(10):
                serving_db.query(f"SELECT {i}")
            self.assertLessEqual(len(serving_db._CACHE), 4)


if __name__ == "__main__":
    unittest.main()
