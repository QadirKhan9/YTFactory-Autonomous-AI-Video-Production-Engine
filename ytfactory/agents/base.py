"""Agent invocation layer (blackboard pattern: stateless (in, artifacts) -> out).

Agents never call each other; they read payload dicts produced by the step and
return schema-validated objects. All routing goes through Router (D-005).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Type

from pydantic import BaseModel

from ytfactory.orchestrator.router import Router, RouterError

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def render_prompt(template: str, payload: dict[str, Any]) -> str:
    out = template
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, indent=2)
        out = out.replace("{{" + key + "}}", str(value))
    return out


def run_agent(
    router: Router,
    *,
    agent_id: str,
    tier: str,
    prompt_file: str,
    payload: dict[str, Any],
    schema: Type[BaseModel],
    stub: dict,
    forbid_family: Optional[str] = None,
) -> BaseModel:
    template = (PROMPTS_DIR / prompt_file).read_text(encoding="utf-8")
    messages = [
        {
            "role": "system",
            "content": (
                f"You are {agent_id} inside the YTFactory pipeline. "
                "Reply with ONLY a JSON object matching the requested schema. "
                "Never invent evidence you were not given."
            ),
        },
        {"role": "user", "content": render_prompt(template, payload)},
    ]
    try:
        return router.invoke(
            agent=agent_id,
            tier=tier,
            messages=messages,
            schema=schema,
            stub=stub,
            forbid_family=forbid_family,
        )
    except RouterError as exc:
        if stub is None:
            raise
        # offline-first (architecture.md): every tier failure degrades to the
        # deterministic stub instead of killing a live run
        print(f"[agent-fallback] {agent_id}: {exc}")
        print(f"[agent-fallback] {agent_id}: live LLM unavailable -> deterministic stub")
        return schema.model_validate(stub)
