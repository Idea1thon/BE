"""FastAPI service boundary for the evidence-first recommendation pipeline.

This service is called by the middle backend, not directly by the UI:

    UI -> middle backend -> this REST API -> recommendation
                                      <- validated candidates and Evidence
    UI <- middle backend <- this REST API

The middle backend owns the UI-facing request handling and correlation ID.
This API transports the selected region and special-condition text to the
existing pipeline without interpreting or rewriting the user's request.
The recommendation package itself still performs its configured input-planning,
deterministic validation, data analysis, Evidence validation, and explanation
stages. Risk-siren development is intentionally isolated in
`/Users/parkjunwoo/Documents/siren` and is not connected to this route yet.

Run locally from the repository root with::

    .venv/bin/uvicorn --app-dir services/recommendation-api api.main:app --reload

The heavy pipeline is synchronous and is dispatched through FastAPI's
threadpool. Each request gets an isolated output directory so concurrent
requests do not overwrite a prior run.
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import json
import logging
import os
import re
import secrets
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from recommendation.env import load_env
from recommendation.paths import find_project_root

load_env()
ROOT = find_project_root(__file__)
REGION_CATALOG_PATH = SERVICE_ROOT / "seoul_gu_dong_list.csv"
EVIDENCE_SCHEMA_PATH = SERVICE_ROOT / "artifacts/20-method/rag-evidence-schema.json"
logger = logging.getLogger(__name__)

from recommendation.llm_input_planner import INDUSTRY_NAMES
from recommendation.pipeline import (
    DEFAULT_QUARTER,
    PipelineError,
    PipelineDependencyError,
    PipelineInputError,
    PipelineInternalError,
    RecommendationRequest,
    SUPPORTED_INDUSTRIES,
    normalize_admin_dong_name,
    atomic_write_text,
    run_pipeline,
    validate_candidates,
)


class RegionInput(BaseModel):
    """Region selected by the UI; the LLM cannot change these fields."""

    model_config = ConfigDict(extra="forbid")

    sido: str = Field(default="서울특별시", min_length=1, max_length=40)
    sigungu: str = Field(min_length=1, max_length=40)
    dong: str | None = Field(default=None, max_length=40)
    sigungu_code: str | None = Field(default=None, pattern=r"^[0-9]{5}$")
    admin_dong_code: str | None = Field(default=None, pattern=r"^[0-9]{8}$")

    @model_validator(mode="after")
    def validate_region_codes(self):
        if self.admin_dong_code:
            if not self.dong or not self.dong.strip():
                raise ValueError("행정동 코드에는 행정동 이름이 필요합니다.")
            if self.sigungu_code and not self.admin_dong_code.startswith(self.sigungu_code):
                raise ValueError("행정동 코드와 시군구 코드가 일치하지 않습니다.")
        return self


class PipelineRecommendationRequest(BaseModel):
    """Server-to-server contract sent by the middle backend.

    ``special_condition_text`` is intentionally kept as the original text
    received by the middle backend. The FastAPI boundary does not turn it
    into a different prompt or condition object.
    """

    model_config = ConfigDict(extra="forbid")

    request_id: str | None = Field(default=None, max_length=128)
    region: RegionInput
    industry_code: str | None = Field(default=None, max_length=20)
    special_condition_text: str = Field(default="", max_length=2_000)
    limit: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="반환할 후보 수. 생략하면 서버 기본값을 사용합니다.",
    )


@dataclass(frozen=True)
class ServiceConfig:
    """Execution policy owned by the FastAPI deployment, not the caller."""

    quarter: str
    source: Literal["db", "files"]
    llm_mode: Literal["auto", "required", "offline"]
    limit: int
    include_poi: bool = False
    include_poi_context: bool = False
    include_news: bool = True
    request_timeout_s: float = 180.0
    readiness_timeout_s: float = 3.0
    max_concurrent: int = 4
    seed_mode: Literal["anchors", "buildings", "hybrid"] = "buildings"
    capacity_retry_after_s: int = 10


class RecommendationApiResponse(BaseModel):
    request_id: str | None
    run_id: str
    status: Literal["completed"]
    request: dict[str, Any]
    input_interpretation: dict[str, Any]
    summary: dict[str, Any]
    candidates: list[dict[str, Any]]
    explanations: dict[str, Any]

    @field_validator("candidates")
    @classmethod
    def candidates_follow_evidence_schema(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep the loose transport type while enforcing the shared schema."""
        errors = validate_candidates(value)
        if errors:
            raise ValueError("candidates[]가 LocationCandidateEvidence 스키마를 만족하지 않습니다: " + "; ".join(errors[:3]))
        return value


