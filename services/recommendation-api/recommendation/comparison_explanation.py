"""Render server-computed comparisons with scoped, auditable observation sources."""
from __future__ import annotations

import json
import re

_LABELS = {'rent': '임대가격지수', 'vacancy': '공실률', 'jobs': '직장인구',
           'competition': '동일 업종 점포수', 'sales': '점포당 매출', 'flow': '유동밀도'}


def add_comparison_context(card: dict, candidate: dict, comparison: dict,
                           source_catalog: dict) -> tuple[dict, dict]:
    """Append bounded observations; source catalog is the only mutated input."""
    result = {**card, 'context_notes': list(card.get('context_notes') or []),
              'missing_features': list(card.get('missing_features') or []),
              'citations': dict(card.get('citations') or {})}
    diagnostic = {'mode': 'not_requested', 'server_rendered_claims': []}
    candidate_id = candidate.get('candidate_id')
    rows = {row.get('candidate_id'): row for row in comparison.get('rows', [])}
    if candidate_id not in rows:
        if 'citations' not in card:
            result.pop('citations')
        return result, diagnostic
    seen = set()
    for criterion in comparison.get('criteria', []):
        topic = criterion.get('topic_id')
        if topic not in _LABELS or topic in seen or len(seen) >= 6:
            continue
        seen.add(topic)
        label = _LABELS[topic]
        pair = next((pair for pair in criterion.get('comparisons', [])
                     if candidate_id in (pair.get('left_candidate_id'), pair.get('right_candidate_id'))), None)
        if criterion.get('status') != 'comparable' or pair is None:
            reason = criterion.get('reason') if criterion.get('status') != 'comparable' else '이 후보에 비교 가능한 관측 쌍이 없습니다.'
            claim = f"{label} 후보 간 비교 미확인: {reason or '비교 가능한 관측 근거가 부족합니다.'}"
            if claim not in result['missing_features']:
                position = f"missing_features:{len(result['missing_features'])}"
                result['missing_features'].append(claim)
                diagnostic['server_rendered_claims'].append({'final_position': position,
                    'topic_id': topic, 'method': 'comparison_unavailable',
                    'status': criterion.get('status')})
            continue
        left_id, right_id = pair['left_candidate_id'], pair['right_candidate_id']
        left, right = rows.get(left_id), rows.get(right_id)
        if left is None or right is None:
            continue
        a, b = left.get('cells', {}).get(topic), right.get('cells', {}).get(topic)
        if not a or not b or a.get('status') != 'available' or b.get('status') != 'available':
            continue
        ref = f'comparison:{topic}:{left_id}:{right_id}'
        left_name, right_name = left.get('place_name') or left_id, right.get('place_name') or right_id
        source = {'topic_id': topic, 'left_candidate_id': left_id, 'right_candidate_id': right_id,
                  'left_place_name': left_name, 'right_place_name': right_name,
                  'left_observation': a, 'right_observation': b, 'comparison': pair}
        source_catalog[ref] = {'bucket': 'context_notes', 'text': json.dumps(source, ensure_ascii=False, allow_nan=False)}
        if any(ref in refs for refs in result['citations'].values()):
            continue
        period = str(pair['period'])
        if re.fullmatch(r'[0-9]{4}[1-4]', period):
            period = f'{period[:4]}년 {period[4]}분기'
        claim = (f"{label} 관측 비교(기간 {period}, 공간 단위 {pair['spatial_grain']}): "
                 f"{left_name}의 배경값 {a['value']}{pair['unit']}, "
                 f"{right_name}의 배경값 {b['value']}{pair['unit']}; "
                 f"앞 후보에서 뒤 후보를 뺀 차이는 {pair['difference']}{pair['unit']}입니다. "
                 '지역 배경 관측의 차이이며 개별 건물·점포의 실적, 입지 우열 또는 성공확률을 뜻하지 않습니다.')
        position = f"context_notes:{len(result['context_notes'])}"
        result['context_notes'].append(claim)
        result['citations'][position] = [ref]
        diagnostic['server_rendered_claims'].append({'final_position': position, 'source_id': ref,
            'topic_id': topic, 'method': 'server_observed_comparison'})
    diagnostic['mode'] = 'observed_comparison'
    if diagnostic['server_rendered_claims'] and result.get('explanation_mode') == 'llm':
        result['explanation_mode'] = 'mixed'
    return result, diagnostic
