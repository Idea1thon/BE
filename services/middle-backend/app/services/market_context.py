"""사이렌에 넘길 상권×업종 시장 데이터.

사이렌은 `market_data` 를 **직접 조회하지 않는다**. 우리가 넣어야 하고, 없으면
시장 층이 `missing` 이 되어 종합 점수·등급이 null 인 부분 결과만 나온다
(`services/siren/pipeline.py`).

정본 데이터는 준우형 `ideaton` DB 에 있고 중간 백엔드는 거기 연결돼 있지 않다.
조달 경로는 아직 정해지지 않았다(INTERFACE_SPEC 9장). 그때까지 데모 범위에서
필요한 (시군구, 업종) 조합만 서울 열린데이터 실측 스냅샷으로 들고 간다.

출처는 `app/data/market_context.json` 의 `_provenance` 에 파일 단위로 남아 있다.
값을 만들어내지 않았다 — 없는 조합은 None 이고, 그 점포는 부분 결과가 된다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_PATH = Path(__file__).resolve().parent.parent / "data" / "market_context.json"


@lru_cache(maxsize=1)
def _table() -> dict[str, Any]:
    if not _PATH.exists():
        return {}
    return json.loads(_PATH.read_text(encoding="utf-8"))


def _key(region_code: str, industry_code: str) -> str:
    return f"{(region_code or '').strip()[:5]}:{industry_code}"


def market_data_for(region_code: str, industry_code: str) -> dict[str, Any] | None:
    """(시군구, 업종) 실측 시장 데이터. 없으면 None — 부분 결과로 진행한다."""
    entry = _table().get(_key(region_code, industry_code))
    return entry.get("market_data") if entry else None


def known_combinations() -> list[str]:
    """실측 데이터를 가진 조합. 진단·테스트용."""
    return sorted(_table())
