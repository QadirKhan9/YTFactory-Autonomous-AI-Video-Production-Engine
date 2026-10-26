"""A9 QC Reviewer output schema (S20 subjective editorial review)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DIMENSIONS = ("pacing", "tone", "hook", "visual")


class Finding(BaseModel):
    dimension: Literal["pacing", "tone", "hook", "visual"]
    message: str = Field(min_length=1, max_length=300)


class QcReviewSchema(BaseModel):
    """FR-Q-03: subjective review result — blockers halt the pipeline (user decision)."""

    blockers: list[Finding] = Field(default_factory=list, max_length=20)
    warnings: list[Finding] = Field(default_factory=list, max_length=40)
    notes: list[str] = Field(default_factory=list, max_length=40)
