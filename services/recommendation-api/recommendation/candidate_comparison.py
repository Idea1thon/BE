"""Question-specific observations over selected candidates, never a scoring model."""
from __future__ import annotations

import copy
import math
import re
from itertools import combinations
from typing import Any
from .question_explanation import matches_candidate


_METRICS = {
    'rent': ('R-ONE_임대가격지수', '지수'),
    'vacancy': ('R-ONE_공실률', '%'),
    'jobs': ('총_직장_인구_수', '명'),
    'competition': ('_점포수', '개소'),
    'sales': ('_점포당매출', '원/점포·분기'),
    'flow': ('유동밀도', '명/㎡·분기'),
}


def _finite(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _matches(topic: str, metric: Any, candidate: dict) -> bool:
    expected = _METRICS[topic][0]
    if expected.startswith('_'):
        industry = candidate.get('industry_code')
        return isinstance(industry, str) and bool(re.fullmatch(r'CS\d{6}', industry)) and metric == industry + expected
    return metric == expected


def _host(candidate: dict) -> str | None:
    host = (candidate.get('location') or {}).get('host_commercial_area') or {}
    value = host.get('code') or (candidate.get('profile_ref') or {}).get('host_area')
    return str(value) if value is not None else None


def _retrieval_observation(item: dict, candidate: dict) -> dict:
    """Map only semantically identical SQL metrics; preserve real proxy scope."""
    normalized = dict(item)
    dimension = item.get('dimension')
    metric = {'rent': 'R-ONE_임대가격지수', 'vacancy': 'R-ONE_공실률',
              'workplace_population': '총_직장_인구_수'}.get(dimension)
    if dimension == 'total_store_count' and candidate.get('industry_code') and item.get('industry_code') == candidate['industry_code']:
        metric = f"{candidate['industry_code']}_점포수"
        if item.get('unit') == '개':
            normalized['unit'] = '개소'
    if metric:
        normalized['metric_name'] = metric
    if dimension in ('rent', 'vacancy'):
        normalized['spatial_grain'] = '서울시' if item.get('source_region') in ('서울', '서울시', '서울전체', '서울특별시') else '권역'
    else:
        normalized['spatial_grain'] = '행정동' if item.get('spatial_unit_type') == 'admin_dong' else '상권'
    normalized['grain_is_proxy'] = item.get('grain_is_proxy', False)
    normalized['source_region'] = item.get('source_region') if dimension in ('rent', 'vacancy') else (item.get('source_region') or item.get('spatial_unit_code'))
    return normalized


def _cell(candidate: dict, topic: str, retrieval: list[dict]) -> dict:
    observations = []
    for index, item in enumerate(candidate.get('evidence') or []):
        if isinstance(item, dict) and _matches(topic, item.get('metric_name'), candidate) and _finite(item.get('value')):
            observations.append((item, f'candidate-evidence:{index}'))
    # Supplement only exact metrics and units for the candidate's host. In
    # particular SQL sales totals and flow totals are not per-store/density data.
    if not observations:
        host = _host(candidate)
        for item in retrieval:
            if not isinstance(item, dict):
                continue
            item = _retrieval_observation(item, candidate)
            ref = item.get('evidence_id')
            if (matches_candidate(item, candidate)
                    and isinstance(ref, str) and ref.startswith('retrieval-')
                    and _matches(topic, item.get('metric_name'), candidate)
                    and item.get('unit') == _METRICS[topic][1]
                    and _finite(item.get('value'))):
                observations.append((item, ref))
        commercial = [observation for observation in observations if observation[0].get('spatial_unit_type') == 'commercial_area']
        if commercial:
            observations = commercial
    base = {'status': 'missing', 'value': None, 'unit': _METRICS[topic][1], 'period': None,
            'spatial_grain': None, 'grain_is_proxy': None, 'source_ids': [], 'metric_name': None,
            'source_region': None, 'limitation': '질문에 필요한 유효한 관측 근거가 없습니다.'}
    if not observations:
        return base
    def signature(item: dict) -> tuple:
        return (item.get('metric_name'), item.get('value'), item.get('unit'),
                str(item.get('observed_end_period') or item.get('period') or ''),
                item.get('spatial_grain'), item.get('grain_is_proxy'), item.get('source_region'))
    first = observations[0][0]
    if any(signature(item) != signature(first) for item, _ in observations[1:]):
        return {**base, 'status': 'ambiguous', 'source_ids': [ref for _, ref in observations],
                'limitation': '서로 다른 관측값 또는 관측 범위가 있어 하나의 값으로 선택하지 않았습니다.'}
    metric, value, unit, period, grain, proxy, region = signature(first)
    return {'status': 'available', 'metric_name': metric, 'value': value, 'unit': unit,
            'period': period or None, 'spatial_grain': grain, 'grain_is_proxy': proxy,
            'source_region': region, 'source_ids': [ref for _, ref in observations],
            'limitation': first.get('limitation') or '지역 배경 관측은 개별 점포 성과나 수요 예측이 아닙니다.'}


def _criterion(topic: str, rows: list[dict]) -> dict:
    available = [row for row in rows if row['cells'][topic]['status'] == 'available']
    result = {'topic_id': topic, 'status': 'insufficient', 'comparisons': [],
              'reason': '비교 가능한 후보 관측이 2개 미만입니다.'}
    if len(available) < 2:
        return result
    result.update(status='incomparable', reason='기간·단위·공간 범위·지표가 같지 않거나 배경 범위가 불명확합니다.')
    if topic == 'rent':
        result['reason'] = '임대가격지수의 기준이 확인되지 않아 권역 간 절대 임대료·월세 우열을 비교할 수 없습니다.'
        return result
    for left, right in combinations(available, 2):
        a, b = left['cells'][topic], right['cells'][topic]
        coordinates = ('period', 'unit', 'spatial_grain', 'metric_name')
        if any(not a[key] or a[key] != b[key] for key in coordinates):
            continue
        if (not re.fullmatch(r'[0-9]{4}[1-4]', a['period'])
                or a['unit'] != _METRICS[topic][1]
                or a['spatial_grain'] not in ('상권', '행정동', '권역', '서울시')
                or type(a['grain_is_proxy']) is not bool or type(b['grain_is_proxy']) is not bool):
            continue
        if a['grain_is_proxy'] != b['grain_is_proxy']:
            continue
        if a['grain_is_proxy'] and (not a['source_region'] or not b['source_region'] or a['source_region'] == b['source_region']):
            continue
        if a['spatial_grain'] == '행정동':
            left_codes, right_codes = left['admin_dong_codes'], right['admin_dong_codes']
            if len(left_codes) != 1 or len(right_codes) != 1 or left_codes == right_codes:
                continue  # A shared dong statistic is not a difference between sites.
        # Same host observations cannot distinguish two sites even when legacy
        # evidence marks its regional grain as non-proxy.
        if left.get('host_area_code') and left.get('host_area_code') == right.get('host_area_code'):
            continue
        difference = a['value'] - b['value']
        if not _finite(difference):
            continue
        result['comparisons'].append({'left_candidate_id': left['candidate_id'],
            'right_candidate_id': right['candidate_id'], 'difference': difference,
            'unit': a['unit'], 'period': a['period'], 'spatial_grain': a['spatial_grain'],
            'source_ids': {'left': a['source_ids'], 'right': b['source_ids']}})
    if result['comparisons']:
        result.update(status='comparable', reason='명시된 후보 쌍의 관측값 차이이며 입지 우열·성공확률·전체 후보 순위가 아닙니다.')
    return result


def build_candidate_comparison(candidates: list[dict], contract: dict,
                               retrieval_evidence: list[dict] | None = None) -> dict:
    """Preserve selection order and expose compatible, source-backed comparisons."""
    topics = list(dict.fromkeys(t for t in contract.get('topic_ids', []) if t in _METRICS))
    result = {'mode': 'observed_comparison' if topics else 'not_requested', 'rows': [],
              'criteria': [], 'unsupported': copy.deepcopy(contract.get('unsupported') or []),
              'limitations': ['기존 선택 후보의 관측 비교입니다. fit_tier 및 순위를 변경하지 않습니다.',
                              '직장인구는 점심 방문량이 아니며 점포수는 성공확률이 아닙니다.']}
    if not topics:
        return result
    for candidate in candidates:
        result['rows'].append({'candidate_id': candidate.get('candidate_id'),
            'place_name': (candidate.get('location') or {}).get('place_name') or candidate.get('place_name'),
            'fit_tier': candidate.get('fit_tier'),
            'host_area_code': _host(candidate),
            'admin_dong_codes': (candidate.get('location') or {}).get('overlapping_units', {}).get('admin_dong') or [],
            'cells': {topic: _cell(candidate, topic, retrieval_evidence or []) for topic in topics}})
    result['criteria'] = [_criterion(topic, result['rows']) for topic in topics]
    return result
