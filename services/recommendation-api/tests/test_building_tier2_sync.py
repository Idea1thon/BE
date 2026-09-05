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
    BUILDING_SEED_CAP, DbSource, _building_seed, _cap_building_seeds,
    _enrich_tier1_rows_with_tier2, load_building_seeds, pg_bool,
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


class DbSourceBuildingRowsBooleanTests(unittest.TestCase):
    """DbSource._building_rows() — 실제 --source db(기본) 경로의 postgres boolean 파싱.

    이전 회귀 테스트는 pg_bool()과 파일 경로(_enrich_tier1_rows_with_tier2)만
    커버했고, 이 P1 수정의 실제 배포 경로인 DbSource._building_rows()는
    검증하지 않았다(2026-09-05 코드 검수 지적) — 여기서 채운다.
    """

    def _make_source(self, rows: list[dict[str, str]]) -> DbSource:
        src = object.__new__(DbSource)  # __init__(DB ping)을 건너뛴다
        src._query = lambda sql: rows  # noqa: ARG005
        return src

    def _row(self, pk: str, x: str, y: str, confirmed_flag: str) -> dict[str, str]:
        return {
            "건물관리번호": pk, "PNU": f"PNU-{pk}", "시군구코드": "11710", "시군구명": "송파구",
            "법정동코드": "1171000000", "대지위치": "서울특별시 송파구 잠실동", "지번": "1",
            "지번구분": "일반", "용도코드": "03000", "용도명": "제1종근린생활시설",
            "용도군": "근린생활1", "지상층수": "3", "지하층수": "0",
            "건축면적_㎡": "100", "연면적_㎡": "300", "건물연식_년": "10",
            "상권_결합": "내부", "상권_코드": "3120225",
            "행정동_코드": "11710650", "행정동_명": "잠실본동",
            "x_5181": x, "y_5181": y,
            "도로명주소": "", "건물명": "",
            "has_confirmed_commercial_floor": confirmed_flag,  # postgres CSV 't'/'f'
        }

    def test_unconfirmed_postgres_f_is_not_treated_as_confirmed(self):
        src = self._make_source([self._row("PK1", "10", "10", "f")])
        rows = src._building_rows()
        self.assertEqual(rows[0]["_has_confirmed_commercial_floor"], False)

        from shapely.geometry import box
        seeds = load_building_seeds(box(0, 0, 20, 20), rows, "test")
        self.assertFalse(seeds[0]["has_confirmed_commercial_floor"],
                          "postgres 'f' 문자열이 파이썬 bool()로 새서 True가 되면 안 된다")

    def test_confirmed_postgres_t_is_treated_as_confirmed(self):
        src = self._make_source([self._row("PK2", "10", "10", "t")])
        rows = src._building_rows()
        self.assertTrue(rows[0]["_has_confirmed_commercial_floor"])


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
