"""A1 Research Planner (S01) — tier: cheap."""
from __future__ import annotations

from ytfactory.agents.base import run_agent
from ytfactory.schemas import ResearchPlanSchema

TIER = "cheap"
PROMPT_FILE = "a01_research_planner.md"
STUB = {
    "sub_questions": [
        "What neural substrates encode declarative memories?",
        "How does consolidation transform short-term traces?",
        "What role does sleep play in memory stabilization?",
    ],
    "search_queries": [
        "hippocampus memory consolidation review",
        "synaptic long-term potentiation memory",
        "systems consolidation sleep replay",
    ],
    "source_types": ["peer_reviewed", "gov", "institution"],
}


def run(router, payload: dict, stub: dict | None = None) -> ResearchPlanSchema:
    return run_agent(
        router,
        agent_id="A1",
        tier=TIER,
        prompt_file=PROMPT_FILE,
        payload=payload,
        schema=ResearchPlanSchema,
        stub=stub if stub is not None else STUB,
    )
