import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recommendation.rag_tools import build_retrieval_evidence, execute_retrieval_requests


class RetrievalGroundingTests(unittest.TestCase):
    def row(self, **overrides):
        return {
            "spatial_unit_type": "admin_dong", "spatial_unit_code": "11710610",
            "spatial_unit_name": "잠실동", "sigungu_name": "송파구", "period": "20261",
            "industry_code": "CS100010", "dimension": "sales", "value": "120000.00",
            "source_table": "location.sales_quarter", **overrides,
        }

    def build(self, *rows):
        return build_retrieval_evidence({"results": [{"rows": list(rows)}]})

    def test_facts_carry_scope_and_stable_ids_independent_of_row_order(self):
        sales = self.row()
        flow = self.row(dimension="flow", value="250.5", industry_code=None, source_table="location.flow_quarter")
        records = self.build(sales, flow, sales)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["value"], 120000)
        self.assertEqual(records[0]["unit"], "원")
        self.assertEqual(records[0]["period"], "20261")
        self.assertEqual(records[0]["industry_code"], "CS100010")
        self.assertEqual(records[1]["value"], 250.5)
        self.assertIsNone(records[1]["industry_code"])
        self.assertEqual({r["evidence_id"] for r in records}, {r["evidence_id"] for r in self.build(flow, sales)})

    def test_missing_nonfinite_and_wrong_provenance_are_not_facts(self):
        for overrides in ({"value": None}, {"value": "NaN"}, {"value": "Infinity"},
                          {"value": True}, {"value": "-1"}, {"period": None},
                          {"industry_code": None}, {"spatial_unit_name": ""},
                          {"source_table": "arbitrary.table"}):
            with self.subTest(overrides=overrides):
                self.assertEqual(self.build(self.row(**overrides)), [])

    def test_admin_dong_row_without_sigungu_name_is_still_a_fact(self):
        # location.area 의 admin_dong 행은 sigungu_name 이 비어 있다.
        records = self.build(self.row(sigungu_name=""))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["spatial_unit_name"], "잠실동")

    def test_store_metrics_preserve_real_zero_and_omit_unknown_count(self):
        records = self.build(self.row(dimension="stores", source_table="location.store_quarter",
                                      value='{"total_store_count": 0, "franchise_store_count": null}'))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["dimension"], "total_store_count")
        self.assertEqual(records[0]["value"], 0)
        self.assertEqual(records[0]["unit"], "개")

    def test_executor_binds_resolved_dong_codes_not_sigungu_name(self):
        queries = []
        def query(sql):
            queries.append(sql)
            return [self.row()]
        context = execute_retrieval_requests(query, [{"tool": "search_region_evidence",
            "dimensions": ["sales", "stores", "flow", "change"]}],
            {"sigungu": "송파구", "dong": "잠실동"}, "CS100010", "20261",
            dong_codes=["11710610", "11710620", "11710670"])
        self.assertEqual(len(queries), 4)
        for sql in queries:
            self.assertIn("a.spatial_unit_type = 'admin_dong'", sql)
            self.assertIn("a.spatial_unit_code IN ('11710610', '11710620', '11710670')", sql)
            self.assertNotIn("a.spatial_unit_name = '잠실동'", sql)
            self.assertIn("'송파구') AS sigungu_name", sql)  # 빈 sigungu_name 채움
            self.assertIn(".period,", sql)
        self.assertEqual(len(build_retrieval_evidence(context)), 1)

    def test_executor_falls_back_to_dong_name_without_codes(self):
        queries = []
        execute_retrieval_requests(lambda sql: queries.append(sql) or [],
            [{"tool": "search_region_evidence", "dimensions": ["sales"]}],
            {"sigungu": "송파구", "dong": "잠실동"}, "CS100010", "20261")
        self.assertIn("a.spatial_unit_name = '잠실동'", queries[0])
        self.assertNotIn("a.sigungu_name = '송파구'", queries[0])

    def test_input_rows_are_bounded(self):
        rows = [self.row(spatial_unit_code=str(i)) for i in range(100)]
        self.assertEqual(len(self.build(*rows)), 80)


if __name__ == "__main__":
    unittest.main()
