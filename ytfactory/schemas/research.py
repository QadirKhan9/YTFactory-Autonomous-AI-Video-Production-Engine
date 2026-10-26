"""Research / outline / critique / fact-check schemas (A1, A2, A3, A5, A6)."""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field, model_validator


class ResearchPlanSchema(BaseModel):
    sub_questions: List[str] = Field(min_length=1)
    search_queries: List[str] = Field(min_length=1)
    source_types: List[str] = Field(default_factory=lambda: ["peer_reviewed", "gov", "institution"])


class Fact(BaseModel):
    text: str = Field(..., min_length=8, description="Atomic factual statement")
    source_id: int = Field(..., ge=1, description="Row id in sources")
    char_span: str = Field(..., pattern=r"^\d+-\d+$", description="char offsets in source text")
    confidence: float = Field(..., ge=0.0, le=1.0)


class EvidenceExtractSchema(BaseModel):
    facts: List[Fact] = Field(min_length=1)


class Beat(BaseModel):
    title: str
    summary: str


class OutlineSchema(BaseModel):
    hook: str = Field(..., min_length=10)
    thesis: str
    beats: List[Beat] = Field(min_length=5)
    open_loops: List[str] = Field(default_factory=list)
    knowledge_gaps: List[str] = Field(default_factory=list)
    target_wpm: int = Field(140, ge=90, le=200)

    @model_validator(mode="after")
    def check_open_loops(self) -> "OutlineSchema":
        if not self.open_loops:
            raise ValueError("outline must define at least one open loop (FR-S-01)")
        return self


class CritiqueSchema(BaseModel):
    retention_risks: List[str] = Field(default_factory=list)
    rewrite_notes: List[str] = Field(default_factory=list)
    hook_score: float = Field(..., ge=0.0, le=10.0)
    score: float = Field(..., ge=0.0, le=10.0)


class ClaimVerdict(BaseModel):
    claim_key: str = Field(..., pattern=r"^C\d+$", description="claim_map key, e.g. C1")
    verdict: Literal["supported", "partial", "unsupported"]
    span: str = Field("", description="Supporting span in evidence")
    note: str = ""


class FactCheckReport(BaseModel):
    verdicts: List[ClaimVerdict] = Field(min_length=1)
