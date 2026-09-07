"""Bounded, explicitly unverified draft diagnostics; never used as Evidence."""
from __future__ import annotations

from collections import defaultdict, deque
from hashlib import sha256
import re
from typing import Any


BUCKETS = ('reasons', 'counter_evidence', 'context_notes', 'missing_features')
MAX_DIAGNOSTIC_CLAIMS = 100
MAX_TEXT_CHARS = 500
MAX_SOURCE_IDS = 8
MAX_SOURCE_ID_CHARS = 128


def _safe_excerpt(value: str, limit: int) -> str:
    # JSON permits escaped lone surrogates; rejected draft data must not break
    # the existing ensure_ascii=False / UTF-8 response and artifact writers.
    return re.sub(r'[\ud800-\udfff]', '\ufffd', value[:limit])


def _claims(card: Any):
    if not isinstance(card, dict):
        return
    if 'summary' in card:
        yield 'summary', card['summary']
    for bucket in BUCKETS:
        values = card.get(bucket)
        if isinstance(values, list):
            for index, value in enumerate(values):
                yield f'{bucket}:{index}', value


def _refs(card: Any, position: str) -> list:
    citations = card.get('citations') if isinstance(card, dict) else None
    refs = citations.get(position) if isinstance(citations, dict) else None
    return refs if isinstance(refs, list) else []


def _citation_counts(card: Any) -> tuple[int, int]:
    """Count emitted string refs, including unresolved refs and invalid positions."""
    citations = card.get('citations') if isinstance(card, dict) else None
    count = retrieval = 0
    if isinstance(citations, dict):
        for refs in citations.values():
            if isinstance(refs, list):
                for ref in refs:
                    if isinstance(ref, str):
                        count += 1
                        retrieval += ref.startswith('retrieval-')
    return count, retrieval


def build_verification_diagnostics(
    template: dict[str, Any], draft: Any, final: dict[str, Any],
    decisions: dict[str, str], *, generation_status: str, fallback_reason: str | None,
    restored_source_ids: list[str] | None = None,
    generated_sections: list[str] | None = None,
    failed_sections: list[str] | None = None,
    failed_section_reasons: dict[str, Any] | None = None,
    grounding_status: str = 'not_attempted',
    summary_replaced: bool = False,
    summary_replacement_source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Compare original draft positions with the final, pruned and ordered card.

    Counts cover the full draft; excerpts and source IDs are bounded. A supported
    claim is still lost if whole-card validation falls back. Exact copies bypass
    the semantic verifier, and their citations may be stripped by pruning.
    """
    draft_citations, draft_retrieval = _citation_counts(draft)
    final_citations, final_retrieval = _citation_counts(final)
    final_positions: dict[tuple, deque] = defaultdict(deque)
    for position, text in _claims(final):
        if isinstance(text, str):
            # References disambiguate identical texts with different citations.
            key = (position.split(':')[0], text, tuple(_refs(final, position)))
            final_positions[key].append(position)
    result = {
        'content_status': 'unverified_draft',
        'generation_status': generation_status,
        'generated_sections': list(generated_sections or []),
        'failed_sections': list(failed_sections or []),
        'failed_section_reasons': dict(failed_section_reasons or {}),
        'grounding_status': grounding_status,
        'final_mode': final['explanation_mode'],
        'fallback_reason': fallback_reason,
        'summary_reverted': False,
        'summary_replaced': summary_replaced,
        'summary_replacement_source_ids': list(summary_replacement_source_ids or []),
        'draft_claim_count': 0, 'rewritten_claim_count': 0,
        'verified_claim_count': sum(value == 'supported' for value in decisions.values()),
        'removed_claim_count': 0,
        'verification_counts': {}, 'disposition_counts': {},
        'draft_citation_count': draft_citations, 'final_citation_count': final_citations,
        'draft_retrieval_citation_count': draft_retrieval,
        'final_retrieval_citation_count': final_retrieval,
        'claims': [], 'claims_truncated_count': 0,
        'restored_claim_count': len(restored_source_ids or []),
        'restored_claims': [],
        'restored_claims_truncated_count': max(0, len(restored_source_ids or []) - MAX_DIAGNOSTIC_CLAIMS),
    }
    for position, text in _claims(draft):
        result['draft_claim_count'] += 1
        bucket = position.split(':')[0]
        verbatim = isinstance(text, str) and (
            text == template['summary'] if bucket == 'summary'
            else text in template.get(bucket, [])
        )
        if isinstance(text, str) and not verbatim:
            result['rewritten_claim_count'] += 1
        verification = ('verbatim' if verbatim else decisions.get(position, 'not_checked'))
        if not isinstance(text, str):
            verification = 'invalid_claim_type'
        final_position = None
        if fallback_reason:
            disposition = 'card_fallback'
        elif verification in {'supported', 'verbatim'}:
            key = (bucket, text, tuple(_refs(draft, position)) if not verbatim else ())
            positions = final_positions.get(key)
            final_position = positions.popleft() if positions else None
            disposition = 'kept' if final_position is not None else 'removed'
        else:
            disposition = 'summary_reverted' if bucket == 'summary' else 'removed'
        if disposition == 'removed':
            result['removed_claim_count'] += 1
        for field, value in [('verification_counts', verification), ('disposition_counts', disposition)]:
            result[field][value] = result[field].get(value, 0) + 1
        if bucket == 'summary' and isinstance(text, str) and text != template['summary']:
            result['summary_reverted'] = final.get('summary') == template['summary']
        if len(result['claims']) >= MAX_DIAGNOSTIC_CLAIMS:
            result['claims_truncated_count'] += 1
            continue
        refs = _refs(draft, position)
        string_refs = [ref for ref in refs if isinstance(ref, str)]
        result['claims'].append({
            'draft_position': position, 'final_position': final_position,
            'verification': verification, 'disposition': disposition,
            'text_excerpt': _safe_excerpt(text, MAX_TEXT_CHARS) if isinstance(text, str) else '',
            'text_truncated': isinstance(text, str) and len(text) > MAX_TEXT_CHARS,
            'text_sha256': sha256(text.encode('utf-8', errors='surrogatepass')).hexdigest() if isinstance(text, str) else None,
            'source_ids': [_safe_excerpt(ref, MAX_SOURCE_ID_CHARS) for ref in string_refs[:MAX_SOURCE_IDS]],
            'source_ids_truncated': len(string_refs) > MAX_SOURCE_IDS or any(len(ref) > MAX_SOURCE_ID_CHARS for ref in string_refs),
        })
    # These are trusted template facts appended separately from draft claims.
    # Report their final positions after relevance sorting without claiming that
    # the model generated or verified them.
    for source_id in (restored_source_ids or [])[:MAX_DIAGNOSTIC_CLAIMS]:
        bucket, _, index = source_id.partition(':')
        text = template[bucket][int(index)]
        position = next((position for position, value in _claims(final)
                         if position.startswith(bucket + ':') and value == text), None)
        result['restored_claims'].append({'source_id': source_id, 'final_position': position})
    return result
