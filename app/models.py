from __future__ import annotations

import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RequestStatus(StrEnum):
    PENDING = "pending"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"


class RequestCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    url: str
    method: str = "GET"
    body: dict[str, object] | list[object] | None = None
    max_retries: int = Field(default=5, alias="maxRetries")
    backoff_ms: int = Field(default=1000, alias="backoffMs")

    @field_validator("body", mode="before")
    @classmethod
    def validate_body_is_json(cls, v: object) -> object:
        if v is None:
            return None
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                msg = f"body must be valid JSON: {e}"
                raise ValueError(msg) from e
        if isinstance(v, (dict, list)):
            return v
        msg = "body must be a JSON object, array, or JSON string"
        raise ValueError(msg)

    def body_as_str(self) -> str | None:
        if self.body is None:
            return None
        return json.dumps(self.body)


class RequestResponse(BaseModel):
    id: str
    status: RequestStatus


class AttemptOut(BaseModel):
    attempt_number: int
    status_code: int | None = None
    response_body: str | None = None
    error: str | None = None
    started_at: str
    duration_ms: float | None = None


class RequestOut(BaseModel):
    id: str
    url: str
    method: str
    body: str | None = None
    max_retries: int
    backoff_ms: int
    status: RequestStatus
    attempt_count: int
    next_retry_at: str | None = None
    last_error: str | None = None
    result: str | None = None
    created_at: str
    updated_at: str
    attempts: list[AttemptOut] = []
