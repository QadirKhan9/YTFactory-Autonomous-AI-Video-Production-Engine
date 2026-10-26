"""A6 Fact Checker (S08) — tier: verifier, MUST run on a different model family
than A4 (FR-F-02; decorrelation enforced via forbid_family at dispatch)."""
from __future__ import annotations

from ytfactory.agents.base import run_agent
from ytfactory.schemas import FactCheckReport

TIER = "verifier"
PROMPT_FILE = "a06_fact_checker.md"


def run(router, payload: dict, stub: dict, forbid_family: str | None = None) -> FactCheckReport:
    return run_agent(
        router,
        agent_id="A6",
        tier=TIER,
        prompt_file=PROMPT_FILE,
        payload=payload,
        schema=FactCheckReport,
        stub=stub,
        forbid_family=forbid_family,
    )
