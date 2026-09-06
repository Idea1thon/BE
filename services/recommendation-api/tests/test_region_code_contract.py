import unittest
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from pydantic import ValidationError
from api.main import PipelineRecommendationRequest, ServiceConfig, _pipeline_request


class RegionCodeContractTests(unittest.TestCase):
    def test_optional_codes_survive_request_conversion(self):
        payload = PipelineRecommendationRequest(region={'sido': '서울특별시', 'sigungu': '강남구', 'dong': '역삼1동',
            'sigungu_code': '11680', 'admin_dong_code': '11680640'})
        request = _pipeline_request(payload, ServiceConfig(quarter='20262', source='db', llm_mode='offline', limit=1))
        self.assertEqual(request.sigungu_code, '11680')
        self.assertEqual(request.admin_dong_code, '11680640')

    def test_name_only_requests_remain_supported(self):
        payload = PipelineRecommendationRequest(region={'sigungu': '강남구'})
        request = _pipeline_request(payload, ServiceConfig(quarter='20262', source='db', llm_mode='offline', limit=1))
        self.assertIsNone(request.sigungu_code)
        self.assertIsNone(request.admin_dong_code)

    def test_invalid_code_shape_or_parent_mismatch_is_rejected(self):
        for changes in ({'sigungu_code': '1168x'}, {'admin_dong_code': '1168010100'},
                        {'admin_dong_code': 'abc'}, {'admin_dong_code': '11440660'},
                        {'dong': None}, {'admin_dong_code': 11680640}):
            region = {'sigungu': '강남구', 'dong': '역삼1동', 'sigungu_code': '11680', 'admin_dong_code': '11680640', **changes}
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                PipelineRecommendationRequest(region=region)
