"""A7 Scene Director (S09 upgrade) — tier: writer."""
from __future__ import annotations

from ytfactory.agents.base import run_agent
from ytfactory.schemas import ScenePlanSchema

TIER = "writer"
PROMPT_FILE = "a07_scene_director.md"
STUB = {
    "scenes": [
        {
            "ord": 1,
            "sentence_ids": [1, 2, 3],
            "visual_intent": "Close-up of a glowing accretion disk bending light around a black sphere",
            "stock_queries": ["black hole accretion disk", "space telescope galaxy"],
            "gen_prompt": "photorealistic black hole bending starlight, deep space, cinematic lighting",
            "motion": "kenburns_zoom_in",
        }
    ]
}


def run(router, payload: dict, stub: dict | None = None) -> ScenePlanSchema:
    return run_agent(
        router,
        agent_id="A7",
        tier=TIER,
        prompt_file=PROMPT_FILE,
        payload=payload,
        schema=ScenePlanSchema,
        stub=stub if stub is not None else STUB,
    )
