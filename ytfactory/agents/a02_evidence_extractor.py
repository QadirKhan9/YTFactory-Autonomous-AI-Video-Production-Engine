"""A2 Evidence Extractor (S04) — tier: cheap."""
from __future__ import annotations

from ytfactory.agents.base import run_agent
from ytfactory.schemas import EvidenceExtractSchema

TIER = "cheap"
PROMPT_FILE = "a02_evidence_extractor.md"


def run(router, payload: dict, stub: dict) -> EvidenceExtractSchema:
    return run_agent(
        router,
        agent_id="A2",
        tier=TIER,
        prompt_file=PROMPT_FILE,
        payload=payload,
        schema=EvidenceExtractSchema,
        stub=stub,
    )
