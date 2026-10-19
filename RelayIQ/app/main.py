"""FastAPI app: serves the UI and a WebSocket that streams live scheduler state."""
from __future__ import annotations
import asyncio
import json
import logging
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from pydantic.json_schema import SkipJsonSchema

from . import config
from .placement import (
    Fallback,
    Placement,
    PlacementContext,
    PlacementEngine,
    PlacementServiceUnavailableError,
    ReasonCode,
)
from .scheduler import Scheduler

STATIC = Path(__file__).resolve().parent.parent / "static"
_logger = logging.getLogger(__name__)

app = FastAPI(title=config.BRAND_NAME)


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PlacementContextRequest(StrictRequestModel):
    connectivity: StrictBool | SkipJsonSchema[None] = None
    cloud_budget_kb: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    required_capabilities: list[str] = Field(default_factory=list)
    available_local: list[str] = Field(default_factory=list)
    available_cloud: list[str] = Field(default_factory=list)

    @field_validator("connectivity")
    @classmethod
    def connectivity_must_be_boolean_when_present(cls, value: bool | None) -> bool:
        if value is None:
            raise ValueError("must be a boolean when provided")
        return value


class PlacementRequest(StrictRequestModel):
    input: str = Field(min_length=1, max_length=100_000)
    context: PlacementContextRequest = Field(default_factory=PlacementContextRequest)


class PlacementResponse(BaseModel):
    decision_id: UUID
    placement: Placement
    policy: str
    permitted_payload: str
    requires_consent: bool
    fallback: Fallback
    suggested_models: list[str]
    reason_codes: list[ReasonCode] = Field(min_length=1)
    explanation: str


class InvalidRequestResponse(BaseModel):
    error: Literal["INVALID_REQUEST"]
    message: str
    decision_id: None


class InternalErrorResponse(BaseModel):
    error: Literal["INTERNAL_ERROR"]
    message: str
    decision_id: None


class ServiceUnavailableResponse(BaseModel):
    error: Literal["SERVICE_UNAVAILABLE"]
    message: str
    decision_id: None


class PlacementInternalError(RuntimeError):
    """Wrap unexpected failures from the placement endpoint."""


def _validation_message(exc: RequestValidationError) -> str:
    error = exc.errors()[0]
    error_type = error["type"]
    location = ".".join(str(part) for part in error["loc"] if part != "body")
    if error_type == "json_invalid":
        return "Malformed JSON request body."
    if error_type == "missing":
        return f"Missing required field: {location}."
    if error_type == "extra_forbidden":
        return f"Unknown field: {location}."
    label = location or "request body"
    return f"Invalid value for {label}: {error['msg']}."


@app.exception_handler(RequestValidationError)
async def invalid_request_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": "INVALID_REQUEST",
            "message": _validation_message(exc),
            "decision_id": None,
        },
    )


@app.exception_handler(PlacementServiceUnavailableError)
async def service_unavailable_handler(
    request: Request,
    exc: PlacementServiceUnavailableError,
) -> JSONResponse:
    _logger.warning("Placement service unavailable")
    return JSONResponse(
        status_code=503,
        content={
            "error": "SERVICE_UNAVAILABLE",
            "message": "The placement service is temporarily unavailable.",
            "decision_id": None,
        },
    )


@app.exception_handler(PlacementInternalError)
async def internal_error_handler(
    request: Request,
    exc: PlacementInternalError,
) -> JSONResponse:
    cause = exc.__cause__
    _logger.error(
        "Unhandled placement API error",
        extra={"failure_type": type(cause).__name__ if cause else "unknown"},
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_ERROR",
            "message": "The placement service could not complete the request.",
            "decision_id": None,
        },
    )

_clients: set[WebSocket] = set()

DEMO_BATCH = [
    # privacy gate + emergency: approval appears immediately while local guidance runs
    "Emergency: my colleague employee id EMP-77102 collapsed, phone 555-234-8811. He has a known heart condition. What do I do right now?",
    # T1 critical + cloud: routed to cloud, high priority
    "I have watery diarrhea and vomiting since morning, could it be cholera and what do I do now?",
    # T2 critical + cloud: medication safety
    "Can I take ibuprofen together with my blood pressure medication?",
    # local: free, no cloud needed
    "Explain how quadratic equations work with a simple example.",
    # local: free, no cloud needed
    "How should I store maize to prevent spoilage?",
    # privacy gate demo: PII detected; placement determines whether review is required
    "Summarize: employee id EMP-20431 met goals; salary is appropriate.",
    # local: casual
    "Tell me a short funny joke to cheer up my kid.",
]


async def _broadcast() -> None:
    if not _clients:
        return
    payload = json.dumps(scheduler.snapshot())
    dead = []
    for ws in _clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _clients.discard(ws)


scheduler = Scheduler(_broadcast)
placement_engine = PlacementEngine()


@app.on_event("startup")
async def _startup():
    scheduler.start()


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.post(
    "/v1/placement/decide",
    response_model=PlacementResponse,
    responses={
        400: {"model": InvalidRequestResponse, "description": "Invalid request"},
        500: {"model": InternalErrorResponse, "description": "Internal error"},
        503: {"model": ServiceUnavailableResponse, "description": "Service unavailable"},
    },
)
async def decide_placement(request: PlacementRequest) -> PlacementResponse:
    try:
        context = request.context
        connectivity = (
            context.connectivity
            if "connectivity" in context.model_fields_set
            else scheduler.connectivity
        )
        cloud_budget_kb = (
            context.cloud_budget_kb
            if "cloud_budget_kb" in context.model_fields_set
            else max(0.0, scheduler.budget_kb - scheduler.cloud_kb_used)
        )
        decision = await asyncio.to_thread(
            placement_engine.decide,
            request.input,
            PlacementContext(
                connectivity=connectivity,
                cloud_budget_kb=cloud_budget_kb,
                required_capabilities=tuple(context.required_capabilities),
                available_local=tuple(context.available_local),
                available_cloud=tuple(context.available_cloud),
            ),
        )
        return PlacementResponse(**decision.to_public_dict())
    except PlacementServiceUnavailableError:
        raise
    except Exception as exc:
        raise PlacementInternalError from exc


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    _clients.add(websocket)
    await websocket.send_text(json.dumps(scheduler.snapshot()))
    try:
        while True:
            msg = json.loads(await websocket.receive_text())
            t = msg.get("type")
            if t == "ask":
                text = (msg.get("text") or "").strip()
                if text:
                    await scheduler.submit(text)
            elif t == "demo":
                for q in DEMO_BATCH:
                    await scheduler.submit(q)
            elif t == "set_conn":
                await scheduler.set_connectivity(bool(msg.get("value")))
            elif t == "set_budget":
                await scheduler.set_budget(float(msg.get("kb", 40)))
            elif t == "reset":
                await scheduler.reset()
            elif t == "review":
                await scheduler.review(int(msg.get("job_id", 0)), bool(msg.get("allow_cloud")))
    except WebSocketDisconnect:
        _clients.discard(websocket)
    except Exception:
        _clients.discard(websocket)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _openapi_schema() -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    schema["paths"]["/v1/placement/decide"]["post"]["responses"].pop("422", None)
    app.openapi_schema = schema
    return schema


app.openapi = _openapi_schema
