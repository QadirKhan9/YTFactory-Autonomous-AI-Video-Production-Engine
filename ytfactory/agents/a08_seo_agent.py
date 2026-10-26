"""A8 SEO & Thumbnail Agent (S18) — tier: cheap."""
from __future__ import annotations

from ytfactory.agents.base import run_agent
from ytfactory.schemas import SeoSchema

TIER = "cheap"
PROMPT_FILE = "a08_seo_agent.md"
STUB = {
    "title_variants": [
        "Black Holes: Why Light Bends Around Them",
        "The Gravity Trick Behind Bending Light",
        "How Black Holes Bend Light — Explained",
    ],
    "description": (
        "Why does light bend around black holes? This video explains gravitational "
        "lensing from the evidence up.\n\n"
        "0:00 Intro\n\nChapters are generated from the scene timeline.\n\n"
        "AI-generated narration and visuals; claims are cited from the sources list."
    ),
    "chapters": [{"title": "Intro", "start_ms": 0}],
    "tags": ["black holes", "gravity", "space", "physics", "explainer"],
    "thumb": {
        "headline": "WHY LIGHT BENDS",
        "subhead": "Black holes, explained",
        "palette": "midnight",
    },
}


def run(router, payload: dict, stub: dict | None = None) -> SeoSchema:
    return run_agent(
        router,
        agent_id="A8",
        tier=TIER,
        prompt_file=PROMPT_FILE,
        payload=payload,
        schema=SeoSchema,
        stub=stub if stub is not None else STUB,
    )
