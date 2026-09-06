"""Source-scoped period equivalence for the numeric grounding gate only."""

import json
import re
from datetime import date


_NATURAL_PERIOD = re.compile(
    r"(?<![\d.+-])(?P<year>[0-9]{4})\s*년\s*"
    r"(?:제\s*)?(?P<part>[0-9]+)\s*(?P<unit>분기|월)"
)


def _natural_key(match: re.Match[str]) -> tuple[int, int, str] | None:
    # A valid month/quarter needs at most two digits, including a leading zero.
    # Bound untrusted model output before converting it to an integer.
    if len(match['part']) > 2:
        return None
    year, part, unit = int(match['year']), int(match['part']), match['unit']
    if year < 1 or not 1 <= part <= (4 if unit == '분기' else 12):
        return None
    return year, part, unit


def _code_key(value: object) -> tuple[int, int, str] | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    code = str(value)
    if not re.fullmatch(r'[0-9]{5,6}', code):
        return None
    year, part = int(code[:4]), int(code[4:])
    unit = '분기' if len(code) == 5 else '월'
    if year < 1 or not 1 <= part <= (4 if unit == '분기' else 12):
        return None
    return year, part, unit


def _iso_month_key(value: object) -> tuple[int, int, str] | None:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}(?:-[0-9]{2})?', value):
        return None
    try:
        observed = date.fromisoformat(value + '-01' if len(value) == 7 else value)
    except ValueError:
        return None
    return observed.year, observed.month, '월'


def normalize_claim_periods(claim: str, cited_texts: list[str]) -> tuple[str, bool]:
    """Return numeric-check text and whether a natural period is unsupported.

    Only explicit top-level structured periods authorize conversion to a code
    or the month portion of a valid ISO date.
    Codes in paths, metric values, or nested data cannot authorize conversion.
    Existing natural-language source periods retain their original number form.
    ISO month aliases retain source hyphens for the signed numeric token parser.
    Callers must still check all numbers and semantically verify the ORIGINAL
    claim: this helper does not establish metric, unit or referent equivalence.
    """
    codes: dict[tuple[int, int, str], str] = {}
    natural: set[tuple[int, int, str]] = set()
    for text in cited_texts:
        try:
            source = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            # Malformed structured data must not become free-text evidence.
            if text.lstrip().startswith(('{', '[')):
                continue
            for match in _NATURAL_PERIOD.finditer(text):
                key = _natural_key(match)
                if key is not None:
                    natural.add(key)
            continue
        if isinstance(source, dict):
            for field in ('period', 'observed_end_period'):
                value = source.get(field)
                key = _code_key(value)
                if key is not None:
                    codes[key] = str(value)
                iso_key = _iso_month_key(value)
                if iso_key is not None:
                    codes[iso_key] = value[:7]

    unsupported = False

    def replace(match: re.Match[str]) -> str:
        nonlocal unsupported
        key = _natural_key(match)
        if key in codes:
            return codes[key]
        if key is not None and key in natural:
            return match.group()
        unsupported = True
        return match.group()

    return _NATURAL_PERIOD.sub(replace, claim), unsupported
