"""
Pydantic models = the contract between the browser and the server.

Anything that comes in from the outside world is validated here, before a
single line of business logic runs.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import MAX_GOAL_LENGTH


class CreateRunRequest(BaseModel):
    """Body of POST /runs."""

    goal: str = Field(..., min_length=1, max_length=MAX_GOAL_LENGTH)

    @field_validator("goal")
    @classmethod
    def goal_must_not_be_blank(cls, value: str) -> str:
        """"   " is technically 3 characters long, but it is not a goal."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("goal must not be empty")
        return cleaned


class StepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step_number: int
    name: str
    status: str
    detail: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    goal: str
    status: str
    current_step: int
    max_steps: int
    credits_used: int
    output: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    steps: List[StepResponse] = []


class ErrorResponse(BaseModel):
    """Every error the API returns has this shape - never a stack trace."""

    error: str
    message: str
