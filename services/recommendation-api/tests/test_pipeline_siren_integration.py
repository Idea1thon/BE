"""The combined pipeline-api exposes recommendation and Siren routes together."""
from __future__ import annotations

import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parent.parent
for path in (SERVICE_ROOT, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from api.main import app


def test_pipeline_api_contains_recommendation_and_siren_endpoints() -> None:
    paths = {route.path for route in app.routes}

    assert "/internal/recommendations" in paths
    assert "/internal/recommendations/{run_id}" in paths
    assert "/internal/risk-sirens/analyze" in paths
    assert "/internal/risk-sirens/analyze-trigger" in paths
    assert "/internal/risk-sirens/hq-summary" in paths


def test_pipeline_api_uses_one_internal_service_process() -> None:
    route_modules = {
        route.endpoint.__module__
        for route in app.routes
        if route.path.startswith("/internal/")
    }

    assert "api.main" in route_modules
    assert "services.siren.api" in route_modules
