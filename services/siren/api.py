"""FastAPI transport boundary for the risk-siren pipeline."""

from __future__ import annotations

from math import isfinite

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .hq_summary import summarize
from .models import (
    HqSummaryRequest,
    HqSummaryResponse,
    RiskSirenRequest,
    RiskSirenResponse,
)
from .pipeline import analyze


app = FastAPI(
    title="Risk Siren API",
    version="1.0.0",
    description="Deterministic franchise-branch risk analysis with evidence (v1: 5 signals, 2 layers).",
)


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


@app.post("/internal/risk-sirens/analyze", response_model=RiskSirenResponse)
def analyze_risk(request: RiskSirenRequest) -> RiskSirenResponse:
    try:
        return RiskSirenResponse.model_validate(analyze(request))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/internal/risk-sirens/hq-summary", response_model=HqSummaryResponse)
def hq_summary(request: HqSummaryRequest) -> HqSummaryResponse:
    try:
        return HqSummaryResponse.model_validate(summarize(request))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