def _validate_common_input(payload: PipelineRecommendationRequest) -> None:
    if payload.region.sido not in {"서울특별시", "서울"}:
        raise HTTPException(status_code=422, detail={
            "code": "unsupported_region",
            "message": "현재 데이터 계약은 서울특별시만 지원합니다.",
            "questions": [],
        })
    if payload.industry_code and payload.industry_code not in SUPPORTED_INDUSTRIES:
        raise HTTPException(status_code=422, detail={
            "code": "unsupported_industry",
            "message": f"지원하지 않는 업종 코드: {payload.industry_code}",
            "questions": [],
        })


def verify_internal_token(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    """Fail closed for every server-to-server route using this dependency."""
    expected_token = os.getenv("INTERNAL_API_TOKEN", "").strip()
    if not expected_token:
        raise HTTPException(status_code=503, detail={
            "code": "internal_auth_not_configured",
            "message": "INTERNAL_API_TOKEN이 설정되지 않았습니다.",
            "questions": [],
        })
    if not x_internal_token or not secrets.compare_digest(x_internal_token, expected_token):
        raise HTTPException(status_code=401, detail={
            "code": "unauthorized",
            "message": "유효한 내부 호출 토큰이 필요합니다.",
            "questions": [],
        })


def _region_options() -> list[tuple[str, str]]:
    path = REGION_CATALOG_PATH
    if not path.is_file():
        raise HTTPException(status_code=503, detail={
            "code": "region_catalog_unavailable",
            "message": "지역 선택 목록을 읽을 수 없습니다.",
            "questions": [],
        })
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        return sorted({
            (str(row.get("자치구", "")).strip(), normalize_admin_dong_name(row.get("행정동", "")))
            for row in rows
            if str(row.get("자치구", "")).strip() and str(row.get("행정동", "")).strip()
        })


def _confirmation_questions(message: str) -> list[str]:
    prefix = "입력 확인이 필요합니다:"
    if prefix not in message:
        return []
    return [part.strip() for part in message.split(prefix, 1)[1].split(";") if part.strip()]


def _pipeline_error_detail(
    exc: PipelineError,
    request_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    raw_message = str(exc)
    if isinstance(exc, PipelineInputError):
        questions = _confirmation_questions(raw_message)
        code = "confirmation_required" if questions else "invalid_request"
        message = raw_message
    elif isinstance(exc, PipelineDependencyError):
        code = "dependency_unavailable"
        message = "추천에 필요한 외부 의존성을 사용할 수 없습니다. 잠시 후 다시 시도해 주세요."
        questions = []
    elif isinstance(exc, PipelineInternalError):
        code = "internal_validation_error"
        message = "추천 결과 내부 검증에 실패했습니다. 운영 로그를 확인해 주세요."
        questions = []
    else:
        code = "pipeline_error"
        message = "추천 파이프라인을 처리하지 못했습니다. 운영 로그를 확인해 주세요."
        questions = []
    detail: dict[str, Any] = {
        "code": code,
        "message": message,
        "questions": questions,
    }
    if request_id is not None:
        detail["request_id"] = request_id
    if run_id is not None:
        detail["run_id"] = run_id
    return detail


def _pipeline_error_status(exc: PipelineError) -> int:
    if isinstance(exc, PipelineInputError):
        return 422
    if isinstance(exc, PipelineDependencyError):
        return 503
    return 500


def _output_root() -> Path:
    configured = os.getenv("RECOMMENDATION_API_OUT_ROOT", "output/recommendation_api_runs").strip()
    path = Path(configured)
    return path if path.is_absolute() else SERVICE_ROOT / path


_RUN_ID_PATTERN = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{12}")


def _write_json_artifact(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _write_run_status(
    run_id: str,
    out_dir: Path,
    status: Literal["running", "completed", "failed"],
    **extra: Any,
) -> None:
    """Persist a small, non-sensitive marker for timeout polling."""
    payload: dict[str, Any] = {
        "run_id": run_id,
        "status": status,
        "updated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        **extra,
    }
    try:
        _write_json_artifact(out_dir / "run-status.json", payload)
    except OSError:
        # A status marker is operational metadata; it must not mask the
        # original pipeline result or prevent the worker from releasing a slot.
        logger.exception("could not persist recommendation run status run_id=%s", run_id)


def _read_json_value(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail={
            "code": "run_artifact_unavailable",
            "message": "추천 실행 결과를 읽을 수 없습니다.",
            "questions": [],
        }) from exc
    return payload


def _read_json_artifact(path: Path) -> dict[str, Any]:
    payload = _read_json_value(path)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail={
            "code": "run_artifact_invalid",
            "message": "추천 실행 결과 형식이 올바르지 않습니다.",
            "questions": [],
        })
    return payload


def _completed_run_response(run_id: str, out_dir: Path) -> dict[str, Any]:
    response_path = out_dir / "api-response.json"
    if response_path.is_file():
        response = _read_json_artifact(response_path)
    else:
        # Support artifacts created before the API response cache was added.
        required = (
            out_dir / "request.json",
            out_dir / "candidates.json",
            out_dir / "explanations.json",
            out_dir / "coverage-summary.json",
        )
        if not all(path.is_file() for path in required):
            raise HTTPException(status_code=500, detail={
                "code": "run_artifact_incomplete",
                "message": "완료된 추천 실행 결과가 불완전합니다.",
                "questions": [],
                "run_id": run_id,
            })
        request = _read_json_artifact(out_dir / "request.json")
        summary = _read_json_artifact(out_dir / "coverage-summary.json")
        response = {
            "request_id": None,
            "run_id": run_id,
            "status": "completed",
            "request": {
                "region": {
                    "sido": request.get("sido"),
                    "sigungu": request.get("sigungu"),
                    "dong": request.get("dong"),
                },
                "industry_code": request.get("industry_code"),
                "special_condition_text": request.get("special_condition_text", ""),
                "limit": summary.get("applied_limit"),
            },
            "input_interpretation": request.get("input_interpretation", {}),
            "summary": summary,
            "candidates": _read_json_value(out_dir / "candidates.json"),
            "explanations": _read_json_artifact(out_dir / "explanations.json"),
        }
    if response.get("run_id") != run_id or response.get("status") != "completed":
        raise HTTPException(status_code=500, detail={
            "code": "run_artifact_invalid",
            "message": "추천 실행 결과의 식별자가 올바르지 않습니다.",
            "questions": [],
            "run_id": run_id,
        })
    try:
        return RecommendationApiResponse.model_validate(response).model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(status_code=500, detail={
            "code": "run_artifact_invalid",
            "message": "추천 실행 결과가 API 응답 계약을 만족하지 않습니다.",
            "questions": [],
            "run_id": run_id,
        }) from exc


def _load_run_response(run_id: str) -> tuple[int, dict[str, Any]]:
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise HTTPException(status_code=404, detail={
            "code": "run_not_found",
            "message": "추천 실행을 찾을 수 없습니다.",
            "questions": [],
        })
    out_dir = _output_root() / run_id
    if not out_dir.is_dir():
        raise HTTPException(status_code=404, detail={
            "code": "run_not_found",
            "message": "추천 실행을 찾을 수 없습니다.",
            "questions": [],
        })

    # The response is atomically published before the best-effort status marker.
    # A failed marker write must not leave a completed run polling forever.
    if (out_dir / "api-response.json").is_file():
        return 200, _completed_run_response(run_id, out_dir)

    status_path = out_dir / "run-status.json"
    if status_path.is_file():
        status = _read_json_artifact(status_path)
        if status.get("status") == "running":
            return 202, {
                "run_id": run_id,
                "request_id": status.get("request_id"),
                "status": "running",
                "status_url": f"/internal/recommendations/{run_id}",
                "message": "추천 파이프라인이 아직 실행 중입니다.",
            }
        if status.get("status") == "failed":
            detail = status.get("error_detail")
            if not isinstance(detail, dict):
                detail = {
                    "code": "pipeline_error",
                    "message": "추천 파이프라인을 처리하지 못했습니다.",
                    "questions": [],
                    "run_id": run_id,
                }
            status_code = status.get("status_code", 500)
            if not isinstance(status_code, int) or not 400 <= status_code <= 599:
                status_code = 500
            raise HTTPException(status_code=status_code, detail=detail)
        if status.get("status") not in {"completed", None}:
            raise HTTPException(status_code=500, detail={
                "code": "run_status_invalid",
                "message": "추천 실행 상태를 확인할 수 없습니다.",
                "questions": [],
                "run_id": run_id,
            })

    return 200, _completed_run_response(run_id, out_dir)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "y", "on"}


def _service_config() -> ServiceConfig:
    quarter = os.getenv("RECOMMENDATION_QUARTER", DEFAULT_QUARTER).strip() or DEFAULT_QUARTER
    source = os.getenv("RECOMMENDATION_SOURCE", "db").strip() or "db"
    llm_mode = os.getenv("RECOMMENDATION_LLM_MODE", "auto").strip() or "auto"
    seed_mode = os.getenv("RECOMMENDATION_SEED_MODE", "buildings").strip() or "buildings"
    try:
        limit = int(os.getenv("RECOMMENDATION_DEFAULT_LIMIT", "5"))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail={
            "code": "invalid_service_config",
            "message": "RECOMMENDATION_DEFAULT_LIMIT은 정수여야 합니다.",
            "questions": [],
        }) from exc
    try:
        request_timeout_s = float(os.getenv("RECOMMENDATION_REQUEST_TIMEOUT_SECONDS", "180"))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail={
            "code": "invalid_service_config",
            "message": "RECOMMENDATION_REQUEST_TIMEOUT_SECONDS는 숫자여야 합니다.",
            "questions": [],
        }) from exc
    try:
        readiness_timeout_s = float(os.getenv("RECOMMENDATION_READINESS_TIMEOUT_SECONDS", "3"))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail={
            "code": "invalid_service_config",
            "message": "RECOMMENDATION_READINESS_TIMEOUT_SECONDS는 숫자여야 합니다.",
            "questions": [],
        }) from exc
    try:
        max_concurrent = int(os.getenv("RECOMMENDATION_MAX_CONCURRENT", "4"))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail={
            "code": "invalid_service_config",
            "message": "RECOMMENDATION_MAX_CONCURRENT는 정수여야 합니다.",
            "questions": [],
        }) from exc
    try:
        capacity_retry_after_s = int(os.getenv("RECOMMENDATION_CAPACITY_RETRY_AFTER_SECONDS", "10"))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail={
            "code": "invalid_service_config",
            "message": "RECOMMENDATION_CAPACITY_RETRY_AFTER_SECONDS는 정수여야 합니다.",
            "questions": [],
        }) from exc
    if (
        not re.fullmatch(r"\d{4}[1-4]", quarter)
        or source not in {"db", "files"}
        or llm_mode not in {"auto", "required", "offline"}
        or seed_mode not in {"anchors", "buildings", "hybrid"}
        or not 1 <= limit <= 50
        or not 5 <= request_timeout_s <= 900
        or not 0.1 <= readiness_timeout_s <= 30
        or not 1 <= max_concurrent <= 64
        or not 1 <= capacity_retry_after_s <= 60
    ):
        raise HTTPException(status_code=503, detail={
            "code": "invalid_service_config",
            "message": "RECOMMENDATION_QUARTER/SOURCE/LLM_MODE/SEED_MODE/DEFAULT_LIMIT/REQUEST_TIMEOUT/READINESS_TIMEOUT/MAX_CONCURRENT/CAPACITY_RETRY_AFTER 설정을 확인해 주세요.",
            "questions": [],
        })
    return ServiceConfig(
        quarter=quarter,
        source=source,  # type: ignore[arg-type]
        llm_mode=llm_mode,  # type: ignore[arg-type]
        limit=limit,
        seed_mode=seed_mode,  # type: ignore[arg-type]
        include_poi=_env_bool("RECOMMENDATION_INCLUDE_POI", False),
        include_poi_context=_env_bool("RECOMMENDATION_INCLUDE_POI_CONTEXT", False),
        include_news=_env_bool("RECOMMENDATION_INCLUDE_NEWS", True),
        request_timeout_s=request_timeout_s,
        readiness_timeout_s=readiness_timeout_s,
        max_concurrent=max_concurrent,
        capacity_retry_after_s=capacity_retry_after_s,
    )


