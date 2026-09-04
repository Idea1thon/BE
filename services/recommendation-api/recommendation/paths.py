"""Filesystem anchors shared by the recommendation service."""
from __future__ import annotations

import os
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def find_project_root(anchor: str | Path) -> Path:
    """Resolve the external data root without depending on package depth.

    The deployable service lives below ``services/`` while the large source
    datasets are mounted separately. ``RECOMMENDATION_DATA_ROOT`` therefore
    takes precedence; otherwise local development falls back to the nearest
    ancestor containing ``data/``. A clean checkout remains importable when
    the data volume is absent; the pipeline reports that dependency failure
    when execution is attempted.
    """
    configured = os.getenv("RECOMMENDATION_DATA_ROOT", "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_absolute():
            configured_path = SERVICE_ROOT / configured_path
        return configured_path.resolve()

    path = Path(anchor).resolve()
    candidates = (path, *path.parents) if path.is_dir() else path.parents
    for candidate in candidates:
        if (candidate / "data").is_dir():
            return candidate
    return SERVICE_ROOT.parent.parent
