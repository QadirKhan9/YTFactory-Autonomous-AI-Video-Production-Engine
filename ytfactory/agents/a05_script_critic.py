"""A5 Script Critic (S07) — tier: writer, MUST run on a different model family
than A4 (decorrelation enforced via forbid_family at dispatch)."""
from __future__ import annotations

from ytfactory.agents.base import run_agent
from ytfactory.schemas import CritiqueSchema

TIER = "writer"
PROMPT_FILE = "a05_script_critic.md"
STUB = {
    "retention_risks": ["Beat 3 dips in energy before the sleep payoff."],
    "rewrite_notes": ["Tighten the opening of beat 3; state the stake earlier."],
    "hook_score": 8.0,
    "score": 7.5,
}


def run(router, payload: dict, stub: dict | None = None, forbid_family: str | None = None) -> CritiqueSchema:
    return run_agent(
        router,
        agent_id="A5",
        tier=TIER,
        prompt_file=PROMPT_FILE,
        payload=payload,
        schema=CritiqueSchema,
        stub=stub if stub is not None else STUB,
        forbid_family=forbid_family,
    )
