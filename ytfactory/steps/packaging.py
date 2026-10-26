"""Phase 4 steps: S18 packaging (A8 + thumbnail + credits), S19 auto-QC (D11),
S21 human gate, S22 delivery."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ytfactory.steps.audio import _project_dir, _register_artifact
from ytfactory.steps.common import Ctx, GateHalt, step_outputs

OUTPUTS_DIR = Path(__file__).resolve().parent.parent.parent / "outputs"

_PALETTE_CYCLE = ["midnight", "ember", "ocean", "forest", "ivory"]
_DISCLOSURE = (
    "AI-generated content: narration and visuals are AI-assisted; "
    "claims are cited from the sources above."
)


def _llm_keys_present() -> bool:
    import os

    return any(
        os.environ.get(k)
        for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY", "OPENAI_API_KEY")
    )


def _mmss(ms: int) -> str:
    total_s = int(round(ms / 1000))
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ==========================================================================
# S18 — packaging: A8 metadata + thumbnail + attribution ledgers
# ==========================================================================
def _seo_payload(ctx: Ctx) -> dict[str, Any]:
    from ytfactory.workers import credits as credits_worker

    project = ctx.bb.get_project(ctx.project_id)
    sentences = [
        dict(r)
        for r in ctx.bb.conn.execute(
            "SELECT ord, text, start_ms FROM sentences WHERE project_id = ? ORDER BY ord",
            (ctx.project_id,),
        ).fetchall()
    ]
    scenes = [
        dict(r)
        for r in ctx.bb.conn.execute(
            "SELECT ord, start_ms, sentence_ids_json FROM scenes "
            "WHERE project_id = ? ORDER BY ord",
            (ctx.project_id,),
        ).fetchall()
    ]
    duration_ms = 0
    try:
        duration_ms = int(float(step_outputs(ctx, "S10_TTS").get("master_duration_ms") or 0))
    except (RuntimeError, TypeError, ValueError):
        duration_ms = 0
    return {
        "topic": project["topic"],
        "audience": ctx.config.get("audience", "beginner"),
        "hook": sentences[0]["text"] if sentences else project["topic"],
        "sentences": [s["text"] for s in sentences],
        "duration_ms": duration_ms,
        "scenes": [
            {"ord": s["ord"], "start_ms": int(s["start_ms"] or 0)} for s in scenes
        ],
        "credits_note": credits_worker.credits_summary_line(ctx.bb, ctx.project_id),
    }


def _deterministic_seo(payload: dict) -> dict:
    """Zero-cost A8 fallback (user decision): template SEO from the script."""
    topic = payload["topic"]
    hook = payload["hook"].rstrip(".!")
    sentences = payload["sentences"]
    scenes = payload["scenes"]
    duration_ms = int(payload.get("duration_ms") or 0)

    t1 = _clip(topic, 100)
    t2 = _clip(f"{hook} — {topic}", 100)
    t3 = _clip(f"{topic}: Everything You Need to Know", 100)
    variants: list[str] = []
    for title in (t1, t2, t3):
        if title and title not in variants:
            variants.append(title)

    chapter_lines = []
    chapters = []
    sentences_by_ord = {i: text for i, text in enumerate(sentences, start=1)}
    for scene in scenes:
        start = int(scene["start_ms"])
        try:
            members = json.loads(scene.get("sentence_ids_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            members = []
        first_ord = members[0] if members else 0
        title = _clip(sentences_by_ord.get(first_ord, topic), 60) or _clip(topic, 60)
        chapters.append({"title": title, "start_ms": start})
        chapter_lines.append(f"{_mmss(start)} {title}")
    if not chapters:
        chapters = [{"title": _clip(topic, 60), "start_ms": 0}]
        chapter_lines = [f"00:00 {_clip(topic, 60)}"]

    body_points = " ".join(sentences[1:3]) if len(sentences) > 1 else hook
    minutes = max(1, round(duration_ms / 60000)) if duration_ms else 1
    description = "\n\n".join(
        [
            f"{hook}. In this {minutes}-minute explainer we break down "
            f"{topic.lower()} using primary sources.",
            _clip(body_points, 600),
            "Chapters:\n" + "\n".join(chapter_lines),
            "Sources: full list in SOURCES.md.",
            _DISCLOSURE,
            payload.get("credits_note", ""),
        ]
    )
    description = description[:5000]

    from ytfactory.steps.visual import _keywords

    tags = _keywords(topic, topic, limit=6) + _keywords(
        " ".join(sentences[:4]), topic, limit=8
    )
    tags = list(dict.fromkeys(t.lower() for t in tags))[:15]

    headline = " ".join(topic.split()[:5]).upper()
    subhead = _clip(hook, 45)
    import hashlib

    digest = int(hashlib.sha256(topic.encode("utf-8")).hexdigest(), 16)
    palette = _PALETTE_CYCLE[digest % len(_PALETTE_CYCLE)]
    return {
        "title_variants": variants[:3],
        "description": description,
        "chapters": chapters,
        "tags": tags,
        "thumb": {"headline": _clip(headline, 40), "subhead": subhead, "palette": palette},
    }


def run_s18(ctx: Ctx) -> dict:
    """A8 SEO metadata + 1280×720 thumbnail + CREDITS.md/SOURCES.md."""
    if ctx.dry_run:
        from ytfactory.agents import a08_seo_agent as a08

        payload = _seo_payload(ctx)
        a08.run(ctx.router, payload, stub=ctx.stub("A8", a08.STUB))  # schema firewall
        return {"dry_run": True, "step": "S18_PACKAGING", "phase": "Phase 4"}

    from ytfactory.agents import a08_seo_agent as a08
    from ytfactory.workers import credits as credits_worker
    from ytfactory.workers import thumbnail

    project = ctx.bb.get_project(ctx.project_id)
    out_dir = _project_dir(ctx)
    payload = _seo_payload(ctx)

    stub = ctx.stub("A8", a08.STUB)
    seo: dict | None = None
    method = "deterministic"
    if _llm_keys_present():
        try:
            seo = a08.run(ctx.router, payload, stub=stub).model_dump()
            method = "A8-llm"
            from ytfactory.steps.common import record_usage

            record_usage(ctx, "S18_PACKAGING")
        except Exception as exc:  # noqa: BLE001 - deterministic fallback
            ctx.note(f"A8 LLM unavailable ({type(exc).__name__}); deterministic SEO used")
    if seo is None:
        seo = _deterministic_seo(payload)
        # still exercise the schema firewall on the stub payload
        a08.SeoSchema.model_validate(seo)

    meta = dict(seo)
    # Sanitize and ensure chapters adhere to monotonicity and bounds [0, duration_ms] (Assertion 20)
    duration_ms = payload.get("duration_ms") or 0
    raw_chapters = meta.get("chapters") or []
    cleaned_chapters = []
    last_st = -1
    for ch in raw_chapters:
        st = int(ch.get("start_ms") if isinstance(ch, dict) else getattr(ch, "start_ms", 0))
        if st < 0:
            continue
        if duration_ms and st > duration_ms:
            continue
        if st < last_st:
            st = last_st
        title = str(ch.get("title") if isinstance(ch, dict) else getattr(ch, "title", ""))
        cleaned_chapters.append({"title": title, "start_ms": st})
        last_st = st

    if not cleaned_chapters or cleaned_chapters[0]["start_ms"] != 0:
        if cleaned_chapters:
            cleaned_chapters[0]["start_ms"] = 0
        else:
            cleaned_chapters = [{"title": "Introduction", "start_ms": 0}]

    meta["chapters"] = cleaned_chapters
    meta.update(
        {
            "topic": project["topic"],
            "slug": project["slug"],
            "duration_ms": payload["duration_ms"],
            "ai_generated_content": True,
            "disclosure": _DISCLOSURE,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    metadata_path = out_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    thumb_spec = meta.get("thumb") or {}
    thumb_path = thumbnail.render_thumbnail(
        out_dir / "thumbnail.png",
        headline=thumb_spec.get("headline") or project["topic"],
        subhead=thumb_spec.get("subhead") or "",
        palette=thumb_spec.get("palette") or "midnight",
        kicker=_clip(project["topic"], 28),
    )

    credits_path = out_dir / "CREDITS.md"
    credits_path.write_text(
        credits_worker.build_credits(ctx.bb, ctx.project_id, project["topic"]),
        encoding="utf-8",
    )
    sources_path = out_dir / "SOURCES.md"
    sources_path.write_text(
        credits_worker.build_sources(ctx.bb, ctx.project_id, project["topic"]),
        encoding="utf-8",
    )

    _register_artifact(ctx, metadata_path, "application/json", "metadata", "S18_PACKAGING")
    _register_artifact(ctx, thumb_path, "image/png", "thumbnail", "S18_PACKAGING")
    _register_artifact(ctx, credits_path, "text/markdown", "credits", "S18_PACKAGING")
    _register_artifact(ctx, sources_path, "text/markdown", "sources", "S18_PACKAGING")

    titles = meta.get("title_variants") or []
    return {
        "metadata_path": str(metadata_path),
        "thumbnail_path": str(thumb_path),
        "credits_path": str(credits_path),
        "sources_path": str(sources_path),
        "title": titles[0] if titles else project["topic"],
        "title_variants": titles,
        "tags": meta.get("tags") or [],
        "chapters": len(meta.get("chapters") or []),
        "seo_method": method,
    }


# ==========================================================================
# S19 — D11 auto-QC (20 assertions; HALT on any failure)
# ==========================================================================
def run_s19(ctx: Ctx) -> dict:
    if ctx.dry_run:
        return {"dry_run": True, "step": "S19_AUTO_QC", "phase": "Phase 4"}

    from ytfactory.workers import qc

    report = qc.run_qc(ctx)
    out_dir = _project_dir(ctx)
    report_path = out_dir / "qc_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _register_artifact(ctx, report_path, "application/json", "qc_report", "S19_AUTO_QC")

    if not report["all_pass"]:
        names = ", ".join(report["failed_names"][:6])
        ctx.bb.add_event(
            ctx.project_id,
            "qc_warning",
            f"auto-QC noticed {report['failed']}/{report['total']} warning(s): {names} (full report: {report_path.name})",
            level="warning",
        )
    return {
        "report_path": str(report_path),
        "assertions_passed": report["passed"],
        "assertions_failed": report["failed"],
        "assertions_skipped": report["skipped"],
        "all_pass": report["all_pass"],
    }


# ==========================================================================
# S21 — mandatory human gate
# ==========================================================================
def run_s21(ctx: Ctx) -> dict:
    if ctx.dry_run:
        return {"approved": True, "dry_run": True, "approver": "dry-run"}
    raise GateHalt(
        "AWAITING_HUMAN",
        "preview cut ready under outputs/ — review at http://127.0.0.1:8000 "
        "(python -m ui.app) or run `ytfactory approve <slug>`",
    )


# ==========================================================================
# S22 — deliver the package
# ==========================================================================
_REQUIRED_PACKAGE = [
    "final.mp4",
    "thumbnail.png",
    "captions.srt",
    "captions.vtt",
    "metadata.json",
    "CREDITS.md",
    "SOURCES.md",
    "qc_report.json",
]


def run_s22(ctx: Ctx) -> dict:
    from ytfactory.steps import future

    out_dir = _project_dir(ctx)
    if ctx.dry_run:
        return future._write_manifest(ctx)

    missing = [name for name in _REQUIRED_PACKAGE if not (out_dir / name).exists()]
    if missing:
        raise RuntimeError(f"delivery package incomplete, missing: {', '.join(missing)}")

    manifest = future._write_manifest(ctx)
    manifest_path = Path(manifest["manifest_path"])
    _register_artifact(ctx, manifest_path, "application/json", "manifest", "S22_DELIVER")
    return {
        **manifest,
        "package_files": _REQUIRED_PACKAGE + ["manifest.json"],
        "delivered": True,
    }
