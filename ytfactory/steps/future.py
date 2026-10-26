"""Legacy stub module — every step S00–S22 now has a real builder.

Kept for the manifest writer shared by S22 (dry + live) and the outputs
directory constant. Steps live in steps/{research,script,audio,visual,
packaging,ai_qc}.py and research/orchestrator handlers.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ytfactory.steps import STEP_NAMES
from ytfactory.steps.common import Ctx

OUTPUTS_DIR = Path(__file__).resolve().parent.parent.parent / "outputs"

BUILDERS: set[str] = {
    "S00_INTAKE",
    "S01_RESEARCH_PLAN",
    "S02_SOURCE_HARVEST",
    "S03_SOURCE_SCORE",
    "S04_EVIDENCE_INDEX",
    "S05_OUTLINE",
    "S06_DRAFT",
    "S07_CRITIQUE",
    "S08_FACT_CHECK",
    "S09_SCENE_PLAN",
    "S10_TTS",
    "S17_CAPTIONS",
    "S11_VISUAL_SOURCE",
    "S12_LICENSE_GATE",
    "S13_IMAGE_GEN",
    "S14_AUDIO_BED",
    "S15_EDL_COMPILE",
    "S16_RENDER",
    "S18_PACKAGING",
    "S19_AUTO_QC",
    "S20_AI_QC",
    "S21_HUMAN_GATE",
    "S22_DELIVER",
}


def _write_manifest(ctx: Ctx) -> dict[str, Any]:
    project = ctx.bb.get_project(ctx.project_id)
    slug = project["slug"]
    out_dir = OUTPUTS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    steps = [
        dict(r)
        for r in ctx.bb.conn.execute(
            "SELECT step_name, status, gate, error "
            "FROM steps WHERE project_id = ? ORDER BY step_name",
            (ctx.project_id,),
        ).fetchall()
    ]
    claims = ctx.bb.conn.execute(
        "SELECT COUNT(*) AS n FROM claims WHERE project_id = ? AND verdict = 'supported'",
        (ctx.project_id,),
    ).fetchone()["n"]
    sentences = ctx.bb.conn.execute(
        "SELECT COUNT(*) AS n FROM sentences WHERE project_id = ?",
        (ctx.project_id,),
    ).fetchone()["n"]

    manifest = {
        "slug": slug,
        "topic": ctx.topic,
        "dry_run": ctx.dry_run,
        "steps_done": sum(1 for s in steps if s["status"] == "done"),
        "steps_total": len(STEP_NAMES),
        "supported_claims": claims,
        "sentences": sentences,
        "steps": [
            {"step": s["step_name"], "status": s["status"], "gate": s["gate"]}
            for s in steps
        ],
    }
    path = out_dir / ("dry_run_manifest.json" if ctx.dry_run else "manifest.json")
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"manifest_path": str(path), "slug": slug}
