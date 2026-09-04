"""Filesystem anchors shared by the recommendation service."""
from __future__ import annotations

from pathlib import Path


def find_project_root(anchor: str | Path) -> Path:
    """Find the repository root without depending on package depth."""
    path = Path(anchor).resolve()
    candidates = (path, *path.parents) if path.is_dir() else path.parents
    for candidate in candidates:
        if (candidate / "data").is_dir() and (candidate / "artifacts").is_dir():
            return candidate
    raise RuntimeError(f"프로젝트 루트를 찾을 수 없습니다: {anchor}")
