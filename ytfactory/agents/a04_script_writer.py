"""A4 Script Writer (S06) — tier: writer."""
from __future__ import annotations

from ytfactory.agents.base import run_agent
from ytfactory.schemas import ScriptDraftSchema

TIER = "writer"
PROMPT_FILE = "a04_script_writer.md"


def run(router, payload: dict, stub: dict) -> ScriptDraftSchema:
    return run_agent(
        router,
        agent_id="A4",
        tier=TIER,
        prompt_file=PROMPT_FILE,
        payload=payload,
        schema=ScriptDraftSchema,
        stub=stub,
    )
