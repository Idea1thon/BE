import unittest

from recommendation.question_contract import build_question_contract, retrieval_requests_for_contract


class QuestionContractTests(unittest.TestCase):
    def contract(self, text):
        return build_question_contract({'normalized_text': text})

    def test_office_question_requests_workers_and_competitors_with_lunch_gap(self):
        c = self.contract('직장인 점심 수요와 카페 경쟁 현황을 비교해 주세요.')
        self.assertEqual(c['topic_ids'], ['jobs', 'competition'])
        self.assertTrue(c['comparison_requested'])
        self.assertEqual(c['unsupported'][0]['id'], 'lunch_demand')
        self.assertEqual(retrieval_requests_for_contract(c, [])[0]['dimensions'], ['workplace_population', 'stores'])

    def test_no_estimates_keeps_rent_but_explicit_exclusion_removes_it(self):
        self.assertEqual(self.contract('월세는 추정하지 말고 임대료와 공실을 비교')['topic_ids'], ['rent', 'vacancy'])
        self.assertEqual(self.contract('임대료 말고 공실을 비교')['topic_ids'], ['vacancy'])
        self.assertEqual(self.contract('경쟁은 비교하지 마세요')['topic_ids'], [])
        self.assertEqual(retrieval_requests_for_contract(self.contract('경쟁 제외'), ['fallback']), [])

    def test_explicit_exclusions_cover_full_words_and_coordinated_topics(self):
        self.assertEqual(self.contract('직장인구는 제외하고 공실률만 비교')['topic_ids'], ['vacancy'])
        self.assertEqual(self.contract('임대료와 공실은 제외하고 매출만 비교')['topic_ids'], ['sales'])
        self.assertEqual(self.contract('임대료를 비교하고 공실은 제외')['topic_ids'], ['rent'])

    def test_unknown_or_overlong_input_does_not_invent_topics(self):
        self.assertEqual(self.contract(None)['topic_ids'], [])
        c = self.contract('반려견을 동반할 수 있나요?')
        self.assertEqual(c['topic_ids'], [])
        self.assertEqual(c['unsupported'][0]['id'], 'unmapped_question')
        self.assertEqual(retrieval_requests_for_contract(c, ['fallback']), ['fallback'])
