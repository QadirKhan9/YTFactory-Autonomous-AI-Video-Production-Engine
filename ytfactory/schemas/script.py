"""Script & fact-grounding schemas (design.md §2.1).

Grounding rule (FR-S-02/03, AC-05): a non-rhetorical sentence MUST carry at
least one claim_id. In Pydantic v2 this needs @model_validator(mode="after");
a bare method would never run.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class SentenceSchema(BaseModel):
    ord: int = Field(..., ge=1, description="1-based sequence index")
    text: str = Field(..., min_length=2, description="Sentence text")
    is_rhetorical: bool = Field(False, description="True if sentence contains no factual assertions")
    claim_ids: List[str] = Field(default_factory=list, description="IDs of claims supporting this sentence")
    emphasis: Optional[Literal["strong", "soft", "fast", "slow"]] = None

    @model_validator(mode="after")
    def validate_grounding(self) -> "SentenceSchema":
        if not self.is_rhetorical and not self.claim_ids:
            raise ValueError(f"Sentence {self.ord} is marked factual but carries no claim_ids")
        return self


class ScriptDraftSchema(BaseModel):
    sentences: List[SentenceSchema] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sequence(self) -> "ScriptDraftSchema":
        ords = [s.ord for s in self.sentences]
        if ords != sorted(ords):
            raise ValueError("sentence ordinals must be ascending")
        if len(set(ords)) != len(ords):
            raise ValueError("sentence ordinals must be unique")
        return self
