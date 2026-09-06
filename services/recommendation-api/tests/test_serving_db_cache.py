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

    def test_entry_expires_after_ttl_even_if_stamp_unchanged(self) -> None:
        """부분 적재 실패로 완료 스탬프가 그대로여도, 엔트리는 삽입 후 TTL 이
        지나면 만료돼 재조회된다 (ziholee P2-1)."""
        clock = [1000.0]
        with patch.object(serving_db.time, "monotonic", lambda: clock[0]), \
             patch.object(serving_db, "_cache_ttl", lambda: 300.0):
            serving_db.query("SELECT 1")          # 삽입 @1000
            clock[0] = 1200.0
            serving_db.query("SELECT 1")          # 200s < 300 → 적중
            clock[0] = 1400.0
            serving_db.query("SELECT 1")          # 400s ≥ 300 → 만료 → 재조회
        self.assertEqual(self._data_calls(), ["SELECT 1", "SELECT 1"])

    def test_inflight_result_not_stored_across_clear(self) -> None:
        """조회 도중 clear_cache() 가 끼면(동일 스탬프 강제 초기화 포함) 그 결과를
        캐시에 다시 넣지 않는다 (ziholee P2-2)."""
        serving_db.query("SEED")  # 스탬프 워밍업
        base = serving_db._raw_query
        n = {"c": 0}

        def racing(sql):
            if sql != serving_db._STAMP_SQL:
                n["c"] += 1
                if n["c"] == 1:
                    serving_db.clear_cache()  # 조회 결과 저장 직전에 초기화
            return base(sql)

        with patch.object(serving_db, "_raw_query", side_effect=racing):
            serving_db.query("RACED")   # gen 이 어긋나므로 저장 스킵
            serving_db.query("RACED")   # 캐시에 없어 재조회
        self.assertEqual(n["c"], 2)
        with serving_db._CACHE_LOCK:
            self.assertEqual(len(serving_db._CACHE), 1)  # 2번째 RACED 만 저장됨

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
            with serving_db._CACHE_LOCK:
                self.assertEqual(len(serving_db._CACHE), 3)
            self.calls.clear()
            serving_db.query("SELECT 4")  # 최신 — 여전히 캐시
            serving_db.query("SELECT 0")  # 가장 오래됨 — 방출됐어야 함
            self.assertEqual(self._data_calls(), ["SELECT 0"])

    def test_repeated_expiry_reinsert_beyond_max_still_hits(self) -> None:
        """동일 SQL 이 만료·재삽입을 상한보다 많이 반복해도, 순서 관리가 어긋나
        방금 저장한 엔트리가 방출되는 일이 없어야 한다 (ziholee P2-3).
        가짜 시계로 매 조회 사이 TTL 을 넘긴 뒤, 시계를 멈추고 재조회하면 적중."""
        clock = [0.0]
        with patch.object(serving_db.time, "monotonic", lambda: clock[0]), \
             patch.object(serving_db, "_cache_ttl", lambda: 300.0):
            for i in range(serving_db._CACHE_MAX + 20):
                if i:
                    clock[0] += 400.0  # 직전 엔트리 만료
                serving_db.query("SELECT SAME")
            n = len(self._data_calls())
            with serving_db._CACHE_LOCK:
                self.assertEqual(len(serving_db._CACHE), 1)
            serving_db.query("SELECT SAME")  # 시계 정지 상태 → 적중
            serving_db.query("SELECT SAME")
            self.assertEqual(len(self._data_calls()), n)

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
