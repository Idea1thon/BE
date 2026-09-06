"""이슈 #28 — 인구 3종(상주·직장·외국인) 로더·근거 연결 테스트.

계약: candidate-selection-spec.md §0-8·§0-9·§4-2, rag-evidence-schema.json.
"""

from __future__ import annotations

import csv
import unittest
from pathlib import Path

from recommendation import population
from recommendation.pipeline import ROOT, load_scope_index

FLOW_DONG, _ = load_scope_index("data/길단위인구", "길단위인구-행정동", "20261", "행정동_코드")


class PopulationLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pop = population.load_population(ROOT, FLOW_DONG)

    def test_as_of_snapshots_are_stepwise_confirmed_quarters(self):
        self.assertEqual(self.pop.as_of["resident"], "20234")
        self.assertEqual(self.pop.as_of["worker"], "20244")
        self.assertEqual(self.pop.as_of["foreign_resident"], "20262")

    def test_coverage_matches_audit_ranges(self):
        # data-usage-classification.md 부록 C.1 (근사 범위, ±few).
        self.assertGreater(self.pop.coverage["resident_trdar"], 1600)
        self.assertEqual(self.pop.coverage["resident_dong"], 425)
        self.assertGreater(self.pop.coverage["worker_trdar"], 1600)
        self.assertLess(self.pop.coverage["worker_dong"], 425)  # 11개 동 결측
        self.assertGreaterEqual(self.pop.coverage["foreign_dong"], 420)

    def test_resident_stepwise_value_frozen_from_as_of_to_latest(self):
        """20234 파티션 값이 20261 파티션과 동일해야(계단식 동결) — 부록 C.2 근거."""
        path = ROOT / population._RESIDENT_DIR / population._RESIDENT_FILES["행정동"]
        by_q: dict[str, dict[str, str]] = {}
        for row in population._read_csv(path):
            if row.get("행정동_코드") == "11710670":  # 잠실3동
                by_q[row["기준_년분기_코드"]] = row
        self.assertEqual(by_q["20234"]["총_상주인구_수"], by_q["20261"]["총_상주인구_수"])
        # 20234 직전(20233)과는 값이 달라야(그때 마지막 갱신)
        self.assertNotEqual(by_q["20233"]["총_상주인구_수"], by_q["20234"]["총_상주인구_수"])

    def test_foreign_ratio_a_is_long_foreign_over_resident(self):
        d = "11710670"
        fr = self.pop.foreign_dong[d]
        expected = fr["장기_외국인_평균"] / fr["총_상주인구_수"]
        self.assertAlmostEqual(fr["ratio_a"], expected, places=6)

    def test_foreign_trdar_is_area_weighted_proxy(self):
        for code, row in self.pop.foreign_trdar.items():
            self.assertTrue(row["grain_is_proxy"])
            break
        # crosswalk 커버리지 — 대부분 상권이 대리값을 받음
        self.assertGreater(self.pop.coverage["foreign_trdar"], 1500)

    def test_no_partial_quarter_20263(self):
        self.assertEqual(population.FOREIGN_PARTIAL_EXCLUDED, "20263")
        # 로더는 20262만 — 20263 행이 섞이면 커버리지가 2배가 되므로 간접 확인
        self.assertLess(self.pop.coverage["foreign_dong"], 500)


class PopulationContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pop = population.load_population(ROOT, FLOW_DONG)
        # 잠실3동 + 한 상권 코드 골라서 컨텍스트 생성
        cls.dong = "11710670"
        cls.host = next(iter(cls.pop.resident_trdar))
        cls.flow_seoul = sorted([float(i) for i in range(1, 400)])

    def _ctx(self, host_code=None, dong_code=None):
        return population.context_for_candidate(
            self.pop, host_code=host_code, dong_code=dong_code,
            dong_sigungu="송파구", target_sigungu="송파구",
            flow_dong_total=50000.0, flow_dong_seoul=self.flow_seoul,
        )

    def test_all_five_fcs_land_in_context_notes_only(self):
        ctx = self._ctx(host_code=self.host, dong_code=self.dong)
        joined = " ".join(ctx.context_notes)
        for fc in ("FC-03", "FC-04", "FC-05", "FC-06a", "FC-06b"):
            self.assertIn(fc, joined, fc)
        # 버킷 규율: reasons·counter로는 안 감 (PopulationContext에 그런 필드 자체가 없음)
        self.assertFalse(hasattr(ctx, "reasons"))

    def test_as_of_populated_when_pop_present(self):
        ctx = self._ctx(host_code=self.host, dong_code=self.dong)
        self.assertEqual(ctx.as_of["resident"], "20234")
        self.assertEqual(ctx.as_of["worker"], "20244")
        self.assertEqual(ctx.as_of["foreign_resident"], "20262")

    def test_trdar_grain_foreign_triggers_confidence_downgrade(self):
        ctx = self._ctx(host_code=self.host, dong_code=self.dong)
        self.assertTrue(ctx.confidence_downgrade)
        self.assertTrue(any("FC-06a" in r for r in ctx.confidence_reasons))

    def test_dong_only_foreign_is_not_proxy_no_downgrade(self):
        ctx = self._ctx(host_code=None, dong_code=self.dong)
        # 행정동 직접이면 proxy 아님 → 하향 없음
        self.assertFalse(ctx.confidence_downgrade)

    def test_evidence_items_carry_required_population_fields(self):
        ctx = self._ctx(host_code=self.host, dong_code=self.dong)
        pop_ev = [e for e in ctx.evidence if e["feature_id"] in ("FC-03", "FC-04", "FC-05", "FC-06a", "FC-06b")]
        self.assertGreaterEqual(len(pop_ev), 4)
        for e in pop_ev:
            self.assertIn("observed_end_period", e)
            self.assertIn("update_cycle", e)
            self.assertIn(e["update_cycle"], ("stepwise", "quarterly"))
            self.assertTrue(e["source_path"])
            if e["feature_id"] in ("FC-06a", "FC-06b"):
                self.assertIn("approximation_note", e)
                self.assertEqual(e["partial_period_excluded"], "20263")
            if e.get("grain_is_proxy"):
                self.assertTrue(e.get("proxy_note"))

    def test_none_pop_returns_missing_for_all_five_fcs_not_crash(self):
        ctx = population.context_for_candidate(
            None, host_code="x", dong_code="y", dong_sigungu=None,
            target_sigungu="송파구", flow_dong_total=None, flow_dong_seoul=[],
        )
        self.assertEqual(ctx.context_notes, [])
        missing_fcs = {m["feature"] for m in ctx.missing}
        self.assertEqual(missing_fcs, {"FC-03", "FC-04", "FC-05", "FC-06a", "FC-06b"})


