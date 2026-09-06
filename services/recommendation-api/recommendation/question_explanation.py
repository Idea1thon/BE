"""Bounded server rendering of scoped SQL observations, separate from LLM claims."""
from __future__ import annotations

from .evidence_reranker import source_topic_ids

_DIMENSIONS = {
    'rent': ('rent', '임대가격지수', 'R-ONE 임대 통계'),
    'vacancy': ('vacancy', '공실률', 'R-ONE 공실 통계'),
    'workplace_population': ('jobs', '직장인구', '직장인구 통계'),
    'total_store_count': ('competition', '동일 업종 점포수', '점포 통계'),
    'sales': ('sales', '동일 업종 분기 매출 합계', '추정매출 통계'),
    'flow': ('flow', '유동인구 합계', '유동인구 통계'),
}
NO_QUESTION_EXPLANATION = '질문에 직접 답하는 관측 설명을 구성하지 못했습니다. 질문별 비교표의 관측값과 미확인 항목을 확인해야 합니다.'


def matches_candidate(item: dict, candidate: dict) -> bool:
    location = candidate.get('location') or {}
    host = location.get('host_commercial_area') or {}
    if item.get('dimension') in {'sales', 'total_store_count', 'franchise_store_count'}:
        if not candidate.get('industry_code') or item.get('industry_code') != candidate['industry_code']:
            return False
    if item.get('spatial_unit_type') == 'admin_dong':
        codes = (location.get('overlapping_units') or {}).get('admin_dong') or []
        return str(item.get('spatial_unit_code')) in {str(code) for code in codes}
    return bool(host.get('code')) and (
        item.get('spatial_unit_type') == 'commercial_area'
        and str(item.get('spatial_unit_code')) == str(host['code'])
    )


def finish_question_card(card: dict, candidate: dict, selected: dict,
                         retrieval: list[dict], contract: dict) -> tuple[dict, dict]:
    """Filter optional prose and add exact scoped facts without an LLM verdict.

    Input retrieval has already passed build_retrieval_evidence's table, value,
    period and provenance checks. Added statements are descriptive background,
    not candidate performance, and are diagnosed as server rendering.
    """
    topics = set(contract.get('topic_ids') or [])
    diagnostics = {'mode': 'not_requested', 'removed_optional_claim_count': 0,
                   'server_rendered_claims': []}
    if not topics and not contract.get('excluded_topics'):
        return card, diagnostics
    result = dict(card)
    citations = card.get('citations') or {}
    remapped = {key: value for key, value in citations.items()
                if key.split(':')[0] not in {'reasons', 'context_notes'}}
    grouped = {}
    for item in retrieval:
        dimension = item.get('dimension')
        if dimension not in _DIMENSIONS or not matches_candidate(item, candidate):
            continue
        topic, label, source_name = _DIMENSIONS[dimension]
        if topic not in topics:
            continue
        # Administrative and commercial areas are distinct observations, not
        # conflicting values of one geography. Keep both independently citable.
        group = (topic, item['spatial_unit_type'], item['spatial_unit_code'])
        grouped.setdefault(group, []).append(item)
    ambiguous = {topic for topic, items in grouped.items() if len({
        (item['value'], item['unit'], item['period'], item.get('source_region'), item.get('grain_is_proxy'))
        for item in items}) != 1}
    blocked_refs = {item['evidence_id'] for topic in ambiguous for item in grouped[topic]}
    relevant = {ref: source for ref, source in selected.items()
                if source['bucket'] in {'evidence', 'reasons', 'context_notes'}
                and source_topic_ids(source) & topics and ref not in blocked_refs}
    for bucket in ('reasons', 'context_notes'):
        result[bucket] = []
        for index, claim in enumerate(card.get(bucket) or []):
            refs = citations.get(f'{bucket}:{index}', [])
            verbatim = any(source['bucket'] == bucket and source['text'] == claim
                           for source in relevant.values())
            if any(ref in blocked_refs for ref in refs) or (not verbatim and not any(ref in relevant for ref in refs)):
                diagnostics['removed_optional_claim_count'] += 1
                continue
            position = f'{bucket}:{len(result[bucket])}'
            result[bucket].append(claim)
            if refs:
                remapped[position] = refs
    result['citations'] = remapped
    diagnostics['ambiguous_topics'] = []
    result['missing_features'] = list(card.get('missing_features') or [])
    for group, items in grouped.items():
        topic, spatial_type, _ = group
        if group in ambiguous:
            diagnostics['ambiguous_topics'].append(topic)
            result['missing_features'].append(f'{_DIMENSIONS[items[0]["dimension"]][1]}: 여러 관측값·기간·권역이 연결되어 단일 값으로 표시하지 않았습니다.')
            continue
        item = items[0]
        _, label, source_name = _DIMENSIONS[item['dimension']]
        ref = item['evidence_id']
        if any(ref in refs for refs in remapped.values()):
            continue
        period = str(item['period'])
        scope = item.get('source_region') or item['spatial_unit_name']
        grain = '행정동' if spatial_type == 'admin_dong' else '상권'
        limitation = item.get('limitation') or f'{grain} 배경 통계이며 개별 건물·점포의 실적이나 방문량이 아닙니다.'
        proxy = ' (후보 상권에 연결된 조사권역 대리지표)' if item.get('grain_is_proxy') else ''
        industry = f" / 업종 {item['industry_code']}" if item.get('industry_code') else ''
        claim = (f"{scope} {grain}{proxy}의 {period[:4]}년 {period[4]}분기 {label}는 "
                 f"{item['value']}{item['unit']}입니다. 출처: {source_name}{industry}. {limitation}")
        position = f"context_notes:{len(result['context_notes'])}"
        result['context_notes'].append(claim)
        remapped[position] = [ref]
        diagnostics['server_rendered_claims'].append({'final_position': position, 'source_id': ref,
            'topic_id': topic, 'spatial_unit_type': spatial_type,
            'spatial_unit_code': item['spatial_unit_code'], 'method': 'validated_sql_template'})
    for unsupported in contract.get('unsupported') or []:
        reason = unsupported.get('reason')
        if isinstance(reason, str) and reason not in result['missing_features']:
            result['missing_features'].append(reason)
    diagnostics['optional_answer_available'] = bool(result['reasons'] or result['context_notes'])
    if not diagnostics['optional_answer_available']:
        result['missing_features'].append(NO_QUESTION_EXPLANATION)
    diagnostics['mode'] = 'question_scoped'
    if result.get('explanation_mode') == 'llm' and (
            diagnostics['server_rendered_claims'] or diagnostics['removed_optional_claim_count']):
        result['explanation_mode'] = 'mixed'
    return result, diagnostics
