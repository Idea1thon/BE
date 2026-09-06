"""이슈 #29 — 도시계획·정비사업 추진단계 표준화(계획/추진/착공이후) 로더·연결 테스트."""

from __future__ import annotations

import unittest

from recommendation import urban_plan
from recommendation.pipeline import ROOT


class StageCrosswalkTests(unittest.TestCase):
    def test_urban_5stage_to_3group(self):
        cases = {"초기": "계획", "조합": "추진", "인가": "추진", "착공": "착공이후", "완료": "착공이후"}
        for gubun, expected in cases.items():
            group, _ = urban_plan._stage_group_from_urban(gubun, "")
            self.assertEqual(group, expected, gubun)

    def test_assoc_raw_stage_keyword_mapping(self):
        cases = {
            "정비계획 수립": "계획", "정비구역지정": "계획", "안전진단": "계획",
            "추진위원회승인": "계획", "조합설립인가": "추진", "사업시행인가": "추진",
            "관리처분인가": "추진", "착공": "착공이후", "준공인가": "착공이후",
            "이전고시": "착공이후", "분양": "착공이후",
        }
        for raw, expected in cases.items():
            group, _ = urban_plan._stage_group_from_assoc(raw)
            self.assertEqual(group, expected, raw)

    def test_dissolved_association_flagged(self):
        for raw in ("조합해산", "조합청산", "청산 및 조합해산"):
            group, note = urban_plan._stage_group_from_assoc(raw)
            self.assertEqual(group, "착공이후")
            self.assertIsNotNone(note)
            self.assertIn("해산", note + raw)

    def test_empty_and_unknown_stage_are_conservative_계획(self):
        for raw in ("", "  ", "알수없는단계"):
            group, note = urban_plan._stage_group_from_assoc(raw)
            self.assertEqual(group, "계획")
            self.assertIsNotNone(note)


class LoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = urban_plan.load_from_files(ROOT)

    def test_urban_projects_spatially_joined_to_trdar(self):
        self.assertGreater(self.plan.coverage["urban_trdar"], 900)
        self.assertGreater(self.plan.coverage["urban_projects"], 2000)

    def test_redev_associations_are_sigungu_grain(self):
        self.assertLessEqual(self.plan.coverage["redev_sigungu"], 26)
        self.assertGreater(self.plan.coverage["redev_projects"], 1000)

    def test_every_project_has_a_valid_3group(self):
        for lst in list(self.plan.by_trdar.values())[:200]:
            for p in lst:
                self.assertIn(p.stage_group, urban_plan.STAGE_GROUPS)
        for lst in list(self.plan.by_sigungu.values()):
            for p in lst:
                self.assertIn(p.stage_group, urban_plan.STAGE_GROUPS)

    def test_by_trdar_sorted_by_overlap_desc_deterministic(self):
        for lst in list(self.plan.by_trdar.values())[:100]:
            ratios = [p.overlap_ratio or 0.0 for p in lst]
            self.assertEqual(ratios, sorted(ratios, reverse=True))


class ContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = urban_plan.load_from_files(ROOT)
        # 겹침 사업이 있는 상권 하나
        cls.host = next(code for code, lst in cls.plan.by_trdar.items() if len(lst) >= 2)

    def _ctx(self, host_code=None, sgg="11710"):
        return urban_plan.context_for_candidate(
            self.plan, host_code=host_code, sigungu_code=sgg, sigungu_name="송파구")

    def test_fc51_and_fc52_are_context_notes_only(self):
        ctx = self._ctx(host_code=self.host)
        joined = " ".join(ctx.context_notes)
        self.assertIn("FC-51", joined)
        self.assertFalse(hasattr(ctx, "reasons"))
        self.assertFalse(hasattr(ctx, "counter_evidence"))
        # §428: 사업의 미래를 "재개발 예정/확정"으로 단정하면 안 된다.
        # (금지 안내 문구 "'예정'·'확정' … 금지", 부정어 "미확정"은 허용)
        import re
        for n in ctx.context_notes:
            s = (n.replace("'예정'·'확정' 단정 금지", "")
                  .replace("'예정역'·'확정' 표현 금지", "")
                  .replace("미확정", ""))
            self.assertNotRegex(s, r"(재개발|재건축|정비사업|사업)\s*(예정|확정)", n)
            self.assertNotRegex(s, r"(예정|확정)\s*(재개발|재건축|사업|구역)", n)

    def test_stage_breakdown_in_note(self):
        ctx = self._ctx(host_code=self.host)
        fc51 = [n for n in ctx.context_notes if n.rstrip().endswith("(FC-51)")]
        self.assertTrue(fc51)
        self.assertRegex(fc51[0], r"계획 \d+·추진 \d+·착공이후 \d+")

    def test_none_plan_returns_missing_not_crash(self):
        ctx = urban_plan.context_for_candidate(None, host_code="x", sigungu_code="11710", sigungu_name="송파구")
        self.assertEqual(ctx.context_notes, [])
        feats = {m["feature"] for m in ctx.missing}
        self.assertEqual(feats, {"FC-51", "FC-52"})

    def test_sigungu_association_marked_as_proxy(self):
        ctx = self._ctx(host_code=None, sgg="11710")
        assoc_notes = [n for n in ctx.context_notes if "정비사업조합" in n]
        self.assertTrue(assoc_notes)
        self.assertIn("자치구 grain 대리", assoc_notes[0])
        assoc_ev = [e for e in ctx.evidence if e["metric_name"] == "자치구_정비사업조합"]
        self.assertTrue(assoc_ev[0]["grain_is_proxy"])


class PipelineWiringTests(unittest.TestCase):
    REQ = dict(sido="서울특별시", sigungu="송파구", dong="잠실동",
               industry_code="CS100010", special_condition_text="", quarter="20261")

    def _run(self, patch_none):
        import recommendation.pipeline as P
        from recommendation.pipeline import RecommendationRequest, run_pipeline
        orig = P.FileSource.urban_plan
        if patch_none:
            P.FileSource.urban_plan = lambda self: None
        try:
            return run_pipeline(RecommendationRequest(**self.REQ), source="files", llm_mode="offline", limit=8)
        finally:
            P.FileSource.urban_plan = orig

    def test_plan_does_not_change_ranking_or_verdict(self):
        withp = self._run(patch_none=False)["candidates"]
        without = self._run(patch_none=True)["candidates"]
        self.assertEqual([c["candidate_id"] for c in withp], [c["candidate_id"] for c in without])
        for a, b in zip(withp, without):
            self.assertEqual(a["fit_tier"], b["fit_tier"], a["candidate_id"])
            self.assertEqual(a["reasons"], b["reasons"], a["candidate_id"])
            self.assertEqual(a["counter_evidence"], b["counter_evidence"], a["candidate_id"])
        self.assertTrue(any(n.rstrip().endswith("(FC-51)") for c in withp for n in c["context_notes"]))


class DbSourcePlanParityTests(unittest.TestCase):
    REQ = dict(sido="서울특별시", sigungu="송파구", dong="잠실동",
               industry_code="CS100010", special_condition_text="", quarter="20261")

    def setUp(self):
        try:
            from recommendation import serving_db
            serving_db.query("SELECT 1 FROM context.plan_snapshot LIMIT 1")
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"context.plan_snapshot 미가용: {type(exc).__name__}")

    def test_db_plan_evidence_matches_files(self):
        from recommendation.pipeline import RecommendationRequest, run_pipeline
        f = {c["candidate_id"]: c for c in run_pipeline(RecommendationRequest(**self.REQ), source="files", llm_mode="offline", limit=20)["candidates"]}
        d = {c["candidate_id"]: c for c in run_pipeline(RecommendationRequest(**self.REQ), source="db", llm_mode="offline", limit=20)["candidates"]}
        common = set(f) & set(d)
        self.assertTrue(common)

        def plan_ev(c):
            return sorted((e["feature_id"], e["metric_name"], e["value"]) for e in c["evidence"] if e.get("feature_id") in ("FC-51", "FC-52"))

        def plan_notes(c):
            return sorted(n for n in c["context_notes"] if n.rstrip().endswith("(FC-51)") or n.rstrip().endswith("(FC-52)") or "(FC-51 보조)" in n)

        for cid in common:
            self.assertEqual(plan_ev(f[cid]), plan_ev(d[cid]), cid)
            self.assertEqual(plan_notes(f[cid]), plan_notes(d[cid]), cid)


if __name__ == "__main__":
    unittest.main()