class PipelineInvarianceTests(unittest.TestCase):
    """인구 연결이 fit_tier·정렬·reasons·counter_evidence를 절대 바꾸지 않아야 한다 (F36)."""

    REQ = dict(sido="서울특별시", sigungu="송파구", dong="잠실동",
               industry_code="CS100010", special_condition_text="", quarter="20261")

    def _run(self, patch_pop_none):
        import recommendation.pipeline as P
        from recommendation.pipeline import RecommendationRequest, run_pipeline
        orig = P.FileSource.population
        if patch_pop_none:
            P.FileSource.population = lambda self, flow_dong=None: None
        try:
            return run_pipeline(RecommendationRequest(**self.REQ), source="files", llm_mode="offline", limit=8)
        finally:
            P.FileSource.population = orig

    def test_population_does_not_change_ranking_or_verdict(self):
        with_pop = self._run(patch_pop_none=False)["candidates"]
        without = self._run(patch_pop_none=True)["candidates"]
        self.assertEqual([c["candidate_id"] for c in with_pop], [c["candidate_id"] for c in without])
        for a, b in zip(with_pop, without):
            self.assertEqual(a["fit_tier"], b["fit_tier"], a["candidate_id"])
            self.assertEqual(a["reasons"], b["reasons"], a["candidate_id"])
            self.assertEqual(a["counter_evidence"], b["counter_evidence"], a["candidate_id"])
        # 인구가 있을 때만 수요구성 차원·인구 context_notes가 붙는다
        self.assertIn("수요구성", with_pop[0]["dimension_evidence"])
        self.assertTrue(any("FC-03" in n for n in with_pop[0]["context_notes"]))

    def test_files_mode_vacancy_evidence_has_string_period(self):
        """F38 회귀: files 모드에 R-ONE 공실률 CSV가 없어도 evidence period가 None이면 안 됨."""
        for c in self._run(patch_pop_none=False)["candidates"]:
            for e in c["evidence"]:
                self.assertIsInstance(e["period"], str, f"{c['candidate_id']} {e['metric_name']}")


class DbSourcePopulationParityTests(unittest.TestCase):
    """--source db 인구 근거가 context.population_snapshot에서 --source files와 동일하게 나와야 한다.

    DB(또는 context.population_snapshot) 미가용 시 skip.
    """

    REQ = dict(sido="서울특별시", sigungu="송파구", dong="잠실동",
               industry_code="CS100010", special_condition_text="", quarter="20261")

    def setUp(self):
        try:
            from recommendation import serving_db
            serving_db.query("SELECT 1 FROM context.population_snapshot LIMIT 1")
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"context.population_snapshot 미가용: {type(exc).__name__}")

    def test_db_population_matches_files(self):
        from recommendation.pipeline import RecommendationRequest, run_pipeline
        f = {c["candidate_id"]: c for c in run_pipeline(RecommendationRequest(**self.REQ), source="files", llm_mode="offline", limit=20)["candidates"]}
        d = {c["candidate_id"]: c for c in run_pipeline(RecommendationRequest(**self.REQ), source="db", llm_mode="offline", limit=20)["candidates"]}
        common = set(f) & set(d)
        self.assertTrue(common, "db·files 공통 후보 없음")

        def pop_notes(c):
            return sorted(n for n in c["context_notes"] if any(t in n for t in ("FC-03", "FC-04", "FC-05", "FC-06")))

        for cid in common:
            self.assertEqual(pop_notes(f[cid]), pop_notes(d[cid]), cid)
            self.assertEqual(f[cid]["profile_ref"]["as_of_quarter"].get("resident"),
                             d[cid]["profile_ref"]["as_of_quarter"].get("resident"), cid)
            self.assertEqual(f[cid]["data_confidence"]["level"], d[cid]["data_confidence"]["level"], cid)
        # db 모드에서도 인구 근거가 실제로 붙어야 한다(missing 처리 아님)
        self.assertTrue(any(pop_notes(c) for c in d.values()), "db 모드 후보에 인구 context_notes 없음")
        self.assertTrue(all("수요구성" in c["dimension_evidence"] for c in d.values()))


if __name__ == "__main__":
    unittest.main()
