"""하위호환 shim — 건축인허가(ArchPmsHubService) 전용 래퍼. 실제 구현은 datago_hub.py."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import datago_hub as _h

_SERVICE = "ArchPmsHubService"


def fetch_all(op: str, **kw):
    return _h.fetch_all(_SERVICE, op, **kw)


def budget_status(ops: list[str]) -> str:
    return _h.budget_status(_SERVICE, ops)
