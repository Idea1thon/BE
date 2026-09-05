"""PR #14 리뷰(ziholee) 회귀 테스트 — 상가건물 seed의 postgres boolean 파싱, 밀도 캡.

serving_db.query()는 COPY ... CSV로 postgres boolean을 't'/'f' 문자열로 반환한다.
파이썬 bool("f")는 True이므로, 이 변환을 명시하지 않으면 층별용도 미확인 건물까지
has_confirmed_commercial_floor=True로 처리돼 밀도 캡의 "확인 건물 우선"과
data_confidence 미확인 경고가 둘 다 무력화된다(2026-09-05 발견).
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from recommendation_pipeline import (  # noqa: E402
    DbSource, pg_bool, _building_seed, _building_seeds_from_files, _cap_building_seeds, BUILDING_SEED_CAP,
)


class PgBoolTests(unittest.TestCase):
    def test_postgres_csv_false_string_is_false(self):
        self.assertFalse(pg_bool("f"))

    def test_postgres_csv_true_string_is_true(self):
        self.assertTrue(pg_bool("t"))

    def test_empty_and_none_are_false(self):
        self.assertFalse(pg_bool(""))
        self.assertFalse(pg_bool(None))

    def test_python_bool_would_have_been_wrong(self):
        # 회귀 방지용 — 이 단언이 실패하면 파이썬 bool()의 "f"→True 함정이 사라진 것이므로
        # pg_bool() 자체가 불필요해진 게 아닌지 반드시 재확인한다.
        self.assertTrue(bool("f"))


class DbSourceBuildingSeedsBooleanTests(unittest.TestCase):
    """DbSource._building_seeds()가 postgres CSV 't'/'f' 문자열을 올바르게 해석하는지."""

    def _make_source(self, rows: list[dict[str, str]]) -> DbSource:
        src = object.__new__(DbSource)  # __init__(DB ping)을 건너뛴다
        src._db = type("Stub", (), {"query": staticmethod(lambda sql: rows)})()
        return src

    def test_unconfirmed_row_is_not_treated_as_confirmed(self):
        rows = [{
            "building_pk": "PK1", "pnu": "PNU1", "use_group": "근린생활1",
            "gross_floor_area_m2": "100", "building_age_years": "10",
            "lot_address": "서울특별시 송파구 잠실동", "wkb": _point_wkb(207413.33, 445774.85),
            "building_name": "", "road_address": "",
            "has_confirmed_commercial_floor": "f",  # postgres CSV false
        }]
        src = self._make_source(rows)

        class Buf:  # 전체 커버 버퍼
            def covers(self, _pt):
                return True

        seeds = src._building_seeds(Buf())
        self.assertEqual(len(seeds), 1)
        self.assertFalse(seeds[0]["has_confirmed_commercial_floor"])

    def test_confirmed_row_is_treated_as_confirmed(self):
        rows = [{
            "building_pk": "PK2", "pnu": "PNU2", "use_group": "근린생활1",
            "gross_floor_area_m2": "100", "building_age_years": "10",
            "lot_address": "서울특별시 송파구 잠실동", "wkb": _point_wkb(207413.33, 445774.85),
            "building_name": "", "road_address": "",
            "has_confirmed_commercial_floor": "t",
        }]
        src = self._make_source(rows)

        class Buf:
            def covers(self, _pt):
                return True

        seeds = src._building_seeds(Buf())
        self.assertTrue(seeds[0]["has_confirmed_commercial_floor"])


class CapBuildingSeedsTests(unittest.TestCase):
    """confirmed 건물이 limit 미만이면 전부 포함되는지(H2, 2026-09-05 QA)."""

    def _seed(self, i: int, x: float, y: float, confirmed: bool) -> dict:
        from shapely.geometry import Point
        return _building_seed(
            f"PK{i}", Point(x, y), "data/건축물대장/상가건물_서울.csv",
            use_group="근린생활1", floor_area_m2=float(100 + i), building_age_years=10,
            building_name=None, road_address=None, lot_address="서울특별시 송파구 잠실동",
            has_confirmed_commercial_floor=confirmed,
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


class AmbiguousPnuLinkTests(unittest.TestCase):
    """PR #14 리뷰(ziholee) P2 — 한 PNU에 실재 건물이 2개면 임의로 승격하지 않는지.

    실제 사례: data/건축물대장/api/건물링크_중랑구.csv의 PNU 1126010200101360027에
    mgmBldrgstPk 1008116192(중랑천로 76 에이동)·1008116193(중랑천로12길 10 비동)가
    둘 다 다중후보_미해결=False로 남아있다 — 진짜 서로 다른 건물.
    """

    def test_ambiguous_pnu_gets_no_address_enrichment(self):
        import csv
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            (tmp_root / "data/건축물대장/api").mkdir(parents=True)
            tier1 = tmp_root / "data/건축물대장/상가건물_서울.csv"
            with tier1.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=[
                    "건물관리번호", "PNU", "용도군", "연면적_㎡", "건물연식_년",
                    "대지위치", "x_5181", "y_5181",
                ])
                w.writeheader()
                w.writerow({
                    "건물관리번호": "TIER1-PK", "PNU": "1126010200101360027",
                    "용도군": "근린생활1", "연면적_㎡": "100", "건물연식_년": "10",
                    "대지위치": "서울특별시 중랑구 상봉동", "x_5181": "200000", "y_5181": "450000",
                })
            link_csv = tmp_root / "data/건축물대장/api/건물링크_중랑구.csv"
            with link_csv.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=[
                    "PNU", "지번주소", "도로명주소", "용도군_tier1", "대장_주용도",
                    "mgmBldrgstPk", "매칭", "원후보수", "다중후보_미해결",
                ])
                w.writeheader()
                for pk, addr in (
                    ("1008116192", "서울특별시 중랑구 중랑천로 76 (상봉동)"),
                    ("1008116193", "서울특별시 중랑구 중랑천로12길 10 (상봉동)"),
                ):
                    w.writerow({
                        "PNU": "1126010200101360027", "지번주소": "서울특별시 중랑구 상봉동",
                        "도로명주소": addr, "용도군_tier1": "근린생활1", "대장_주용도": "제1종근린생활시설",
                        "mgmBldrgstPk": pk, "매칭": "지번", "원후보수": "2", "다중후보_미해결": "False",
                    })

            import recommendation_pipeline as rp
            orig_root = rp.ROOT
            rp.ROOT = tmp_root
            try:
                class Buf:
                    def covers(self, _pt):
                        return True

                seeds = _building_seeds_from_files(Buf(), "중랑구")
            finally:
                rp.ROOT = orig_root

        self.assertEqual(len(seeds), 1)
        self.assertIsNone(seeds[0]["road_address"], "모호한 PNU는 임의의 건물 주소로 승격하면 안 된다")


def _point_wkb(x: float, y: float) -> str:
    from shapely.geometry import Point
    return Point(x, y).wkb.hex()


if __name__ == "__main__":
    unittest.main()
