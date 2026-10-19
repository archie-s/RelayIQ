"""Shared validation for the Judge's public placement assessment."""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema


SensitiveDataType = Annotated[
    str, Field(pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
]
SensitiveDataTypes = Annotated[
    list[SensitiveDataType], Field(min_length=1, json_schema_extra={"uniqueItems": True})
]


class JudgeClassification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    routine: bool
    fresh_knowledge: bool
    reasoning_heavy: bool
    critical: bool
    sensitive_data: bool


class JudgeAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    classification: JudgeClassification
    sensitive_data_types: SensitiveDataTypes | SkipJsonSchema[None] = None

    @model_validator(mode="after")
    def validate_sensitive_data(self) -> "JudgeAssessment":
        present = "sensitive_data_types" in self.model_fields_set
        if present and self.sensitive_data_types is None:
            raise ValueError("sensitive_data_types must be omitted rather than null")
        if self.classification.sensitive_data != present:
            raise ValueError("sensitive_data_types must match sensitive_data")
        if self.sensitive_data_types is not None and (
            len(self.sensitive_data_types) != len(set(self.sensitive_data_types))
        ):
            raise ValueError("sensitive_data_types must contain unique categories")
        return self
