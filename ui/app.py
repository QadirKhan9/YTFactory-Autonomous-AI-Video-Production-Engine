"""YTFactory local control dashboard (FastAPI + htmx, localhost only — NG7).

Run:  python -m ui.app      ->  http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ytfactory.db import Blackboard, slugify, utcnow
from ytfactory.orchestrator import state_machine

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = ROOT / "outputs"
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

app = FastAPI(title="YTFactory", docs_url=None, redoc_url=None)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")


def get_bb() -> Blackboard:
    bb = Blackboard(ROOT / "store" / "ytfactory.db")
    bb.init_schema()
    return bb


def _project(bb: Blackboard, ref: str):
    project = bb.resolve_project(str(ref))
    if project is None and str(ref).isdigit() is False:
        # allow /projects/<topic-slug-with-hash> and also raw topics
        project = bb.resolve_project(slugify(str(ref)))
    if project is None:
        raise ValueError(f"no project matching {ref!r}")
    return project


def _project_or_404(bb: Blackboard, ref: str):
    try:
        return _project(bb, ref)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _steps(bb: Blackboard, project_id: int) -> list[dict]:
    return [
        dict(r)
        for r in bb.conn.execute(
            "SELECT step_name, status, gate, error, started_at, finished_at "
            "FROM steps WHERE project_id = ? ORDER BY step_name",
            (project_id,),
        ).fetchall()
    ]


def _qc_report(project_slug: str) -> dict:
    path = OUTPUTS_DIR / project_slug / "qc_report.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _ai_qc_report(project_slug: str) -> dict:
    path = OUTPUTS_DIR / project_slug / "ai_qc_report.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _metadata(project_slug: str) -> dict:
    path = OUTPUTS_DIR / project_slug / "metadata.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _storyboard(bb: Blackboard, project_id: int, slug: str) -> list[dict]:
    rows = [
        dict(r)
        for r in bb.conn.execute(
            "SELECT ord, start_ms, end_ms, motion, asset_sha256, gate_status "
            "FROM scenes WHERE project_id = ? ORDER BY ord",
            (project_id,),
        ).fetchall()
    ]
    out = []
    for scene in rows:
        sha = scene.pop("asset_sha256", None)
        preview = ""
        if sha:
            card = OUTPUTS_DIR / slug / "cards" / f"scene_{scene['ord']}.png"
            if card.exists():
                preview = f"/outputs/{slug}/cards/scene_{scene['ord']}.png"
            else:
                matches = list((OUTPUTS_DIR / slug / "assets").glob(f"{sha}.*"))
                if matches:
                    preview = f"/outputs/{slug}/assets/{matches[0].name}"
        asset = {}
        if sha:
            row = bb.conn.execute(
                "SELECT source_url, source_title, license_id, license_url, gate_status "
                "FROM media_assets WHERE project_id = ? AND sha256 = ?",
                (project_id, sha),
            ).fetchone()
            asset = dict(row) if row else {}
        scene["preview"] = preview
        scene["asset"] = asset
        out.append(scene)
    return out


def _claims(bb: Blackboard, project_id: int) -> list[dict]:
    return [
        dict(r)
        for r in bb.conn.execute(
            "SELECT claim_text, verdict FROM claims WHERE project_id = ? ORDER BY id LIMIT 50",
            (project_id,),
        ).fetchall()
    ]


# -- pages -----------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    bb = get_bb()
    try:
        projects = [dict(r) for r in bb.conn.execute(
            "SELECT id, slug, topic, status, updated_at FROM projects "
            "ORDER BY updated_at DESC"
        ).fetchall()]
        return TEMPLATES.TemplateResponse(
            request, "dashboard.html", {"projects": projects}
        )
    finally:
        bb.close()


@app.get("/projects/{ref}", response_class=HTMLResponse)
def project_detail(request: Request, ref: str, error: str = ""):
    bb = get_bb()
    try:
        project = _project_or_404(bb, ref)
        return TEMPLATES.TemplateResponse(
            request,
            "project.html",
            {
                "project": project,
                "steps": _steps(bb, project["id"]),
                "events": [
                    dict(r)
                    for r in bb.conn.execute(
                        "SELECT event_type, level, message, timestamp FROM events "
                        "WHERE project_id = ? ORDER BY id DESC LIMIT 25",
                        (project["id"],),
                    ).fetchall()
                ],
                "error": error,
            },
        )
    finally:
        bb.close()


@app.get("/projects/{ref}/review", response_class=HTMLResponse)
def review(request: Request, ref: str, error: str = ""):
    bb = get_bb()
    try:
        project = _project_or_404(bb, ref)
        gate_row = bb.get_step(project["id"], "S21_HUMAN_GATE")
        awaiting = bool(
            gate_row and gate_row["status"] == "blocked"
            and gate_row["gate"] == "AWAITING_HUMAN"
        )
        return TEMPLATES.TemplateResponse(
            request,
            "review.html",
            {
                "project": project,
                "awaiting": awaiting,
                "steps": _steps(bb, project["id"]),
                "qc": _qc_report(project["slug"]),
                "ai_qc": _ai_qc_report(project["slug"]),
                "storyboard": _storyboard(bb, project["id"], project["slug"]),
                "claims": _claims(bb, project["id"]),
                "metadata": _metadata(project["slug"]),
                "has_video": (OUTPUTS_DIR / project["slug"] / "final.mp4").exists(),
                "error": error,
            },
        )
    finally:
        bb.close()


@app.get("/projects/{ref}/qc", response_class=HTMLResponse)
def qc_page(request: Request, ref: str):
    bb = get_bb()
    try:
        project = _project_or_404(bb, ref)
        return TEMPLATES.TemplateResponse(
            request,
            "qc.html",
            {"project": project, "qc": _qc_report(project["slug"])},
        )
    finally:
        bb.close()


@app.get("/projects/{ref}/delivery", response_class=HTMLResponse)
def delivery(request: Request, ref: str, error: str = ""):
    bb = get_bb()
    try:
        project = _project_or_404(bb, ref)
        slug = project["slug"]
        files = {}
        for name in ("final.mp4", "thumbnail.png", "captions.srt", "captions.vtt",
                     "metadata.json", "CREDITS.md", "SOURCES.md", "qc_report.json",
                     "manifest.json"):
            path = OUTPUTS_DIR / slug / name
            files[name] = {
                "url": f"/outputs/{slug}/{name}",
                "size": path.stat().st_size if path.exists() else None,
            }
        meta = _metadata(slug)
        credits_text = ""
        credits_path = OUTPUTS_DIR / slug / "CREDITS.md"
        if credits_path.exists():
            credits_text = credits_path.read_text(encoding="utf-8")
        return TEMPLATES.TemplateResponse(
            request,
            "delivery.html",
            {"project": project, "files": files, "meta": meta,
             "credits_text": credits_text, "error": error},
        )
    finally:
        bb.close()


# -- gate actions ----------------------------------------------------------
@app.post("/projects/{ref}/approve")
def approve(ref: str):
    bb = get_bb()
    try:
        state_machine.approve(bb, ref)
    except ValueError as exc:
        return RedirectResponse(
            f"/projects/{ref}/review?error={exc}", status_code=303
        )
    finally:
        bb.close()
    return RedirectResponse(f"/projects/{ref}/delivery", status_code=303)


@app.post("/projects/{ref}/reject")
def reject(ref: str, reason: str = Form("")):
    bb = get_bb()
    try:
        project = _project(bb, ref)
        sanitized_reason = (reason or "").strip()[:500]
        bb.add_event(
            project["id"], "human_gate", f"rejected: {sanitized_reason or 'no reason given'}",
            level="warning",
        )
        bb.set_project_status(project["id"], "REJECTED")
    except ValueError as exc:
        return RedirectResponse(f"/projects/{ref}/review?error={exc}", status_code=303)
    finally:
        bb.close()
    return RedirectResponse(f"/projects/{ref}", status_code=303)


@app.post("/projects/{ref}/request_changes")
def request_changes(ref: str, step: str = Form("S18")):
    bb = get_bb()
    try:
        project = _project(bb, ref)
        target = state_machine.resolve_step(step)
        bb.invalidate_from(project["id"], target, [s.name for s in state_machine.ORDER])
        bb.set_project_status(project["id"], "PAUSED")
        bb.add_event(project["id"], "human_gate", f"changes requested from {target}")
    except (ValueError, KeyError) as exc:
        return RedirectResponse(f"/projects/{ref}/review?error={exc}", status_code=303)
    finally:
        bb.close()
    return RedirectResponse(f"/projects/{ref}", status_code=303)


@app.post("/projects/{ref}/resume")
def resume_project(ref: str):
    """Resume a PAUSED / REJECTED project from where it stopped."""
    bb = get_bb()
    try:
        project = _project(bb, ref)
        project_id = project["id"]
        bb.add_event(project_id, "resume", "resumed from web UI")
    except ValueError as exc:
        bb.close()
        return RedirectResponse(f"/projects/{ref}?error={exc}", status_code=303)
    finally:
        bb.close()

    def _run():
        _bb = get_bb()
        try:
            state_machine.run_project(_bb, project_id, dry_run=False, verbose=True)
        finally:
            _bb.close()

    threading.Thread(target=_run, daemon=True).start()
    return RedirectResponse(f"/projects/{ref}", status_code=303)


@app.post("/run")
def run_new(topic: str = Form(...), mode: str = Form("dry")):
    """Start a brand-new project from the web UI modal."""
    cleaned_topic = topic.strip()
    if not cleaned_topic or len(cleaned_topic) < 3:
        return RedirectResponse("/?error=Topic+must+be+at+least+3+characters", status_code=303)
    if len(cleaned_topic) > 300:
        return RedirectResponse("/?error=Topic+too+long+(maximum+300+characters)", status_code=303)

    live = (mode.lower().strip() == "live")
    bb = get_bb()
    try:
        config = {
            "audience": "general",
            "duration_min": 5,
            "target_wpm": 150,
        }
        project_id = bb.create_project(cleaned_topic, config)
        project = bb.get_project(project_id)
        slug = project["slug"]
        bb.add_event(project_id, "created", f"started from web UI ({'live' if live else 'dry-run'})")
    finally:
        bb.close()

    def _run():
        _bb = get_bb()
        try:
            state_machine.run_project(_bb, project_id, dry_run=not live, verbose=True)
        finally:
            _bb.close()

    threading.Thread(target=_run, daemon=True).start()
    return RedirectResponse(f"/projects/{slug}", status_code=303)


def main() -> None:
    import os
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
