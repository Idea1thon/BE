"""FastAPI transport boundary for the risk-siren pipeline."""

from __future__ import annotations

from contextlib import asynccontextmanager
from math import isfinite
import hmac
import os

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .hq_summary import summarize
from .models import (
    HqSummaryRequest,
    HqSummaryResponse,
    RiskSirenRequest,
    RiskSirenResponse,
    SirenAnalyzeTrigger,
)
from .orchestrator import RiskSirenOrchestrator, build_default_orchestrator
from .pipeline import analyze
from .providers import ProviderUnavailable, SourceNotFound


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await _orchestrator.close()


app = FastAPI(
    title="Risk Siren API",
    version="1.0.0",
    description="Deterministic franchise-branch risk analysis with evidence (v1: 5 signals, 2 layers).",
    lifespan=lifespan,
)

_orchestrator: RiskSirenOrchestrator = build_default_orchestrator()
risk_siren_router = APIRouter()


def require_internal_access(
    x_internal_token: str | None = Header(default=None),
) -> None:
    """Protect internal routes when a deployment configures a shared token.

    Local tests and development remain tokenless. Production fails closed when
    the token is missing from configuration; the middle-backend already sends
    the same value through ``X-Internal-Token``.
    """

    # Standalone Siren uses its component-specific variable. The combined
    # pipeline-api intentionally shares one server-to-server credential.
    expected = (
        os.getenv("SIREN_INTERNAL_API_TOKEN", "").strip()
        or os.getenv("INTERNAL_API_TOKEN", "").strip()
    )
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    if not expected:
        if environment in {"production", "prod"}:
            raise HTTPException(status_code=503, detail="siren internal token is not configured")
        return
    if not x_internal_token or not hmac.compare_digest(x_internal_token, expected):
        raise HTTPException(status_code=401, detail="invalid siren internal token")


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # JSON numbers such as 1e309 decode to infinity. Preserve the usual error
    # structure while making rejected non-finite inputs safe to serialize.
    return JSONResponse(
        status_code=422,
        content={
            "detail": jsonable_encoder(
                exc.errors(),
                custom_encoder={float: lambda value: value if isfinite(value) else str(value)},
            )
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "risk-siren", "version": "1.0.0"}


@risk_siren_router.post(
    "/internal/risk-sirens/analyze",
    response_model=RiskSirenResponse,
    dependencies=[Depends(require_internal_access)],
)
def analyze_risk(request: RiskSirenRequest) -> RiskSirenResponse:
    try:
        return RiskSirenResponse.model_validate(analyze(request))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@risk_siren_router.post(
    "/internal/risk-sirens/analyze-trigger",
    response_model=RiskSirenResponse,
    dependencies=[Depends(require_internal_access)],
)
async def analyze_trigger(request: SirenAnalyzeTrigger) -> RiskSirenResponse:
    """Resolve source data inside siren and run the deterministic pipeline."""
    try:
        return RiskSirenResponse.model_validate(await _orchestrator.analyze_trigger(request))
    except SourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@risk_siren_router.post(
    "/internal/risk-sirens/hq-summary",
    response_model=HqSummaryResponse,
    dependencies=[Depends(require_internal_access)],
)
def hq_summary(request: HqSummaryRequest) -> HqSummaryResponse:
    try:
        return HqSummaryResponse.model_validate(summarize(request))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def close_siren_resources() -> None:
    """Release provider engines for standalone and combined deployments."""
    await _orchestrator.close()


app.include_router(risk_siren_router)