_ACTIVE_RECOMMENDATIONS = 0
_ACTIVE_RECOMMENDATIONS_LOCK = threading.Lock()
_RECOMMENDATION_TASKS: set[asyncio.Task[dict[str, Any]]] = set()


def _recommendation_task_done(task: asyncio.Task[dict[str, Any]]) -> None:
    _RECOMMENDATION_TASKS.discard(task)
    if not task.cancelled():
        # Observe late exceptions even if the HTTP caller has timed out. The
        # worker persists the terminal error for the polling endpoint.
        task.exception()


def _try_acquire_recommendation_slot(limit: int) -> bool:
    global _ACTIVE_RECOMMENDATIONS
    with _ACTIVE_RECOMMENDATIONS_LOCK:
        if _ACTIVE_RECOMMENDATIONS >= limit:
            return False
        _ACTIVE_RECOMMENDATIONS += 1
        return True


def _release_recommendation_slot() -> None:
    global _ACTIVE_RECOMMENDATIONS
    with _ACTIVE_RECOMMENDATIONS_LOCK:
        _ACTIVE_RECOMMENDATIONS = max(0, _ACTIVE_RECOMMENDATIONS - 1)


def _run_pipeline_with_slot(
    pipeline_request: RecommendationRequest,
    out_dir: Path,
    config: ServiceConfig,
    limit: int,
    run_id: str,
    payload: PipelineRecommendationRequest,
) -> dict[str, Any]:
    """Run in the worker and release capacity only after the worker exits.

    The caller shields this task so even queued work survives HTTP timeout.
    Releasing the slot in the request coroutine would allow timed-out work
    to exceed the concurrency cap.
    """
    try:
        result = run_pipeline(
            pipeline_request,
            out_dir,
            config.include_poi,
            config.include_poi_context,
            limit,
            None,
            config.include_news,
            config.source,
            config.llm_mode,
            seed_mode=config.seed_mode,
        )
        result["summary"]["applied_limit"] = limit
        _write_json_artifact(out_dir / "api-response.json", _response_payload(run_id, payload, result))
        _write_run_status(run_id, out_dir, "completed", request_id=payload.request_id)
        return result
    except PipelineError as exc:
        _write_run_status(
            run_id,
            out_dir,
            "failed",
            request_id=payload.request_id,
            status_code=_pipeline_error_status(exc),
            error_detail=_pipeline_error_detail(exc, payload.request_id, run_id),
        )
        raise
    except Exception:
        _write_run_status(
            run_id,
            out_dir,
            "failed",
            request_id=payload.request_id,
            status_code=500,
            error_detail={
                "code": "pipeline_error",
                "message": "추천 파이프라인을 처리하지 못했습니다.",
                "questions": [],
                "request_id": payload.request_id,
                "run_id": run_id,
            },
        )
        raise
    finally:
        _release_recommendation_slot()


