"""PR #14 리뷰(ziholee) 회귀 테스트 — 서비스 파이프라인에 새로 이식한 로직.

루트 scripts/recommendation_pipeline.py에서 발견된 결함(postgres boolean 파싱,
PNU 다중후보 임의 승격)을 서비스 recommendation/pipeline.py에도 동일 패턴으로
이식했다 — 새로 작성한 코드이지만 같은 함정을 재현하지 않는지 확인한다.
"""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from recommendation.pipeline import (  # noqa: E402
    BUILDING_SEED_CAP, _building_seed, _cap_building_seeds,
    _enrich_tier1_rows_with_tier2, pg_bool,
)


class PgBoolTests(unittest.TestCase):
    def test_postgres_csv_false_string_is_false(self):
        self.assertFalse(pg_bool("f"))

    def test_postgres_csv_true_string_is_true(self):
        self.assertTrue(pg_bool("t"))

    def test_python_bool_would_have_been_wrong(self):
        self.assertTrue(bool("f"))  # 회귀 방지용 — pg_bool()이 왜 필요한지 재확인


class CapBuildingSeedsTests(unittest.TestCase):
    def _seed(self, i: int, x: float, y: float, confirmed: bool) -> dict:
        from shapely.geometry import Point
        return _building_seed(
            {"건물관리번호": f"PK{i}", "대지위치": "서울 송파구 잠실동", "용도군": "근린생활1",
             "연면적_㎡": str(100 + i), "x_5181": str(x), "y_5181": str(y),
             "_has_confirmed_commercial_floor": confirmed},
            Point(x, y), "data/건축물대장/상가건물_서울.csv",
        )

    def test_confirmed_under_limit_all_kept(self):
        confirmed = [self._seed(i, i * 10.0, 0.0, True) for i in range(5)]
        unconfirmed = [self._seed(100 + i, i * 10.0, 100.0, False) for i in range(50)]
        result = _cap_building_seeds(confirmed + unconfirmed, limit=10)
        kept_confirmed = [s for s in result if s["has_confirmed_commercial_floor"]]
        self.assertEqual(len(kept_confirmed), 5, "limit 미만인 confirmed 건물은 전부 포함돼야 한다")
        self.assertEqual(len(result), 10)

    def test_default_cap_constant_unchanged(self):
        self.assertEqual(BUILDING_SEED_CAP, 40)


class AmbiguousPnuEnrichmentTests(unittest.TestCase):
    """한 PNU에 실재 건물이 2개면(다중후보_미해결=False라도) 임의로 승격하지 않는지."""

    def test_ambiguous_pnu_gets_no_address_enrichment(self):
        with tempfile.TemporaryDirectory() as tmp:
            api_dir = Path(tmp) / "data/건축물대장/api"
            api_dir.mkdir(parents=True)
            link_csv = api_dir / "건물링크_테스트구.csv"
            with link_csv.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=[
                    "PNU", "지번주소", "도로명주소", "용도군_tier1", "대장_주용도",
                    "mgmBldrgstPk", "매칭", "원후보수", "다중후보_미해결",
                ])
                w.writeheader()
                for pk, addr in (("PK-A", "주소 A"), ("PK-B", "주소 B")):
                    w.writerow({
                        "PNU": "AMBIGUOUS-PNU", "지번주소": "서울 테스트구", "도로명주소": addr,
                        "용도군_tier1": "근린생활1", "대장_주용도": "제1종근린생활시설",
                        "mgmBldrgstPk": pk, "매칭": "지번", "원후보수": "2", "다중후보_미해결": "False",
                    })

            import recommendation.pipeline as rp
            orig_root = rp.ROOT
            rp.ROOT = Path(tmp)
            try:
                rows = [{"건물관리번호": "TIER1", "PNU": "AMBIGUOUS-PNU", "시군구명": "테스트구"}]
                enriched = _enrich_tier1_rows_with_tier2(rows)
            finally:
                rp.ROOT = orig_root

        self.assertEqual(enriched[0]["도로명주소"], "", "모호한 PNU는 임의의 건물 주소로 승격하면 안 된다")
        self.assertFalse(enriched[0]["_has_confirmed_commercial_floor"])


if __name__ == "__main__":
    unittest.main()
