"""Schemas for the Demo Learning Guide."""

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class PersonaOut(BaseModel):
    key: str
    email: str
    full_name: str
    role: str
    title: str
    responsibilities: str
    active: bool


class ExerciseOut(BaseModel):
    id: str
    kind: str  # "state" (do something) | "answer" (find something)
    completed: bool
    facts: dict[str, Any]


class StatementOut(BaseModel):
    source_system: str
    record_count: int
    filename: str


class GuideOut(BaseModel):
    scenario: str
    organization_name: str
    as_of: date
    approval_threshold_minor: int
    statement: StatementOut
    ai_available: bool
    personas: list[PersonaOut]
    exercises: list[ExerciseOut]
    completed_count: int
    total_count: int


class VerifyRequest(BaseModel):
    answer: str | None = Field(default=None, max_length=200)


class VerifyOut(BaseModel):
    exercise_id: str
    passed: bool
    message: str