def _new_run_dir() -> tuple[str, Path]:
    run_id = f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"
    return run_id, _output_root() / run_id


def _pipeline_request(payload: PipelineRecommendationRequest, config: ServiceConfig) -> RecommendationRequest:
    return RecommendationRequest(
        sido=payload.region.sido,
        sigungu=payload.region.sigungu,
        dong=payload.region.dong,
        industry_code=payload.industry_code,
        special_condition_text=payload.special_condition_text,
        quarter=config.quarter,
        sigungu_code=payload.region.sigungu_code,
        admin_dong_code=payload.region.admin_dong_code,
    )


def _response_payload(run_id: str, payload: PipelineRecommendationRequest, result: dict[str, Any]) -> dict[str, Any]:
    request = {
        "region": {
            "sido": payload.region.sido,
            "sigungu": payload.region.sigungu,
            "dong": payload.region.dong,
        },
        "industry_code": result["input_interpretation"].get("resolved_industry_code") or payload.industry_code,
        "special_condition_text": payload.special_condition_text,
        "limit": result["summary"].get("applied_limit"),
    }
    # Do not expose the local filesystem path. The run is already persisted
    # under RECOMMENDATION_API_OUT_ROOT for later operational retrieval.
    return {
        "request_id": payload.request_id,
        "run_id": run_id,
        "status": "completed",
        "request": request,
        "input_interpretation": result["input_interpretation"],
        "summary": result["summary"],
        "candidates": result["candidates"],
        "explanations": result["explanations"],
    }


