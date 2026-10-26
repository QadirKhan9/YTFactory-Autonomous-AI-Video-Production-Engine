"""Ordered step registry (S00–S22) + synchronous runner with idempotency,
job-queue leasing, and gate handling (FR-Res-01/02, architecture.md §4)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

from ytfactory.db import Blackboard, utcnow
from ytfactory.orchestrator.idem import compute_idem_key
from ytfactory.orchestrator.router import Router
from ytfactory.steps import STEP_NAMES, ai_qc, audio as audio_steps, packaging, research, script, visual as visual_steps
from ytfactory.steps.common import Ctx, GateHalt


@dataclass(frozen=True)
class StepSpec:
    name: str
    capability: str  # jobs.capability CHECK: cpu|net|gpu|render|human
    tier: str        # configured tier that feeds the idem key
    handler: Callable[[Ctx], dict]


ORDER: list[StepSpec] = [
    StepSpec("S00_INTAKE", "cpu", "deterministic", research.run_s00),
    StepSpec("S01_RESEARCH_PLAN", "net", "cheap", research.run_s01),
    StepSpec("S02_SOURCE_HARVEST", "net", "deterministic", research.run_s02),
    StepSpec("S03_SOURCE_SCORE", "cpu", "deterministic", research.run_s03),
    StepSpec("S04_EVIDENCE_INDEX", "net", "cheap", research.run_s04),
    StepSpec("S05_OUTLINE", "net", "writer", script.run_s05),
    StepSpec("S06_DRAFT", "net", "writer", script.run_s06),
    StepSpec("S07_CRITIQUE", "net", "writer", script.run_s07),
    StepSpec("S08_FACT_CHECK", "net", "verifier", script.run_s08),
    # Audio-first: captions are arithmetic over the S10 master clock.
    StepSpec("S09_SCENE_PLAN", "cpu", "deterministic", visual_steps.run_s09),
    StepSpec("S10_TTS", "net", "deterministic", audio_steps.run_s10),
    StepSpec("S17_CAPTIONS", "net", "deterministic", audio_steps.run_s17),
    StepSpec("S11_VISUAL_SOURCE", "net", "deterministic", visual_steps.run_s11),
    StepSpec("S12_LICENSE_GATE", "cpu", "deterministic", visual_steps.run_s12),
    StepSpec("S13_IMAGE_GEN", "cpu", "deterministic", visual_steps.run_s13),
    StepSpec("S14_AUDIO_BED", "cpu", "deterministic", visual_steps.run_s14),
    StepSpec("S15_EDL_COMPILE", "cpu", "deterministic", visual_steps.run_s15),
    StepSpec("S16_RENDER", "render", "deterministic", visual_steps.run_s16),
    StepSpec("S18_PACKAGING", "cpu", "deterministic", packaging.run_s18),
    StepSpec("S19_AUTO_QC", "cpu", "deterministic", packaging.run_s19),
    StepSpec("S20_AI_QC", "net", "verifier", ai_qc.run_s20),
    StepSpec("S21_HUMAN_GATE", "human", "deterministic", packaging.run_s21),
    StepSpec("S22_DELIVER", "cpu", "deterministic", packaging.run_s22),
]

SPECS_BY_NAME = {spec.name: spec for spec in ORDER}


def resolve_step(token: str) -> str:
    """'S08' / 's08' / 'FACT_CHECK' -> 'S08_FACT_CHECK'."""
    t = token.strip().upper()
    if t in STEP_NAMES:
        return t
    matches = [n for n in STEP_NAMES if n.startswith(t)] or [
        n for n in STEP_NAMES if t in n
    ]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"cannot resolve step {token!r}; try one of: {', '.join(STEP_NAMES)}")


def _inputs(bb: Blackboard, project_id: int, upto: int) -> dict:
    """Idem-key inputs: config hash + sha256 of every upstream step's outputs."""
    project = bb.get_project(project_id)
    upstream: dict[str, str | None] = {}
    for spec in ORDER[:upto]:
        row = bb.get_step(project_id, spec.name)
        if row and row["status"] == "done" and row["outputs_json"]:
            upstream[spec.name] = hashlib.sha256(
                row["outputs_json"].encode("utf-8")
            ).hexdigest()
        else:
            upstream[spec.name] = None
    return {"config_hash": project["config_hash"], "upstream": upstream}


