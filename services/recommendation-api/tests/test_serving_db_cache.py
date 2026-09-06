"""serving_db 의 Seoul-wide 읽기 프로세스 캐시 회귀 테스트.

캐시는 (접속 대상 + 최신 dataset_run) 스탬프 기준으로만 유효해야 하고, 반환값은
호출부가 변형해도 캐시를 오염시키지 않도록 매번 독립 복사본이어야 한다.
"""
import sys
import threading
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
        self._row = {"data_version": "2026-09-05", "run_type": "migration",
                     "completed_at": "2026-09-05 14:08:57+00", "notes": "x"}
        p_target = patch.object(serving_db, "target", lambda: "TESTDB")
        p_target.start(); self.addCleanup(p_target.stop)
        self.calls: list[str] = []

        def fake_raw(sql: str) -> list[dict[str, str]]:
            self.calls.append(sql)
            if sql == serving_db._STAMP_SQL:
                return [dict(self._row)]
            return [{"code": "1", "name": "가"}, {"code": "2", "name": "나"}]

        p_raw = patch.object(serving_db, "_raw_query", side_effect=fake_raw)
        p_raw.start(); self.addCleanup(p_raw.stop)

    def _data_calls(self) -> list[str]:
        return [s for s in self.calls if s != serving_db._STAMP_SQL]

    # ── 기본 적중/복사 ────────────────────────────────────────────
    def test_repeated_query_hits_cache(self) -> None:
        a = serving_db.query("SELECT 1")
        b = serving_db.query("SELECT 1")
        self.assertEqual(self._data_calls(), ["SELECT 1"])
        self.assertEqual(a, b)
        self.assertIsNot(a, b)
        self.assertIsNot(a[0], b[0])

    def test_caller_mutation_does_not_poison_cache(self) -> None:
        first = serving_db.query("SELECT 1")
        first[0]["code"] = "MUT"
        first.append({"code": "x", "name": "x"})
        second = serving_db.query("SELECT 1")
        self.assertEqual(second, [{"code": "1", "name": "가"}, {"code": "2", "name": "나"}])

    def test_distinct_sql_cached_separately(self) -> None:
        serving_db.query("SELECT 1")
        serving_db.query("SELECT 2")
        serving_db.query("SELECT 1")
        self.assertEqual(self._data_calls(), ["SELECT 1", "SELECT 2"])

    # ── 무효화 (B1) ──────────────────────────────────────────────
    def test_data_version_change_invalidates_within_ttl(self) -> None:
        """describe() 경로(data_version())가 TTL 과 무관하게 즉시 무효화한다."""
        serving_db.query("SELECT 1")
        self._row = {**self._row, "data_version": "2026-09-06",
                     "completed_at": "2026-09-06 09:00:00+00"}
        serving_db.data_version()  # run_pipeline 이 매 요청 호출하는 지점
        serving_db.query("SELECT 1")
        self.assertEqual(self._data_calls(), ["SELECT 1", "SELECT 1"])

    def test_completed_at_change_invalidates_even_if_label_same(self) -> None:
        serving_db.query("SELECT 1")
        self._row = {**self._row, "completed_at": "2026-09-05 18:00:00+00"}
        serving_db.data_version()
        serving_db.query("SELECT 1")
        self.assertEqual(self._data_calls(), ["SELECT 1", "SELECT 1"])

    def test_unchanged_version_keeps_cache(self) -> None:
        serving_db.query("SELECT 1")
        serving_db.data_version()
        serving_db.data_version()
        serving_db.query("SELECT 1")
        self.assertEqual(self._data_calls(), ["SELECT 1"])

    def test_ttl_backstop_refetches_when_no_describe(self) -> None:
        with patch.object(serving_db, "_cache_ttl", lambda: 1.0):
            serving_db.query("SELECT 1")
            self._row = {**self._row, "data_version": "2026-09-07"}
            serving_db._DV_STATE["checked_at"] = 0.0  # TTL 만료 강제
            serving_db.query("SELECT 1")
        self.assertEqual(self._data_calls(), ["SELECT 1", "SELECT 1"])

    def test_stamp_failure_bypasses_cache(self) -> None:
        serving_db.clear_cache()

        def boom(sql: str):
            if sql == serving_db._STAMP_SQL:
                raise serving_db.ServingDbError("no db")
            self.calls.append(sql)
            return [{"code": "1"}]

        with patch.object(serving_db, "_raw_query", side_effect=boom):
            serving_db.query("SELECT 9")
            serving_db.query("SELECT 9")
        self.assertEqual(self.calls, ["SELECT 9", "SELECT 9"])  # 캐시 안 함

    # ── 우회 ────────────────────────────────────────────────────
    def test_disabled_env_bypasses_cache(self) -> None:
        with patch.dict("os.environ", {"SERVING_CACHE_DISABLED": "1"}):
            serving_db.query("SELECT 1")
            serving_db.query("SELECT 1")
        self.assertEqual(self._data_calls(), ["SELECT 1", "SELECT 1"])

    def test_use_cache_false_bypasses(self) -> None:
        serving_db.query("SELECT 1", use_cache=False)
        serving_db.query("SELECT 1", use_cache=False)
        self.assertEqual(len(self._data_calls()), 2)

    # ── TTL 파싱 ────────────────────────────────────────────────
    def test_cache_ttl_parsing(self) -> None:
        cases = {"": 300.0, "abc": 300.0, "inf": 300.0, "nan": 300.0,
                 "0": 1.0, "-5": 1.0, "5": 5.0, "999999": 86400.0}
        for raw, want in cases.items():
            with patch.dict("os.environ", {"SERVING_CACHE_TTL_SECONDS": raw}):
                self.assertEqual(serving_db._cache_ttl(), want, raw)

    # ── 방출 ────────────────────────────────────────────────────
    def test_eviction_evicts_oldest_first(self) -> None:
        with patch.object(serving_db, "_CACHE_MAX", 3):
            for i in range(5):
                serving_db.query(f"SELECT {i}")
            self.assertEqual(len(serving_db._CACHE), 3)
            self.assertEqual(len(serving_db._CACHE_ORDER), 3)
            self.calls.clear()
            serving_db.query("SELECT 4")  # 최신 — 여전히 캐시
            serving_db.query("SELECT 0")  # 가장 오래됨 — 방출됐어야 함
            self.assertEqual(self._data_calls(), ["SELECT 0"])

    # ── 동시성 스모크 (S2/S5) ───────────────────────────────────
    def test_concurrent_queries_are_consistent_and_converge(self) -> None:
        """동시 미스는 각자 조회할 수 있으나(단일 비행 아님), 크래시 없이 독립
        복사본을 돌려주고 이후 캐시로 수렴한다. 스탬프 재확인은 herd 를 만들지
        않는다(_DV_LOCK)."""
        barrier = threading.Barrier(8)
        out: list[list] = []

        def worker():
            barrier.wait()
            out.append(serving_db.query("SELECT SAME"))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(out), 8)
        self.assertTrue(all(r == out[0] for r in out))
        self.assertEqual(len({id(r) for r in out}), 8)  # 독립 복사본
        # 스탬프 조회는 동시성과 무관하게 최대 1회
        self.assertLessEqual(self.calls.count(serving_db._STAMP_SQL), 1)
        # 이제 캐시로 수렴 — 추가 백엔드 호출 없음
        n = len(self._data_calls())
        serving_db.query("SELECT SAME")
        self.assertEqual(len(self._data_calls()), n)


if __name__ == "__main__":
    unittest.main()