app = FastAPI(
    title="Seoul Site Recommendation API",
    version="0.1.0",
    description="LLM-assisted input interpretation with deterministic, Evidence-first recommendation output.",
)

cors_origins = [item.strip() for item in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if item.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )


@app.get("/healthz", tags=["system"])
async def healthz() -> dict[str, str]:
    return {"status": "ok", "pipeline": "recommendation_pipeline_v2_llm_input"}


@app.get("/readyz", tags=["system"])
async def readyz() -> dict[str, Any]:
    checks: dict[str, bool] = {
        "schema_present": EVIDENCE_SCHEMA_PATH.is_file(),
        "region_catalog_present": REGION_CATALOG_PATH.is_file(),
    }
    config = _service_config()
    if config.source == "db":
        try:
            from recommendation.serving_db import ping

            await asyncio.wait_for(
                run_in_threadpool(ping, config.readiness_timeout_s),
                timeout=config.readiness_timeout_s,
            )
            checks["db"] = True
        except Exception:  # readiness must never leak connection details
            checks["db"] = False
    else:
        checks["db"] = True
    if not all(checks.values()):
        raise HTTPException(status_code=503, detail={"ok": False, "checks": checks})
    return {"ok": True}


@app.get("/api/industries", dependencies=[Depends(verify_internal_token)], tags=["catalog"])
async def industries() -> list[dict[str, str]]:
    """Return the finite industry list used by the input contract."""
    return [{"code": code, "name": INDUSTRY_NAMES[code]} for code in sorted(INDUSTRY_NAMES)]


