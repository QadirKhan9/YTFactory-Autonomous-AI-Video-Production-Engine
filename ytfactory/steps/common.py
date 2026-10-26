"""Shared step context, gate exception, and dry-run helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ytfactory.db import Blackboard
from ytfactory.orchestrator.router import Router


class GateHalt(Exception):
    """A ▸ gate: the DAG stops until a human or a rerun intervenes."""

    def __init__(self, gate: str, message: str):
        super().__init__(message)
        self.gate = gate  # BLOCKED_SOURCES | BLOCKED_FACTS | AWAITING_HUMAN | ...
        self.message = message


@dataclass
class Ctx:
    bb: Blackboard
    project_id: int
    dry_run: bool
    router: Router
    config: dict = field(default_factory=dict)
    stub_queues: dict[str, list[dict]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def topic(self) -> str:
        return str(self.bb.get_project(self.project_id)["topic"])

    def stub(self, agent: str, default: dict) -> dict:
        """Test hook: pre-loaded stub sequences override the default payload."""
        queue = self.stub_queues.get(agent)
        if queue:
            return queue.pop(0)
        return default

    def note(self, message: str) -> None:
        self.notes.append(message)
        self.bb.add_event(self.project_id, "step_note", message)


def step_outputs(ctx: Ctx, step_name: str) -> dict:
    row = ctx.bb.get_step(ctx.project_id, step_name)
    if row is None or row["outputs_json"] is None:
        raise RuntimeError(f"upstream step {step_name} has no outputs")
    import json

    return json.loads(row["outputs_json"])


def last_model_family(ctx: Ctx, step_name: str) -> str | None:
    """Family of the model a prior step actually used (for decorrelation)."""
    row = ctx.bb.conn.execute(
        """SELECT model FROM usage_ledger
           WHERE project_id = ? AND step_name = ?
           ORDER BY id DESC LIMIT 1""",
        (ctx.project_id, step_name),
    ).fetchone()
    if not row or not row["model"] or row["model"] == "stub":
        return None
    from ytfactory.orchestrator.router import model_family

    return model_family(row["model"])


def record_usage(ctx: Ctx, step_name: str) -> None:
    """Drain every real LLM call the step made into usage_ledger.

    The router clears last_usage at the start of each invoke and appends each
    successful call to usage_log, so a stub fallback records nothing (never the
    previous step's token counts).
    """
    pending: list[dict] = list(ctx.router.usage_log)
    ctx.router.usage_log.clear()
    for usage in pending:
        if usage.get("provider") and usage["provider"] not in ("dry-run", "stub"):
            ctx.bb.add_usage(
                ctx.project_id,
                step_name,
                usage.get("provider", ""),
                usage.get("model", ""),
                int(usage.get("tokens_in", 0)),
                int(usage.get("tokens_out", 0)),
            )
