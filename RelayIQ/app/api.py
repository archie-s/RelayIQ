"""Standalone RelayIQ Draft 4 placement API."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from .classification import JudgeAssessment, JudgeClassification, SensitiveDataTypes
from .placement import (
    FallbackAction,
    PlacementContextV2,
    PlacementDecisionV2,
    PlacementEngine,
    PlacementServiceUnavailableError,
    TargetClass,
    V2ReasonCode,
)
from .policy import PlacementPolicySnapshot, load_placement_policy

POLICY_CONFIG_ENV = "RELAYIQ_POLICY_CONFIG"
_logger = logging.getLogger(__name__)
NonBlankString = Annotated[str, Field(min_length=1, pattern=r"\S")]
NonEmptyString = Annotated[str, Field(min_length=1)]


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SuggestedModelsResponse(StrictRequestModel):
    local: list[NonBlankString] = Field(
        json_schema_extra={"uniqueItems": True},
    )
    cloud: list[NonBlankString] = Field(
        json_schema_extra={"uniqueItems": True},
    )

    @field_validator("local", "cloud")
    @classmethod
    def validate_model_names(cls, value: list[str]) -> list[str]:
        if any(not name.strip() for name in value):
            raise ValueError("model names must contain a non-whitespace character")
        if len(set(value)) != len(value):
            raise ValueError("model names must be unique within each lane")
        return value


class ModelMappingRequest(SuggestedModelsResponse):
    local: list[NonBlankString] = Field(
        default_factory=list, json_schema_extra={"uniqueItems": True},
    )
    cloud: list[NonBlankString] = Field(
        default_factory=list, json_schema_extra={"uniqueItems": True},
    )


class PlacementContextRequestV2(StrictRequestModel):
    connectivity: StrictBool = True
    cloud_budget_kb: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    required_capabilities: list[NonEmptyString] = Field(default_factory=list)


class PlacementRequestV2(StrictRequestModel):
    input: str = Field(min_length=1, max_length=100_000, pattern=r"\S")
    preferred_target_class: (
        Literal["local", "cloud"] | SkipJsonSchema[None]
    ) = None
    model_mapping: ModelMappingRequest | SkipJsonSchema[None] = Field(
        default=None,
        description="If omitted, suggested_models is omitted from every response, including fallback.",
    )
    context: PlacementContextRequestV2 = Field(default_factory=PlacementContextRequestV2)

    @model_validator(mode="after")
    def reject_explicit_null_optionals(self) -> "PlacementRequestV2":
        for field_name in ("preferred_target_class", "model_mapping"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} must be omitted rather than null")
        return self


class PlacementRequirementsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    consent_required: bool


class PlacementResponseV2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {
                            "reason_codes": {
                                "contains": {"const": "CLASSIFIER_DEGRADED"},
                            },
                        },
                    },
                    "then": {
                        "not": {
                            "anyOf": [
                                {"required": ["classification"]},
                                {"required": ["sensitive_data_types"]},
                                {
                                    "properties": {
                                        "reason_codes": {
                                            "contains": {"const": "SENSITIVE_DATA_DETECTED"},
                                        },
                                    },
                                },
                            ],
                        },
                    },
                    "else": {"required": ["classification"]},
                },
                {
                    "if": {
                        "properties": {
                            "reason_codes": {
                                "contains": {"const": "SENSITIVE_DATA_DETECTED"},
                            },
                        },
                    },
                    "then": {
                        "required": ["classification", "sensitive_data_types"],
                        "properties": {
                            "classification": {
                                "properties": {
                                    "sensitive_data": {"const": True},
                                },
                            },
                        },
                    },
                    "else": {
                        "not": {"required": ["sensitive_data_types"]},
                        "properties": {
                            "classification": {
                                "properties": {
                                    "sensitive_data": {"const": False},
                                },
                            },
                        },
                    },
                },
                {
                    "if": {
                        "properties": {
                            "target_class": {"const": "cloud_with_consent"},
                        },
                    },
                    "then": {
                        "properties": {
                            "requirements": {
                                "properties": {
                                    "consent_required": {"const": True},
                                },
                            },
                        },
                    },
                    "else": {
                        "properties": {
                            "requirements": {
                                "properties": {
                                    "consent_required": {"const": False},
                                },
                            },
                        },
                    },
                },
                {
                    "if": {
                        "properties": {
                            "target_class": {"const": "fallback"},
                        },
                    },
                    "then": {
                        "properties": {
                            "suggested_models": {
                                "properties": {
                                    "local": {"maxItems": 0},
                                    "cloud": {"maxItems": 0},
                                },
                            },
                        },
                    },
                    "else": {
                        "if": {
                            "properties": {"target_class": {"const": "local"}},
                        },
                        "then": {
                            "properties": {
                                "suggested_models": {
                                    "properties": {"local": {"minItems": 1}},
                                },
                            },
                        },
                        "else": {
                            "properties": {
                                "suggested_models": {
                                    "properties": {"cloud": {"minItems": 1}},
                                },
                            },
                        },
                    },
                },
                {
                    "if": {
                        "properties": {
                            "reason_codes": {
                                "contains": {"const": "POLICY_BLOCKED"},
                            },
                        },
                    },
                    "then": {
                        "properties": {
                            "target_class": {"const": "fallback"},
                            "fallback_action": {"const": "stop"},
                        },
                    },
                },
            ],
        },
    )
    decision_id: UUID
    target_class: TargetClass
    policy_revision: str = Field(min_length=1, pattern=r"\S")
    requirements: PlacementRequirementsResponse
    fallback_action: FallbackAction
    reason_codes: list[V2ReasonCode] = Field(
        min_length=1, json_schema_extra={"uniqueItems": True},
    )
    classification: JudgeClassification | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            "Required for healthy classification; omitted for CLASSIFIER_DEGRADED. "
            "The current relayiq-judge-v1 Judge detects email addresses only for "
            "sensitive_data. Consent and caller redaction remain independent."
        ),
    )
    sensitive_data_types: SensitiveDataTypes | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            "Extensible category labels, never detected values. The current "
            "relayiq-judge-v1 Judge emits only ['email_address'] for a positive "
            "email finding. Omitted for negative or degraded classification."
        ),
    )
    suggested_models: SuggestedModelsResponse | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            "Omitted entirely if the request omits model_mapping, not null or []. "
            "With a mapping, returns both local and cloud lists in request order, "
            "regardless of the selected lane. Missing request lanes become []. "
            "For fallback both lists are empty. Inventory does not authorize execution."
        ),
    )
    explanation: str | SkipJsonSchema[None] = None

    @model_validator(mode="after")
    def validate_contract_invariants(self) -> "PlacementResponseV2":
        requirements = self.requirements
        for name in (
            "classification", "sensitive_data_types", "suggested_models", "explanation",
        ):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} must be omitted rather than null")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("reason_codes must be unique")
        if V2ReasonCode.CLASSIFIER_DEGRADED in self.reason_codes:
            if (
                self.classification is not None
                or self.sensitive_data_types is not None
                or V2ReasonCode.SENSITIVE_DATA_DETECTED in self.reason_codes
            ):
                raise ValueError("degraded classification must omit assessment findings")
        else:
            assessment_fields: dict[str, object] = {"classification": self.classification}
            if "sensitive_data_types" in self.model_fields_set:
                assessment_fields["sensitive_data_types"] = self.sensitive_data_types
            assessment = JudgeAssessment.model_validate(assessment_fields)
            if assessment.classification.sensitive_data != (
                V2ReasonCode.SENSITIVE_DATA_DETECTED in self.reason_codes
            ):
                raise ValueError("sensitive_data must match SENSITIVE_DATA_DETECTED")
        expected_consent = self.target_class == TargetClass.CLOUD_WITH_CONSENT
        if requirements.consent_required != expected_consent:
            raise ValueError(
                "consent_required must match the cloud_with_consent target"
            )
        if "POLICY_BLOCKED" in self.reason_codes and (
            self.target_class != TargetClass.FALLBACK
            or self.fallback_action != FallbackAction.STOP
        ):
            raise ValueError("policy blocks require fallback/stop")
        if self.suggested_models is not None:
            models = self.suggested_models
            if self.target_class == TargetClass.FALLBACK:
                if models.local or models.cloud:
                    raise ValueError("fallback decisions require two empty model lists")
            elif not (
                models.local if self.target_class == TargetClass.LOCAL else models.cloud
            ):
                raise ValueError("the recommended lane must contain a model")
        return self


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
    """Wrap unexpected v2 endpoint failures without exposing request content."""


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


def _policy_from_setting(
    policy_config_path: str | Path | None,
) -> PlacementPolicySnapshot | None:
    configured_path = policy_config_path or os.getenv(POLICY_CONFIG_ENV)
    return load_placement_policy(configured_path) if configured_path else None


def create_app(policy_config_path: str | Path | None = None) -> FastAPI:
    policy_snapshot = _policy_from_setting(policy_config_path)
    placement_engine = PlacementEngine()
    api = FastAPI(title="RelayIQ Placement API", version="2-draft4")
    api.state.policy_snapshot = policy_snapshot
    api.state.placement_engine = placement_engine

    @api.exception_handler(RequestValidationError)
    async def invalid_request_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        _logger.info("Rejected invalid v2 placement request")
        return JSONResponse(
            status_code=400,
            content={
                "error": "INVALID_REQUEST",
                "message": _validation_message(exc),
                "decision_id": None,
            },
        )

    @api.exception_handler(PlacementServiceUnavailableError)
    async def service_unavailable_handler(
        request: Request,
        exc: PlacementServiceUnavailableError,
    ) -> JSONResponse:
        _logger.warning("V2 placement service unavailable")
        return JSONResponse(
            status_code=503,
            content={
                "error": "SERVICE_UNAVAILABLE",
                "message": "The placement service is temporarily unavailable.",
                "decision_id": None,
            },
        )

    @api.exception_handler(PlacementInternalError)
    async def internal_error_handler(
        request: Request,
        exc: PlacementInternalError,
    ) -> JSONResponse:
        cause = exc.__cause__
        _logger.error(
            "V2 placement request failed",
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

    @api.post(
        "/v2/placement/decide",
        response_model=PlacementResponseV2,
        response_model_exclude_unset=True,
        responses={
            400: {"model": InvalidRequestResponse, "description": "Invalid request"},
            500: {
                "model": InternalErrorResponse,
                "description": "Policy unavailable, policy evaluation failed, or internal error",
            },
            503: {
                "model": ServiceUnavailableResponse,
                "description": "Service unavailable",
            },
        },
    )
    async def decide_placement(request: PlacementRequestV2) -> PlacementResponseV2:
        try:
            mapping = (
                {
                    "local": tuple(request.model_mapping.local),
                    "cloud": tuple(request.model_mapping.cloud),
                }
                if request.model_mapping is not None
                else None
            )
            decision: PlacementDecisionV2 = await asyncio.to_thread(
                placement_engine.decide_v2,
                request.input,
                PlacementContextV2(
                    connectivity=request.context.connectivity,
                    cloud_budget_kb=request.context.cloud_budget_kb,
                    required_capabilities=tuple(
                        request.context.required_capabilities
                    ),
                ),
                preferred_target_class=(
                    TargetClass(request.preferred_target_class)
                    if request.preferred_target_class is not None
                    else None
                ),
                model_mapping=mapping,
                policy_snapshot=policy_snapshot,
            )
            _logger.info(
                "V2 placement decision completed",
                extra={
                    "decision_id": str(decision.decision_id),
                    "policy_revision": decision.policy_revision,
                    "target_class": decision.target_class.value,
                    "reason_codes": [
                        reason.value for reason in decision.reason_codes
                    ],
                    "timing_ms": decision.timings_ms["total"],
                },
            )
            response = PlacementResponseV2(**decision.to_public_dict())
            if ("suggested_models" in response.model_fields_set) != (mapping is not None):
                raise ValueError("suggested_models presence must match request model_mapping")
            return response
        except PlacementServiceUnavailableError:
            raise
        except Exception as exc:
            raise PlacementInternalError from exc

    def openapi_schema() -> dict:
        if api.openapi_schema:
            return api.openapi_schema
        schema = get_openapi(title=api.title, version=api.version, routes=api.routes)
        schema["paths"]["/v2/placement/decide"]["post"]["responses"].pop("422", None)
        api.openapi_schema = schema
        return schema

    api.openapi = openapi_schema
    return api


app = create_app()