@app.get("/api/regions", dependencies=[Depends(verify_internal_token)], tags=["catalog"])
async def regions(
    sigungu: str | None = Query(default=None, max_length=40),
) -> dict[str, Any]:
    """Return Seoul district and dependent administrative-dong options."""
    options = await run_in_threadpool(_region_options)
    sigungus = sorted({gu for gu, _ in options})
    dongs = sorted({dong for gu, dong in options if not sigungu or gu == sigungu})
    return {"sido": "서울특별시", "sigungu": sigungus, "dong": dongs}


@app.post(
    "/internal/recommendations",
    response_model=RecommendationApiResponse,
    dependencies=[Depends(verify_internal_token)],
    tags=["pipeline"],
)
@app.post(
    "/api/recommendations",
    response_model=RecommendationApiResponse,
    include_in_schema=False,
    dependencies=[Depends(verify_internal_token)],
    tags=["pipeline"],
)
async def create_recommendation(
    payload: PipelineRecommendationRequest,
) -> dict[str, Any]:
    """Run the complete recommendation pipeline for one backend request."""
    _validate_common_input(payload)
    config = _service_config()
    applied_limit = payload.limit if payload.limit is not None else config.limit
    if not _try_acquire_recommendation_slot(config.max_concurrent):
        raise HTTPException(status_code=429, detail={
            "code": "recommendation_capacity_exceeded",
            "message": "동시 추천 요청 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.",
            "questions": [],
        }, headers={"Retry-After": str(config.capacity_retry_after_s)})

    run_id, out_dir = _new_run_dir()
    _write_run_status(run_id, out_dir, "running", request_id=payload.request_id)
    task = asyncio.create_task(run_in_threadpool(
        _run_pipeline_with_slot,
        _pipeline_request(payload, config),
        out_dir,
        config,
        applied_limit,
        run_id,
        payload,
    ))
    # Keep a strong reference after the request exits, including while waiting
    # for threadpool capacity. Timeout must not cancel work that owns a slot.
    _RECOMMENDATION_TASKS.add(task)
    task.add_done_callback(_recommendation_task_done)
    try:
        result = await asyncio.wait_for(
            asyncio.shield(task),
            timeout=config.request_timeout_s,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail={
            "code": "pipeline_timeout",
            "message": "추천 파이프라인 실행 시간이 제한을 초과했습니다.",
            "questions": [],
            "request_id": payload.request_id,
            "run_id": run_id,
            "status_url": f"/internal/recommendations/{run_id}",
        }) from exc
    except PipelineError as exc:
        if isinstance(exc, PipelineInputError):
            logger.info(
                "recommendation input rejected request_id=%s run_id=%s reason=%s",
                payload.request_id,
                run_id,
                exc,
            )
        else:
            logger.exception(
                "recommendation pipeline failed request_id=%s run_id=%s",
                payload.request_id,
                run_id,
            )
        raise HTTPException(
            status_code=_pipeline_error_status(exc),
            detail=_pipeline_error_detail(exc, payload.request_id, run_id),
        ) from exc

    result["summary"]["applied_limit"] = applied_limit
    return _response_payload(run_id, payload, result)


@app.get(
    "/internal/recommendations/{run_id}",
    response_model=None,
    dependencies=[Depends(verify_internal_token)],
    tags=["pipeline"],
)
async def get_recommendation_run(run_id: str) -> Any:
    """Return a completed run or a pollable in-progress status."""
    status_code, response = await run_in_threadpool(_load_run_response, run_id)
    if status_code == 202:
        return JSONResponse(status_code=202, content=response)
    return response


__all__ = ["app"]
