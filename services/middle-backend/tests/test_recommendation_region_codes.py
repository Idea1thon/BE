"""Region code handoff tests without a database connection."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.api.v1.location_recommendations import _resolve_region
from app.models.enums import RegionLevel
from app.services import recommendation_client


def session_for(*regions):
    session = SimpleNamespace(execute=AsyncMock())
    session.execute.side_effect = [MagicMock(scalar_one_or_none=lambda r=region: r) for region in regions]
    return session


class RecommendationRegionCodesTests(unittest.IsolatedAsyncioTestCase):
    async def test_dong_parent_codes_are_preserved(self):
        dong = SimpleNamespace(code='11680640', name='역삼1동', level=RegionLevel.DONG, parent_code='11680')
        gu = SimpleNamespace(code='11680', name='강남구', level=RegionLevel.SIGUNGU, parent_code='11')
        sido = SimpleNamespace(code='11', name='서울특별시', level=RegionLevel.SIDO)
        self.assertEqual(await _resolve_region(session_for(dong, gu, sido), dong.code),
                         ('서울특별시', '강남구', '역삼1동', '11680', '11680640'))
        self.assertEqual(await _resolve_region(session_for(gu, sido), gu.code),
                         ('서울특별시', '강남구', None, '11680', None))

    async def test_legacy_legal_dong_code_is_not_silently_truncated(self):
        dong = SimpleNamespace(code='1168010100', name='역삼1동', level=RegionLevel.DONG, parent_code='11680')
        gu = SimpleNamespace(code='11680', name='강남구', level=RegionLevel.SIGUNGU, parent_code='11')
        from app.errors import ApiError
        with self.assertRaises(ApiError):
            await _resolve_region(session_for(dong, gu), dong.code)

    async def test_client_serializes_both_codes_and_omits_unspecified_codes(self):
        captured = []
        def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={'run_id': 'test'})
        async with httpx.AsyncClient(base_url='http://test', transport=httpx.MockTransport(handler)) as client:
            with patch.object(recommendation_client, 'get_client', return_value=client):
                kwargs = dict(sido='서울특별시', sigungu='강남구', dong='역삼1동', industry_code=None,
                              special_condition_text='', limit=None, request_id='test')
                await recommendation_client.request_recommendation(**kwargs, sigungu_code='11680', admin_dong_code='11680640')
                await recommendation_client.request_recommendation(**kwargs)
        self.assertEqual(captured[0]['region']['admin_dong_code'], '11680640')
        self.assertEqual(captured[0]['region']['sigungu_code'], '11680')
        self.assertNotIn('sigungu_code', captured[1]['region'])
        self.assertNotIn('admin_dong_code', captured[1]['region'])
