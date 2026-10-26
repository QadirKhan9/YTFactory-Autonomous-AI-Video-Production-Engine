"""A9 QC Reviewer Agent (S20) — tier: writer (PRD architecture row)."""
from __future__ import annotations

from ytfactory.agents.base import run_agent
from ytfactory.schemas import QcReviewSchema

TIER = "writer"
PROMPT_FILE = "a09_qc_reviewer.md"
STUB = {
    "blockers": [],
    "warnings": [
        {
            "dimension": "pacing",
            "message": "Sample warning: one scene drags longer than the rest.",
        }
    ],
    "notes": ["Stub review — dry-run only."],
}


def run(router, payload: dict, stub: dict | None = None) -> QcReviewSchema:
    return run_agent(
        router,
        agent_id="A9",
        tier=TIER,
        prompt_file=PROMPT_FILE,
        payload=payload,
        schema=QcReviewSchema,
        stub=stub if stub is not None else STUB,
    )
