"""Application orchestration for provider reads and pure risk calculation."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from typing import Any

from .mappers.risk_request_mapper import build_risk_request
from .models import RiskSirenRequest, SirenAnalyzeTrigger
from .pipeline import analyze
from .providers import (
    FmpProvider,
    IdeatonProvider,
    NullReviewProvider,
    ProviderUnavailable,
    ReviewProvider,
    SqlReviewProvider,
)


class RiskSirenOrchestrator:
    """Coordinate I/O, mapping, and the deterministic calculator.

    Providers are injected so this boundary can be tested without either
    database. ``calculator`` is also injectable for contract-focused tests.
    """

    def __init__(
        self,
        *,
        fmp_provider: Any,
        ideaton_provider: Any,
        review_provider: ReviewProvider | None = None,
        calculator: Callable[[RiskSirenRequest | dict[str, Any]], dict[str, Any]] = analyze,
    ) -> None:
        self.fmp_provider = fmp_provider
        self.ideaton_provider = ideaton_provider
        self.review_provider = review_provider or NullReviewProvider()
        self.calculator = calculator

    async def analyze_trigger(self, trigger: SirenAnalyzeTrigger | dict[str, Any]) -> dict[str, Any]:
        trigger_model = (
            trigger
            if isinstance(trigger, SirenAnalyzeTrigger)
            else SirenAnalyzeTrigger.model_validate(trigger)
        )
        branch = await self.fmp_provider.fetch_branch(trigger_model.branch_id, trigger_model.as_of)
        market = await self.ideaton_provider.fetch_market(branch, trigger_model.as_of)
        reviews = await self.review_provider.fetch_reviews(trigger_model.branch_id, trigger_model.as_of)
        payload = build_risk_request(trigger_model, branch, market, reviews)
        request = RiskSirenRequest.model_validate(payload)
        return self.calculator(request)

    async def close(self) -> None:
        for provider in (self.fmp_provider, self.ideaton_provider, self.review_provider):
            close = getattr(provider, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result


def _similar_codes_from_environment() -> Sequence[str]:
    raw = os.getenv("SIREN_SIMILAR_INDUSTRIES_JSON", "[]").strip()
    if not raw:
        return ()
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderUnavailable("SIREN_SIMILAR_INDUSTRIES_JSON must be valid JSON") from exc
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ProviderUnavailable("SIREN_SIMILAR_INDUSTRIES_JSON must be a JSON string array")
    return values


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ProviderUnavailable(f"{name} must be a boolean")


def build_default_orchestrator() -> RiskSirenOrchestrator:
    """Create the production composition from environment variables."""

    # The current deployment keeps operational reports, synthetic closure
    # aggregates, reviews, and market facts in the single IDEATON database.
    # Keep component-specific variables as explicit overrides for a split
    # deployment, but do not require a database that no longer exists.
    shared_url = (
        os.getenv("SIREN_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or os.getenv("SIREN_IDEATON_DATABASE_URL")
        or os.getenv("IDEATON_DATABASE_URL")
    )
    fmp_url = os.getenv("SIREN_FMP_DATABASE_URL") or os.getenv("FMP_DATABASE_URL") or shared_url
    ideaton_url = os.getenv("SIREN_IDEATON_DATABASE_URL") or os.getenv("IDEATON_DATABASE_URL") or shared_url
    closure_table = os.getenv("SIREN_FRANCHISE_CLOSURE_TABLE")
    review_table = os.getenv("SIREN_REVIEW_TABLE")
    try:
        radius = float(os.getenv("SIREN_COMPETITION_RADIUS_M", "250"))
    except ValueError as exc:
        raise ProviderUnavailable("SIREN_COMPETITION_RADIUS_M must be numeric") from exc
    if radius <= 0:
        raise ProviderUnavailable("SIREN_COMPETITION_RADIUS_M must be greater than zero")

    return RiskSirenOrchestrator(
        fmp_provider=FmpProvider(
            fmp_url,
            franchise_closure_table=closure_table,
            franchise_closure_synthetic=_env_bool("SIREN_FRANCHISE_CLOSURE_SYNTHETIC"),
        ),
        ideaton_provider=IdeatonProvider(
            ideaton_url,
            competition_radius_m=radius,
            similar_industry_codes=_similar_codes_from_environment(),
        ),
        review_provider=SqlReviewProvider(fmp_url, table=review_table),
    )
