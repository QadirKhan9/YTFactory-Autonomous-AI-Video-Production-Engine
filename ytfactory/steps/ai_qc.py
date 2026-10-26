"""Phase 5: S20 subjective editorial review via A9 (FR-Q-03).

Deterministic heuristics first; LLM verdict used automatically when a key
is present (same zero-cost precedent as A7/A8). Blockers halt the pipeline
with GateHalt("BLOCKED_AI_QC") — user decision; warnings/notes flow to the
human review UI instead.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ytfactory.steps.audio import _project_dir, _register_artifact
from ytfactory.steps.common import Ctx, GateHalt, step_outputs

OUTPUTS_DIR = Path(__file__).resolve().parent.parent.parent / "outputs"


def _llm_keys_present() -> bool:
    import os

    return any(
        os.environ.get(k)
        for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY", "OPENAI_API_KEY")
    )


def _review_payload(ctx: Ctx, *, sample: bool) -> dict[str, Any]:
    from ytfactory.workers import editorial

    project = ctx.bb.get_project(ctx.project_id)
    sentences = [
        dict(r)
        for r in ctx.bb.conn.execute(
            "SELECT ord, text, start_ms, duration_ms, is_rhetorical "
            "FROM sentences WHERE project_id = ? ORDER BY ord",
            (ctx.project_id,),
        ).fetchall()
    ]
    scenes = [
        dict(r)
        for r in ctx.bb.conn.execute(
            "SELECT ord, motion, license_id, gate_status FROM scenes "
            "WHERE project_id = ? ORDER BY ord",
            (ctx.project_id,),
        ).fetchall()
    ]
    duration_ms = 0
    try:
        duration_ms = int(float(step_outputs(ctx, "S10_TTS").get("master_duration_ms") or 0))
    except (RuntimeError, TypeError, ValueError):
        duration_ms = 0

    qc_summary: dict[str, Any] = {}
    qc_path = _project_dir(ctx) / "qc_report.json"
    if qc_path.exists():
        try:
            report = json.loads(qc_path.read_text(encoding="utf-8"))
            qc_summary = {k: report.get(k) for k in ("passed", "failed", "skipped", "all_pass")}
        except (OSError, json.JSONDecodeError):
            qc_summary = {}

    frame_stats: list[dict] = []
    if sample:
        frame_stats = editorial.sample_frames(
            _project_dir(ctx) / "final.mp4", duration_ms
        )
    return {
        "topic": project["topic"],
        "audience": ctx.config.get("audience", "beginner"),
        "pacing": editorial.pacing_stats(sentences, duration_ms),
        "hook": editorial.hook_stats(sentences),
        "sentences": sentences,
        "scenes": scenes,
        "qc_summary": qc_summary,
        "frame_stats": frame_stats,
    }


def run_s20(ctx: Ctx) -> dict:
    from ytfactory.agents import a09_qc_reviewer as a09
    from ytfactory.workers import editorial

    if ctx.dry_run:
        a09.run(ctx.router, _review_payload(ctx, sample=False),
                stub=ctx.stub("A9", a09.STUB))  # schema firewall
        return {"dry_run": True, "step": "S20_AI_QC", "phase": "Phase 5"}

    payload = _review_payload(ctx, sample=True)
    review: dict | None = None
    method = "deterministic"
    if _llm_keys_present():
        try:
            review = a09.run(
                ctx.router, payload, stub=ctx.stub("A9", a09.STUB)
            ).model_dump()
            method = "A9-llm"
            from ytfactory.steps.common import record_usage

            record_usage(ctx, "S20_AI_QC")
        except Exception as exc:  # noqa: BLE001 - deterministic fallback
            ctx.note(f"A9 LLM unavailable ({type(exc).__name__}); heuristic review used")
    if review is None:
        review = editorial.deterministic_review(payload)
        a09.QcReviewSchema.model_validate(review)

    out_dir = _project_dir(ctx)
    report = dict(review)
    report.update(
        {
            "method": method,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stats": {
                "pacing": payload["pacing"],
                "hook": payload["hook"],
            },
        }
    )
    report_path = out_dir / "ai_qc_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _register_artifact(ctx, report_path, "application/json", "ai_qc_report", "S20_AI_QC")

    blockers = report.get("blockers") or []
    warnings = report.get("warnings") or []
    for w in warnings:
        ctx.note(f"A9 warning [{w['dimension']}]: {w['message']}")

    if blockers:
        for b in blockers:
            ctx.note(f"A9 blocker [{b['dimension']}]: {b['message']}")
        summary = "; ".join(b["message"] for b in blockers[:3])
        raise GateHalt(
            "BLOCKED_AI_QC",
            f"A9 editorial review found {len(blockers)} blocker(s): {summary} "
            f"(full report: {report_path.name} — fix, then rerun from S06 or request changes)",
        )
    return {
        "report_path": str(report_path),
        "blockers": 0,
        "warnings": len(warnings),
        "notes": len(report.get("notes") or []),
        "method": method,
    }