def run_project(
    bb: Blackboard,
    project_id: int,
    dry_run: bool = True,
    stub_queues: dict[str, list[dict]] | None = None,
    verbose: bool = True,
    until: str | None = None,
) -> str:
    """Execute S00–S22 in order. `until` pauses right after that step.

    Returns final project status:
    DELIVERED | PAUSED | BLOCKED_* | AWAITING_HUMAN | APPROVED | FAILED (raises)."""
    if until is not None:
        until = resolve_step(until)
    project = bb.get_project(project_id)
    if project["status"] == "DELIVERED":
        if verbose:
            print("project already DELIVERED")
        return "DELIVERED"

    router = Router(dry_run=dry_run)
    ctx = Ctx(
        bb=bb,
        project_id=project_id,
        dry_run=dry_run,
        router=router,
        config=bb.load_config(project_id),
        stub_queues=stub_queues or {},
    )
    bb.set_project_status(project_id, "RUNNING")

    for idx, spec in enumerate(ORDER):
        existing = bb.get_step(project_id, spec.name)
        inputs = _inputs(bb, project_id, idx)
        key = compute_idem_key(spec.name, inputs, model_tier=spec.tier)

        if existing and existing["status"] == "done":
            if existing["idem_key"] == key:
                if verbose:
                    print(f"  {spec.name:<20} cached")
                if until == spec.name:
                    bb.set_project_status(project_id, "PAUSED")
                    return "PAUSED"
                continue
            if verbose:
                print(f"  {spec.name:<20} stale -> rerun")
        elif existing and existing["status"] == "blocked":
            message = existing["error"] or f"blocked at {spec.name}"
            bb.add_event(
                project_id,
                "gate",
                message,
                level="warning",
                metadata={"gate": existing["gate"], "step": spec.name},
            )
            bb.set_project_status(project_id, existing["gate"] or "BLOCKED")
            if verbose:
                print(f"  {spec.name:<20} {existing['gate']} — {message}")
            return existing["gate"]

        job_id = bb.enqueue_job(project_id, spec.name, spec.capability, {"idem_key": key})
        claimed = bb.claim_job(spec.capability, "local-worker")
        bb.begin_step(project_id, spec.name, key)
        bb.add_event(project_id, "step_start", f"{spec.name} ({'dry' if dry_run else 'live'})")
        if verbose:
            print(f"  {spec.name:<20} ...", end="", flush=True)

        try:
            outputs = spec.handler(ctx)
        except GateHalt as gate:
            bb.finish_step(
                project_id, spec.name, {}, gate=gate.gate, status="blocked", error=gate.message
            )
            bb.complete_job(job_id)
            if claimed and int(claimed["id"]) != job_id:
                bb.complete_job(int(claimed["id"]))
            bb.add_event(
                project_id,
                "gate",
                gate.message,
                level="warning",
                metadata={"gate": gate.gate, "step": spec.name},
            )
            bb.set_project_status(project_id, gate.gate)
            if verbose:
                print(f"  {gate.gate}")
            return gate.gate
        except Exception as exc:
            bb.finish_step(
                project_id, spec.name, {}, status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            bb.fail_job(job_id, str(exc))
            if claimed and int(claimed["id"]) != job_id:
                bb.fail_job(int(claimed["id"]), str(exc))
            bb.add_event(project_id, "step_failed", str(exc), level="error")
            bb.set_project_status(project_id, "FAILED")
            if verbose:
                print(f"  FAILED: {exc}")
            raise

        bb.finish_step(project_id, spec.name, outputs, status="done")
        bb.complete_job(job_id)
        if claimed and int(claimed["id"]) != job_id:
            bb.complete_job(int(claimed["id"]))
        if verbose:
            print("  ok")
        if until == spec.name:
            bb.set_project_status(project_id, "PAUSED")
            if verbose:
                print(f"  paused after {spec.name} (--until)")
            return "PAUSED"

    bb.set_project_status(project_id, "DELIVERED")
    return "DELIVERED"


def approve(bb: Blackboard, ref: str) -> str:
    """Human sign-off at S21: clears AWAITING_HUMAN so `resume` can finish."""
    project = bb.resolve_project(ref)
    if not project:
        raise ValueError(f"no project matching {ref!r}")
    row = bb.get_step(project["id"], "S21_HUMAN_GATE")
    if not row or row["status"] != "blocked" or row["gate"] != "AWAITING_HUMAN":
        raise ValueError(f"project {project['slug']} is not awaiting approval")
    bb.finish_step(
        project["id"],
        "S21_HUMAN_GATE",
        {"approved": True, "at": utcnow(), "approver": ref},
        status="done",
    )
    bb.set_project_status(project["id"], "APPROVED")
    bb.add_event(project["id"], "human_gate", f"approved via {ref}")
    return "APPROVED"
