"""Conservative, source-traceable topics shared by retrieval and explanations."""
from __future__ import annotations

import re

_TOPICS = {
    'rent': r'임대료|임대가격|월세|보증금',
    'vacancy': r'공실',
    'jobs': r'직장\s*인구|직장인',
    'competition': r'경쟁|동종\s*점포|점포\s*수',
    'sales': r'매출',
    'flow': r'유동',
}
_DIMENSIONS = {'rent': 'rent', 'vacancy': 'vacancy', 'jobs': 'workplace_population',
               'competition': 'stores', 'sales': 'sales', 'flow': 'flow'}

# An empty free-text field is still a valid request when the UI supplied a
# region and an industry. These are the server-owned analysis dimensions used
# to create the baseline RAG plan; they are deliberately not a user claim.
_DEFAULT_TOPIC_IDS = ('jobs', 'competition', 'sales', 'flow', 'rent', 'vacancy')
_DEFAULT_ANALYSIS_TOPICS = (
    'demand', 'competition', 'population', 'sales_potential',
    'commercial_activity', 'accessibility', 'development', 'risk',
)


def build_question_contract(query_context: dict) -> dict:
    text = query_context.get('normalized_text')
    contract = {'version': 1, 'topic_ids': [], 'topics': [], 'excluded_topics': [],
                'comparison_requested': False, 'unsupported': [],
                'analysis_topics': []}
    has_region_and_industry = bool(
        isinstance(query_context.get('selected_region'), dict)
        and query_context.get('industry_code')
    )
    if not isinstance(text, str) or not text.strip():
        if has_region_and_industry:
            contract.update(
                topic_ids=list(_DEFAULT_TOPIC_IDS),
                topics=[{'id': topic, 'source_text': '지역·업종 기본 분석'}
                        for topic in _DEFAULT_TOPIC_IDS],
                comparison_requested=True,
                analysis_topics=list(_DEFAULT_ANALYSIS_TOPICS),
                mode='default_region_industry',
            )
        return contract
    # Only immediate, explicit topic exclusions are applied. A ban on making
    # estimates ("월세는 추정하지 말라") still requests honest rent coverage.
    mentions = sorted([(match.start(), match.end(), topic, match.group())
                       for topic, pattern in _TOPICS.items() for match in re.finditer(pattern, text)])
    excluded_positions = set()
    for index, (_, end, _, _) in enumerate(mentions):
        if re.match(r'\s*(?:은|는|을|를|이|가)?\s*(?:말고|제외|빼고|비교하지|아닌)', text[end:]):
            excluded_positions.add(index)
            # An explicit exclusion applies to an immediately coordinated noun
            # list, not to an earlier clause with its own predicate.
            previous = index - 1
            while previous >= 0 and re.fullmatch(r'\s*(?:와|과|및|그리고|,|/|·|하고)\s*',
                                                text[mentions[previous][1]:mentions[previous + 1][0]]):
                excluded_positions.add(previous)
                previous -= 1
    for topic in _TOPICS:
        included = [mention[3] for index, mention in enumerate(mentions)
                    if mention[2] == topic and index not in excluded_positions]
        excluded = [mention[3] for index, mention in enumerate(mentions)
                    if mention[2] == topic and index in excluded_positions]
        if included:
            contract['topic_ids'].append(topic)
            contract['topics'].append({'id': topic, 'source_text': included[0]})
        elif excluded:
            contract['excluded_topics'].append(topic)
    contract['comparison_requested'] = bool(re.search(r'비교|차이', text)) and bool(contract['topic_ids'])
    if re.search(r'점심|시간대|방문량', text):
        contract['unsupported'].append({'id': 'lunch_demand',
            'reason': '직장인구·분기 매출로 실제 점심 시간대 방문량이나 수요를 확인할 수 없습니다.'})
    if 'rent' in contract['topic_ids']:
        contract['unsupported'].append({'id': 'property_monthly_rent',
            'reason': '임대가격지수는 개별 매물 월세·보증금이나 권역 간 절대 임대료가 아닙니다.'})
    if not contract['topic_ids'] and not contract['excluded_topics']:
        contract['unsupported'].append({'id': 'unmapped_question',
            'reason': '질문을 지원 지표에 확실히 연결하지 못했습니다. 추가 관측을 추정하지 않습니다.'})
    contract['analysis_topics'] = [
        topic for topic in _DEFAULT_ANALYSIS_TOPICS
        if topic in {'demand', 'competition', 'population', 'sales_potential',
                     'commercial_activity', 'accessibility', 'development', 'risk'}
    ] if contract['topic_ids'] else []
    return contract


def retrieval_requests_for_contract(contract: dict, fallback_requests: list) -> list:
    topics = contract.get('topic_ids') or []
    if not topics:
        return [] if contract.get('excluded_topics') else fallback_requests
    dimensions = list(dict.fromkeys(_DIMENSIONS[topic] for topic in topics if topic in _DIMENSIONS))
    return [{'tool': 'search_region_evidence',
             'dimensions': dimensions,
             'limit': 20, 'reason': '사용자 질문에 명시된 지표를 선택 후보 상권에서 조회'}]
