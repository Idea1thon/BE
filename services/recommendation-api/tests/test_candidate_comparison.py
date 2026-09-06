import copy
import unittest

from recommendation.candidate_comparison import build_candidate_comparison


def candidate(identifier, value=20, **changes):
    item = {'metric_name': '총_직장_인구_수', 'value': value, 'unit': '명',
            'period': '20262', 'spatial_grain': '상권', 'grain_is_proxy': False}
    item.update(changes)
    return {'candidate_id': identifier, 'place_name': identifier, 'fit_tier': 'B',
            'location': {'host_commercial_area': {'code': identifier}}, 'evidence': [item]}


class CandidateComparisonTests(unittest.TestCase):
    def test_candidate_industry_metrics_require_exact_requested_industry(self):
        for topic, suffix, unit in (('competition', '_점포수', '개소'), ('sales', '_점포당매출', '원/점포·분기')):
            a = candidate('a', metric_name='CS100001' + suffix, unit=unit)
            a['industry_code'] = 'CS100010'
            self.assertEqual(build_candidate_comparison([a], {'topic_ids': [topic]})['rows'][0]['cells'][topic]['status'], 'missing')
            a['evidence'].append(dict(a['evidence'][0], metric_name='CS100010' + suffix, value=50))
            cell = build_candidate_comparison([a], {'topic_ids': [topic]})['rows'][0]['cells'][topic]
            self.assertEqual(cell['status'], 'available')
            self.assertEqual(cell['source_ids'], ['candidate-evidence:1'])
            a.pop('industry_code')
            self.assertEqual(build_candidate_comparison([a], {'topic_ids': [topic]})['rows'][0]['cells'][topic]['status'], 'missing')

    def test_unknown_proxy_and_invalid_shared_coordinates_do_not_compare(self):
        changes = [{'grain_is_proxy': value} for value in (None, 'unknown', 'false', 0, 1)]
        changes += [{'period': value} for value in ('unknown', '20265', '202600', True, '')]
        changes += [{'unit': 'unknown'}, {'unit': '천명'}, {'spatial_grain': 'unknown'}]
        for change in changes:
            with self.subTest(change=change):
                result = build_candidate_comparison([candidate('a', **change), candidate('b', **change)], {'topic_ids': ['jobs']})
                self.assertEqual(result['criteria'][0]['status'], 'incomparable')

    def test_difference_without_mutation_or_ranking(self):
        candidates = [candidate('a', 20), candidate('b', 50)]
        before = copy.deepcopy(candidates)
        result = build_candidate_comparison(candidates, {'topic_ids': ['jobs']})
        self.assertEqual(candidates, before)
        self.assertEqual([r['candidate_id'] for r in result['rows']], ['a', 'b'])
        self.assertEqual(result['criteria'][0]['status'], 'comparable')
        self.assertEqual(result['criteria'][0]['comparisons'][0]['difference'], -30)
        self.assertEqual(result['rows'][0]['cells']['jobs']['source_ids'], ['candidate-evidence:0'])

    def test_valid_observed_quarter_is_used_without_requested_quarter_substitution(self):
        for quarter in ('20261', '20244'):
            candidates = [candidate('a', 20, observed_end_period=quarter), candidate('b', 30, observed_end_period=quarter)]
            result = build_candidate_comparison(candidates, {'topic_ids': ['jobs']})
            self.assertEqual(result['criteria'][0]['status'], 'comparable')
            self.assertEqual(result['criteria'][0]['comparisons'][0]['period'], quarter)

    def test_mismatched_coordinates_do_not_compare(self):
        for change in ({'period': '20261'}, {'unit': '천명'}, {'spatial_grain': '행정동'}, {'observed_end_period': '20261'}):
            result = build_candidate_comparison([candidate('a'), candidate('b', **change)], {'topic_ids': ['jobs']})
            self.assertEqual(result['criteria'][0]['status'], 'incomparable')
            self.assertEqual(result['criteria'][0]['comparisons'], [])

    def test_shared_or_unknown_proxy_has_no_candidate_advantage(self):
        for region in ('서울', None):
            result = build_candidate_comparison([candidate('a', grain_is_proxy=True, source_region=region), candidate('b', grain_is_proxy=True, source_region=region)], {'topic_ids': ['jobs']})
            self.assertEqual(result['criteria'][0]['status'], 'incomparable')

    def test_invalid_numbers_are_missing_not_zero(self):
        for value in (None, True, float('nan'), float('inf'), '20', 10 ** 500):
            result = build_candidate_comparison([candidate('a', value)], {'topic_ids': ['jobs']})
            self.assertEqual(result['rows'][0]['cells']['jobs']['status'], 'missing')
            self.assertEqual(result['criteria'][0]['status'], 'insufficient')

    def test_conflicting_observations_are_ambiguous(self):
        a = candidate('a')
        a['evidence'].append(dict(a['evidence'][0], value=30))
        result = build_candidate_comparison([a], {'topic_ids': ['jobs']})
        self.assertEqual(result['rows'][0]['cells']['jobs']['status'], 'ambiguous')

    def test_rent_index_does_not_claim_cheaper_rent(self):
        candidates = [candidate('a', 105.3, metric_name='R-ONE_임대가격지수', unit='지수'), candidate('b', 102, metric_name='R-ONE_임대가격지수', unit='지수')]
        result = build_candidate_comparison(candidates, {'topic_ids': ['rent']})
        self.assertEqual(result['criteria'][0]['status'], 'incomparable')
        self.assertIn('월세', result['criteria'][0]['reason'])

    def test_raw_sql_sales_never_fills_sales_per_store(self):
        result = build_candidate_comparison([candidate('a')], {'topic_ids': ['sales']}, [{'evidence_id': 'retrieval-a', 'dimension': 'sales', 'value': 1000, 'unit': '원', 'period': '20262', 'spatial_unit_type': 'commercial_area', 'spatial_unit_code': 'a'}])
        self.assertEqual(result['rows'][0]['cells']['sales']['status'], 'missing')

    def test_retrieval_requires_host_metric_and_unit(self):
        item = {'evidence_id': 'retrieval-a', 'metric_name': '총_직장_인구_수', 'value': 40, 'unit': '명', 'period': '20262', 'spatial_unit_type': 'commercial_area', 'spatial_unit_code': 'a'}
        for changes, status in (({}, 'available'), ({'spatial_unit_code': 'b'}, 'missing'), ({'unit': '천명'}, 'missing'), ({'evidence_id': 'invented'}, 'missing')):
            result = build_candidate_comparison([candidate('a', None)], {'topic_ids': ['jobs']}, [dict(item, **changes)])
            self.assertEqual(result['rows'][0]['cells']['jobs']['status'], status)

    def test_difference_overflow_is_not_serialized(self):
        result = build_candidate_comparison([candidate('a', 1e308), candidate('b', -1e308)], {'topic_ids': ['jobs']})
        self.assertEqual(result['criteria'][0]['status'], 'incomparable')

    def test_empty_topics_preserves_unsupported(self):
        result = build_candidate_comparison([candidate('a')], {'topic_ids': [], 'unsupported': [{'id': 'lunch_demand'}]})
        self.assertEqual(result['mode'], 'not_requested')
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['unsupported'], [{'id': 'lunch_demand'}])

    def test_real_candidate_location_name_and_same_host_are_preserved(self):
        a, b = candidate('a'), candidate('b')
        a['location']['place_name'] = '건물 인근'
        b['location']['host_commercial_area']['code'] = 'a'
        result = build_candidate_comparison([a, b], {'topic_ids': ['jobs']})
        self.assertEqual(result['rows'][0]['place_name'], '건물 인근')
        self.assertEqual(result['criteria'][0]['status'], 'incomparable')

    def test_sql_dimensions_preserve_proxy_scope_and_map_only_same_units(self):
        a = candidate('a', None)
        a['industry_code'] = 'CS100010'
        common = {'evidence_id': 'retrieval-a', 'value': 20, 'period': '20262',
                  'spatial_unit_type': 'commercial_area', 'spatial_unit_code': 'a'}
        cases = [
            ('jobs', {'dimension': 'workplace_population', 'unit': '명', 'source_region': '실제 상권', 'grain_is_proxy': False}, '상권', False, '실제 상권'),
            ('vacancy', {'dimension': 'vacancy', 'unit': '%', 'source_region': '서울전체', 'grain_is_proxy': True}, '서울시', True, '서울전체'),
            ('competition', {'dimension': 'total_store_count', 'unit': '개', 'industry_code': 'CS100010'}, '상권', False, 'a'),
        ]
        for topic, data, grain, proxy, region in cases:
            cell = build_candidate_comparison([a], {'topic_ids': [topic]}, [{**common, **data}])['rows'][0]['cells'][topic]
            self.assertEqual(cell['status'], 'available')
            self.assertEqual((cell['spatial_grain'], cell['grain_is_proxy'], cell['source_region']), (grain, proxy, region))
        wrong_industry = {**common, 'dimension': 'total_store_count', 'unit': '개', 'industry_code': 'CS100001'}
        self.assertEqual(build_candidate_comparison([a], {'topic_ids': ['competition']}, [wrong_industry])['rows'][0]['cells']['competition']['status'], 'missing')
