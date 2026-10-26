"""A3 Outline & Narrative (S05) — tier: writer."""
from __future__ import annotations

from ytfactory.agents.base import run_agent
from ytfactory.schemas import OutlineSchema

TIER = "writer"
PROMPT_FILE = "a03_outline.md"
STUB = {
    "hook": "Right now, your brain is deciding which of today's moments to keep forever.",
    "thesis": "Memories are not recordings; they are reconstructions stabilized by hippocampal replay and synaptic change.",
    "beats": [
        {"title": "The unreliable recording", "summary": "Why memory is reconstruction, not playback."},
        {"title": "Hippocampus as stage manager", "summary": "Encoding and indexing of episodes."},
        {"title": "Synaptic bookkeeping", "summary": "LTP and protein synthesis."},
        {"title": "Replay during sleep", "summary": "Sharp-wave ripples move traces to cortex."},
        {"title": "Reconsolidation", "summary": "Recalling a memory reopens it for editing."},
        {"title": "Why we forget", "summary": "Interference and active forgetting."},
    ],
    "open_loops": ["Why does the brain delete some details but keep others?"],
    "knowledge_gaps": ["Exact molecular timeline of systems consolidation in humans."],
    "target_wpm": 140,
}


def run(router, payload: dict, stub: dict | None = None) -> OutlineSchema:
    return run_agent(
        router,
        agent_id="A3",
        tier=TIER,
        prompt_file=PROMPT_FILE,
        payload=payload,
        schema=OutlineSchema,
        stub=stub if stub is not None else STUB,
    )
