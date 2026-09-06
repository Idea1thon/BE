import json
import unittest

from recommendation.grounding_periods import normalize_claim_periods


class GroundingPeriodsTests(unittest.TestCase):
    def test_quarter_and_month_normalize_without_changing_metrics(self):
        claim = '2026년 제 2분기 지수 105.3, 2026년 8월 공실 14.91%'
        source = json.dumps({'period': '20262', 'observed_end_period': 202608})
        self.assertEqual(normalize_claim_periods(claim, [source]),
                         ('20262 지수 105.3, 202608 공실 14.91%', False))

    def test_whitespace_and_leading_zero(self):
        self.assertEqual(normalize_claim_periods('2026 년 02 분기', ['{"period":"20262"}']),
                         ('20262', False))

    def test_wrong_period_cannot_borrow_metric_numbers(self):
        for claim in ['2026년 3분기', '2026년 8월', '2025년 2분기']:
            with self.subTest(claim=claim):
                self.assertEqual(normalize_claim_periods(claim, [
                    '{"period":"20262","values":[2026,2025,3,8]}'
                ]), (claim, True))

    def test_only_explicit_top_level_period_fields_authorize_codes(self):
        for source in [
            '{"source_path":"/20262.csv"}', '{"value":20262}',
            '{"nested":{"period":"20262"}}', '[{"period":"20262"}]',
            'source_path=/20262.csv', '{"period":"20262.csv"}',
            '{"source_path":"2026년 2분기.csv"}',
        ]:
            with self.subTest(source=source):
                self.assertEqual(normalize_claim_periods('2026년 2분기', [source]),
                                 ('2026년 2분기', True))

    def test_invalid_period_values_do_not_authorize(self):
        for code, claim in [('20265', '2026년 5분기'), ('202613', '2026년 13월'),
                            ('00002', '0000년 2분기'), ('202600', '2026년 0월')]:
            with self.subTest(code=code):
                self.assertEqual(normalize_claim_periods(claim, [json.dumps({'period': code})]),
                                 (claim, True))

    def test_oversized_period_component_is_rejected_without_exception(self):
        for unit in ('분기', '월'):
            claim = '2026년 ' + '9' * 5000 + unit
            with self.subTest(unit=unit):
                self.assertEqual(normalize_claim_periods(claim, ['{"period":"20262"}']),
                                 (claim, True))

    def test_existing_plain_language_source_keeps_numeric_compatibility(self):
        claim = '2026년 제2분기 지수 105.3'
        self.assertEqual(normalize_claim_periods(claim, ['2026년 2분기 지수는 105.3입니다.']),
                         (claim, False))

    def test_iso_month_and_date_use_source_month_numeric_form(self):
        for field in ('period', 'observed_end_period'):
            for period in ('2026-08', '2026-08-09'):
                with self.subTest(field=field, period=period):
                    source = json.dumps({field: period})
                    self.assertEqual(normalize_claim_periods('2026년 8월', [source]),
                                     ('2026-08', False))
                    self.assertEqual(normalize_claim_periods('2026년 9월', [source]),
                                     ('2026년 9월', True))

    def test_invalid_iso_periods_and_path_dates_do_not_authorize(self):
        for source in [json.dumps({'period': value}) for value in (
            '2026-13', '2026-00', '2026-08-32', '2026-02-29', '2026-8',
            '0000-08', '2026-08-extra',
        )] + ['{"source_path":"/2026-08.csv"}', '{"nested":{"period":"2026-08"}}']:
            with self.subTest(source=source):
                self.assertEqual(normalize_claim_periods('2026년 8월', [source]),
                                 ('2026년 8월', True))

    def test_unrelated_numbers_are_never_rewritten(self):
        claim = '2026만원, 2%, 지수 105.3'
        self.assertEqual(normalize_claim_periods(claim, ['{"period":"20262"}']),
                         (claim, False))

    def test_one_supported_period_does_not_mask_another_unsupported_period(self):
        self.assertEqual(normalize_claim_periods('2026년 2분기 대비 2026년 3분기',
                                                ['{"period":"20262"}']),
                         ('20262 대비 2026년 3분기', True))
