import unittest
from recommendation.rag_tools import execute_retrieval_requests, build_retrieval_evidence


class TargetedRetrievalTests(unittest.TestCase):
    def execute(self, dimensions, targets=None, query=None, dong=None):
        self.queries = []
        def capture(sql):
            self.queries.append(sql)
            return [{"reg": "present"}] if "to_regclass" in sql else []
        return execute_retrieval_requests(query or capture,
            [{"tool": "search_region_evidence", "dimensions": dimensions}],
            {"sigungu": "마포구", "dong": dong}, "CS100010", "20261",
            target_areas=targets)

    def test_selected_candidate_scope_and_dong_are_both_bound(self):
        self.execute(["sales"], [{"spatial_unit_type": "commercial_area", "spatial_unit_code": "3120042"}], dong="합정동")
        self.assertIn("a.spatial_unit_code IN ('3120042')", self.queries[0])
        self.assertIn("a.admin_dong_name = '합정동'", self.queries[0])
        self.assertIn("a.sigungu_name = '마포구'", self.queries[0])

    def test_empty_or_invalid_targets_do_not_broaden_search(self):
        for targets in ([], [{"spatial_unit_type": "commercial_area", "spatial_unit_code": "1' OR 1=1"}]):
            result = self.execute(["sales"], targets)
            self.assertEqual(self.queries, [])
            self.assertEqual(result["results"][0]["availability"][0]["reason"], "no_target_areas")

    def test_optional_missing_and_query_failure_are_distinct(self):
        missing = self.execute(["rent"], query=lambda sql: [])
        self.assertEqual(missing["results"][0]["availability"][0]["reason"], "table_unavailable")
        def fail(sql):
            raise RuntimeError("private database connection")
        failed = self.execute(["sales"], query=fail)
        self.assertEqual(failed["results"][0]["availability"][0]["status"], "error")
        self.assertNotIn("private", str(failed))

    def test_new_dimensions_preserve_period_and_proxy_rules(self):
        self.execute(["rent", "vacancy", "workplace_population"])
        sql = " ".join(self.queries)
        self.assertIn("cw.join_eligible", sql)
        self.assertIn("'임대가격지수'", sql)
        self.assertIn("'공실률'", sql)
        self.assertIn("p.dataset = 'worker'", sql)
        self.assertIn("p.period <= '20261'", sql)
        self.assertIn("총_직장_인구_수", sql)
        self.assertIn("source_region", sql)

    def test_proxy_evidence_requires_scope_and_preserves_it(self):
        row = {"spatial_unit_type": "commercial_area", "spatial_unit_code": "3120042",
            "spatial_unit_name": "합정", "sigungu_name": "마포구", "period": "20262",
            "dimension": "rent", "source_table": "context.rent_index", "value": "105.3",
            "source_region": "홍대합정", "grain_is_proxy": "t", "limitation": "권역 대리지표", "period_policy": "latest_available"}
        result = build_retrieval_evidence({"results": [{"rows": [row]}]})
        self.assertEqual(result[0]["unit"], "지수")
        self.assertTrue(result[0]["grain_is_proxy"])
        self.assertEqual(result[0]["source_region"], "홍대합정")
        row.pop("source_region")
        self.assertEqual(build_retrieval_evidence({"results": [{"rows": [row]}]}), [])

    def test_optional_table_check_is_cached_and_other_dimensions_survive(self):
        queries = []
        def query(sql):
            queries.append(sql)
            if "to_regclass" in sql:
                return [{"reg": "present"}]
            if "'rent' AS dimension" in sql:
                raise RuntimeError("query failed")
            return [{"value": "14.91"}]
        result = self.execute(["rent", "vacancy", "sales"], query=query)
        states = result["results"][0]["availability"]
        self.assertEqual([s["status"] for s in states], ["error", "available", "available"])
        self.assertEqual(sum("to_regclass('context.rent_index')" in q for q in queries), 1)

    def test_future_or_invalid_input_quarter_rejected_before_query(self):
        for quarter in ("20260", "20265", "2026Q1", "20261' OR true"):
            with self.assertRaises(ValueError):
                execute_retrieval_requests(lambda sql: self.fail("unexpected query"), [],
                    {"sigungu": "마포구"}, "CS100010", quarter)

    def test_worker_counts_are_nonnegative_integers_and_source_is_allowlisted(self):
        row = {"spatial_unit_type": "commercial_area", "spatial_unit_code": "3120042",
            "spatial_unit_name": "합정", "sigungu_name": "마포구", "period": "20254",
            "dimension": "workplace_population", "source_table": "context.population_snapshot",
            "value": "1200", "source_region": "합정", "grain_is_proxy": False,
            "limitation": "직장인구는 실제 점심 방문량이 아님", "period_policy": "latest_not_after_requested_quarter"}
        build = lambda r: build_retrieval_evidence({"results": [{"rows": [r]}]})
        self.assertEqual(build(row)[0]["value"], 1200)
        for value in ("NaN", "Infinity", "-1", "1.5", None):
            self.assertEqual(build({**row, "value": value}), [])
        self.assertEqual(build({**row, "source_table": "untrusted.table"}), [])

    def test_new_dimensions_not_truncated_by_legacy_row_cap(self):
        rows = [{"spatial_unit_type": "commercial_area", "spatial_unit_code": str(i),
            "spatial_unit_name": "합정", "sigungu_name": "마포구", "period": "20261",
            "dimension": "sales", "source_table": "location.sales_quarter", "value": "1",
            "industry_code": "CS100010"} for i in range(100)]
        context = {"results": [{"rows": rows, "dimensions": ["sales", "stores", "flow", "rent", "vacancy"]}]}
        self.assertEqual(len(build_retrieval_evidence(context)), 100)

    def test_fifty_candidate_areas_are_not_cut_at_generic_twenty_row_limit(self):
        targets = [{"spatial_unit_type": "commercial_area", "spatial_unit_code": str(3120000+i)} for i in range(50)]
        rows = [{"spatial_unit_type": "commercial_area", "spatial_unit_code": str(3120000+i),
            "spatial_unit_name": "합정", "sigungu_name": "마포구", "period": "20261",
            "dimension": "sales", "source_table": "location.sales_quarter", "value": "1",
            "industry_code": "CS100010"} for i in range(50)]
        queries = []
        def query(sql):
            queries.append(sql)
            return rows
        result = self.execute(["sales"], targets, query)
        self.assertIn("LIMIT 50", queries[0])
        self.assertEqual(len(result["results"][0]["rows"]), 50)
        self.assertEqual(len(build_retrieval_evidence(result)), 50)

    def test_change_retains_two_metrics_for_each_target_area(self):
        targets = [{"spatial_unit_type": "commercial_area", "spatial_unit_code": str(3120000+i)} for i in range(50)]
        rows = [{"spatial_unit_type": "commercial_area", "spatial_unit_code": str(3120000+i),
            "spatial_unit_name": "합정", "sigungu_name": "마포구", "period": "20261",
            "dimension": metric, "source_table": "context.metric_snapshot", "value": "HH"}
            for i in range(50) for metric in ("change_indicator_code", "change_indicator_name")]
        queries = []
        def query(sql):
            queries.append(sql)
            return rows
        result = self.execute(["change"], targets, query)
        self.assertIn("LIMIT 100", queries[0])
        self.assertEqual(len(build_retrieval_evidence(result)), 100)

    def test_dong_scope_has_geometry_fallback_for_unpopulated_area_metadata(self):
        self.execute(["sales"], [{"spatial_unit_type": "commercial_area", "spatial_unit_code": "3120042"}], dong="합정동")
        sql = self.queries[0]
        self.assertIn("d.spatial_unit_name = '합정동'", sql)
        self.assertIn("d.sigungu_name = '마포구'", sql)
        self.assertIn("ST_Intersects(a.geom, d.geom)", sql)
        self.assertIn("ST_Area(ST_Intersection(a.geom, d.geom)) > 0", sql)

    def test_ambiguous_rent_crosswalk_does_not_pick_arbitrary_source_or_consume_cap(self):
        self.execute(["rent"])
        sql = self.queries[-1]
        self.assertIn("other_cw.target_area_id = cw.target_area_id", sql)
        self.assertIn("other_cw.join_eligible", sql)
        self.assertIn("other_cw.source_area_id <> cw.source_area_id", sql)

    def coded_execute(self, dimensions, targets, query=None):
        self.queries = []
        def capture(sql):
            self.queries.append(sql)
            return [{"reg": "present"}] if "to_regclass" in sql else []
        return execute_retrieval_requests(query or capture,
            [{"tool": "search_region_evidence", "dimensions": dimensions}],
            {"sigungu": "마포구", "dong": "잘못된 이름", "sigungu_code": "11440", "admin_dong_code": "11440660"},
            "CS100010", "20261", target_areas=targets)

    def test_verified_dong_code_searches_without_host_area(self):
        result = self.coded_execute(["sales", "stores", "flow", "change", "workplace_population"], [])
        data_queries = [sql for sql in self.queries if "to_regclass" not in sql]
        self.assertEqual(len(data_queries), 5)
        for sql in data_queries:
            self.assertIn("a.spatial_unit_code = '11440660'", sql)
            self.assertIn("a.sigungu_code = '11440'", sql)
            self.assertNotIn("a.spatial_unit_name =", sql)
            self.assertNotIn("a.sigungu_name =", sql)
            self.assertNotIn("ST_", sql)
        states = result["results"][0]["availability"]
        self.assertEqual(sum(s["spatial_unit_type"] == "admin_dong" for s in states), 5)

    def test_verified_codes_bound_commercial_and_dong_queries_independently(self):
        self.coded_execute(["sales"], [{"spatial_unit_type": "commercial_area", "spatial_unit_code": "3120042"}])
        self.assertEqual(len(self.queries), 2)
        commercial, dong = self.queries
        self.assertIn("a.spatial_unit_code IN ('3120042')", commercial)
        self.assertIn("a.admin_dong_code = '11440660'", commercial)
        self.assertIn("commercial_to_admin_overlap", commercial)
        self.assertIn("dcw.join_eligible", commercial)
        self.assertIn("a.spatial_unit_code = '11440660'", dong)
        for sql in self.queries:
            self.assertNotIn("a.sigungu_name =", sql)
            self.assertNotIn("a.admin_dong_name =", sql)
            self.assertNotIn("ST_", sql)

    def test_dong_has_no_direct_rone_rental_search(self):
        result = self.coded_execute(["rent", "vacancy"], [])
        self.assertEqual(self.queries, [])
        self.assertTrue(all(s["reason"] == "no_target_areas" for s in result["results"][0]["availability"]))

    def test_dong_evidence_survives_zero_target_normalization_bound(self):
        row = {"spatial_unit_type": "admin_dong", "spatial_unit_code": "11440660",
            "spatial_unit_name": "서교동", "sigungu_name": "마포구", "period": "20261",
            "dimension": "sales", "source_table": "location.sales_quarter", "value": "100",
            "industry_code": "CS100010"}
        result = self.coded_execute(["sales"], [], lambda sql: [row])
        self.assertEqual(len(build_retrieval_evidence(result)), 1)

    def test_invalid_verified_code_is_not_silently_replaced_by_name_search(self):
        for field, value in (("sigungu_code", "11440' OR TRUE"), ("admin_dong_code", "11440bad")):
            with self.assertRaises(ValueError):
                execute_retrieval_requests(lambda sql: self.fail("unexpected query"),
                    [{"tool": "search_region_evidence", "dimensions": ["sales"]}],
                    {field: value, "sigungu": "마포구", "dong": "서교동"}, "CS100010", "20261")

    def test_fifty_hosts_plus_dong_are_preserved_and_failure_is_per_grain(self):
        targets = [{"spatial_unit_type": "commercial_area", "spatial_unit_code": str(3120000+i)} for i in range(50)]
        base = {"spatial_unit_name": "서교동", "sigungu_name": "마포구", "period": "20261",
            "dimension": "sales", "source_table": "location.sales_quarter", "value": "100",
            "industry_code": "CS100010"}
        def query(sql):
            if "a.spatial_unit_type = 'admin_dong'" in sql:
                return [{**base, "spatial_unit_type": "admin_dong", "spatial_unit_code": "11440660"}]
            return [{**base, "spatial_unit_type": "commercial_area", "spatial_unit_code": t['spatial_unit_code']} for t in targets]
        result = self.coded_execute(["sales"], targets, query)
        self.assertEqual(len(build_retrieval_evidence(result)), 51)
        def partial_failure(sql):
            if "a.spatial_unit_type = 'commercial_area'" in sql:
                raise RuntimeError("crosswalk unavailable")
            return query(sql)
        result = self.coded_execute(["sales"], targets, partial_failure)
        states = result["results"][0]["availability"]
        self.assertEqual([(s['spatial_unit_type'], s['status']) for s in states],
                         [('commercial_area', 'error'), ('admin_dong', 'available')])
        self.assertEqual(len(build_retrieval_evidence(result)), 1)

    def test_code_queries_use_supplied_gu_only_as_display_fallback(self):
        self.coded_execute(["sales"], [])
        self.assertIn("coalesce(nullif(a.sigungu_name, ''), '마포구') AS sigungu_name", self.queries[0])
        self.assertNotIn("a.sigungu_name =", self.queries[0])
