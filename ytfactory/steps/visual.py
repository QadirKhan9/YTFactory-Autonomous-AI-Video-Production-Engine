"""Phase 3/6 steps: S09 visual planning (A7), S11 stock sourcing (D5) with
the Phase 6.2 reusable-library rung, S12 license wall (D6), S13 image fill
(Phase 6.1 GPU bus -> library abstracts -> typographic card), S14 music bed
(Phase 6.3 sidechain ducking), S15 EDL compile (D9), S16 render (D10).

Audio-first invariant: S15 reads the S10 master clock; every frame count is
derived from `sentences/scenes.start_ms` — video conforms to audio.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ytfactory.schemas import EDLSchema, ScenePlanSchema
from ytfactory.steps.audio import _register_artifact
from ytfactory.steps.common import Ctx, step_outputs

OUTPUTS_DIR = Path(__file__).resolve().parent.parent.parent / "outputs"
FPS = 30
OUT_W, OUT_H = 1920, 1080
CANVAS_SCALE = 1.25  # Ken Burns headroom: canvas = output * 1.25

_MOTION_CYCLE = ("kenburns_zoom_in", "pan_right", "kenburns_zoom_out", "pan_left")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "for",
    "with", "from", "by", "as", "is", "are", "was", "were", "be", "been", "it",
    "its", "this", "that", "these", "those", "we", "you", "they", "he", "she",
    "his", "her", "their", "our", "your", "my", "me", "i", "not", "no", "do",
    "does", "did", "can", "could", "will", "would", "should", "may", "might",
    "about", "into", "than", "then", "when", "how", "why", "what", "which",
    "who", "whom", "there", "here", "if", "so", "because", "through", "over",
    "under", "after", "before", "between", "most", "more", "some", "any", "all",
}


def _project_dir(ctx: Ctx) -> Path:
    project = ctx.bb.get_project(ctx.project_id)
    path = OUTPUTS_DIR / str(project["slug"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def _library_off(ctx: Ctx) -> bool:
    import os

    if str(ctx.config.get("library", "on")).lower() in {"off", "0", "false"}:
        return True
    return bool(os.environ.get("YT_LIBRARY_OFF"))


def _used_asset_counts(ctx: Ctx) -> dict[str, int]:
    """Per-project asset usage — QC assertion 4 caps repetition at 3."""
    counts: dict[str, int] = {}
    for row in ctx.bb.conn.execute(
        "SELECT asset_sha256 AS sha, COUNT(*) AS n FROM scenes "
        "WHERE project_id = ? AND asset_sha256 IS NOT NULL GROUP BY asset_sha256",
        (ctx.project_id,),
    ):
        counts[row["sha"]] = int(row["n"])
    return counts


def _link_library_asset(ctx: Ctx, row, scene) -> str:
    """Copy a library file into the project, approve it, link the scene."""
    src = Path(row["file_path"])
    sha = row["sha256"]
    assets_dir = _project_dir(ctx) / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    dest = assets_dir / f"{sha}{src.suffix or '.png'}"
    if not dest.exists() and src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)
    ctx.bb.conn.execute(
        """INSERT OR IGNORE INTO media_assets
           (sha256, project_id, source_url, source_title, credit,
            license_id, license_url, commercial_ok, retrieved_at, gate_status)
           VALUES (?,?,?,?,?,?,?,1,
                   strftime('%Y-%m-%dT%H:%M:%fZ','now'),'approved')""",
        (
            sha,
            ctx.project_id,
            row["source_url"],
            f"visual library ({row['source']})",
            row["credit"] or "YTFactory visual library",
            row["license_id"],
            row["license_url"],
        ),
    )
    ctx.bb.conn.execute(
        "UPDATE scenes SET asset_sha256 = ?, license_id = ?, gate_status = 'approved' "
        "WHERE project_id = ? AND ord = ?",
        (sha, row["license_id"], ctx.project_id, scene["ord"]),
    )
    return sha


# ==========================================================================
# S09 — scene grouping + A7 visual planning (Phase 3 upgrade)
# ==========================================================================
def _load_scenes(ctx: Ctx) -> list[dict]:
    return [
        dict(r)
        for r in ctx.bb.conn.execute(
            "SELECT * FROM scenes WHERE project_id = ? ORDER BY ord", (ctx.project_id,)
        ).fetchall()
    ]


def _scene_texts(ctx: Ctx, member_ords: list[int]) -> list[str]:
    out = []
    for ord_ in member_ords:
        row = ctx.bb.conn.execute(
            "SELECT text FROM sentences WHERE project_id = ? AND ord = ?",
            (ctx.project_id, ord_),
        ).fetchone()
        if row:
            out.append(row["text"])
    return out


def _keywords(text: str, topic: str, limit: int = 4) -> list[str]:
    words: list[str] = []
    for token in (topic + " " + text).lower().replace("-", " ").split():
        token = token.strip(".,;:!?()[]{}\"'`")
        if len(token) < 4 or token in _STOPWORDS or token.isdigit() or token in words:
            continue
        words.append(token)
        if len(words) >= limit:
            break
    return words or ["educational illustration"]


def _deterministic_plan(scenes: list[dict], texts: dict[int, list[str]], topic: str) -> dict:
    """Zero-cost A7 fallback: keyword-driven queries + motion cycling."""
    plan_scenes = []
    for idx, scene in enumerate(scenes):
        members = json.loads(scene["sentence_ids_json"])
        joined = " ".join(texts.get(scene["ord"], []))
        head = (joined[:137] + "...") if len(joined) > 140 else joined
        queries = list(dict.fromkeys(_keywords(joined, topic)))[:5]
        plan_scenes.append(
            {
                "ord": scene["ord"],
                "sentence_ids": members,
                "visual_intent": f"Illustrate: {head}" if head else f"Illustrate {topic}",
                "stock_queries": queries or [topic],
                "gen_prompt": f"photorealistic illustration of {topic}: {head}"[:400],
                "motion": _MOTION_CYCLE[idx % len(_MOTION_CYCLE)],
            }
        )
    return {"scenes": plan_scenes}


def _llm_keys_present() -> bool:
    import os

    return any(
        os.environ.get(k)
        for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY", "OPENAI_API_KEY")
    )


def run_s09(ctx: Ctx) -> dict:
    """Deterministic scene grouping + A7 visual intent (LLM with free fallback).

    Grouping is duration-aware (word estimate @150 wpm) so scene spans stay
    inside QC assertion 2's 1.5–12 s window instead of blind N-per-scene.
    """
    from ytfactory.agents import a07_scene_director as a07

    sentences = ctx.bb.conn.execute(
        "SELECT ord, text FROM sentences WHERE project_id = ? ORDER BY ord",
        (ctx.project_id,),
    ).fetchall()
    if not sentences:
        raise RuntimeError("S09 needs sentences; S06 must run first")
    target_ms = int(ctx.config.get("scene_target_ms", 9000))
    max_per_scene = int(ctx.config.get("sentences_per_scene", 4))

    groups: list[list[int]] = []
    current: list[int] = []
    est_ms = 0.0
    for row in sentences:
        words = len((row["text"] or "").split())
        sentence_ms = words / 150.0 * 60000.0
        if current and (est_ms + sentence_ms > target_ms or len(current) >= max_per_scene):
            groups.append(current)
            current, est_ms = [], 0.0
        current.append(int(row["ord"]))
        est_ms += sentence_ms
    if current:
        groups.append(current)

    ctx.bb.conn.execute("DELETE FROM scenes WHERE project_id = ?", (ctx.project_id,))
    for scene_ord, members in enumerate(groups, start=1):
        ctx.bb.conn.execute(
            "INSERT INTO scenes (project_id, ord, sentence_ids_json, gate_status) "
            "VALUES (?,?,?, 'pending')",
            (ctx.project_id, scene_ord, json.dumps(members)),
        )

    scenes = _load_scenes(ctx)
    texts = {s["ord"]: _scene_texts(ctx, json.loads(s["sentence_ids_json"])) for s in scenes}
    project = ctx.bb.get_project(ctx.project_id)
    payload = {
        "topic": project["topic"],
        "audience": ctx.config.get("audience", "beginner"),
        "scenes": [
            {"ord": s["ord"], "sentence_ids": json.loads(s["sentence_ids_json"]),
             "text": " ".join(texts[s["ord"]])}
            for s in scenes
        ],
    }

    plan: dict[str, Any] | None = None
    method = "deterministic"
    stub = ctx.stub("A7", a07.STUB)
    if ctx.dry_run:
        # free schema validation of the A7 stub (Phase-1 firewall convention)
        a07.run(ctx.router, payload, stub=stub)
        plan = _deterministic_plan(scenes, texts, project["topic"])
        plan = _merge_stub_into(plan, stub, scenes)
        method = "dry-stub+rules"
    elif _llm_keys_present():
        try:
            result = a07.run(ctx.router, payload, stub=stub)
            plan = result.model_dump()
            method = "A7-llm"
            from ytfactory.steps.common import record_usage

            record_usage(ctx, "S09_SCENE_PLAN")
        except Exception as exc:  # noqa: BLE001 - deterministic fallback (user decision)
            ctx.note(f"A7 LLM unavailable ({type(exc).__name__}); deterministic planner used")
    if plan is None:
        plan = _deterministic_plan(scenes, texts, project["topic"])

    by_ord = {p["ord"]: p for p in plan.get("scenes", [])}
    applied = 0
    for scene in scenes:
        entry = by_ord.get(scene["ord"])
        if entry is None:
            continue
        queries = [q for q in entry.get("stock_queries", []) if q][:5]
        motion = entry.get("motion") or _MOTION_CYCLE[0]
        ctx.bb.conn.execute(
            "UPDATE scenes SET visual_intent = ?, stock_queries_json = ?, "
            "gen_prompt = ?, motion = ? WHERE project_id = ? AND ord = ?",
            (
                entry.get("visual_intent", ""),
                json.dumps(queries),
                entry.get("gen_prompt", ""),
                motion,
                ctx.project_id,
                scene["ord"],
            ),
        )
        applied += 1

    return {
        "scene_count": len(groups),
        "scene_target_ms": target_ms,
        "planner": method,
        "planned_scenes": applied,
        "groups": [{"ord": i + 1, "sentence_ords": g} for i, g in enumerate(groups)],
    }


def _merge_stub_into(plan: dict, stub: dict, scenes: list[dict]) -> dict:
    """Dry-run: keep deterministic per-scene entries, echo the stub's scene 1
    through the schema-validated path so the firewall is actually exercised."""
    by_ord = {p["ord"]: p for p in plan["scenes"]}
    stub_scenes = {s["ord"]: s for s in stub.get("scenes", [])}
    target = scenes[0]["ord"] if scenes else None
    if target in stub_scenes and target in by_ord:
        stub_entry = dict(stub_scenes[target])
        stub_entry["sentence_ids"] = json.loads(scenes[0]["sentence_ids_json"])
        by_ord[target] = stub_entry
    plan["scenes"] = [by_ord[o] for o in sorted(by_ord)]
    return plan


# ==========================================================================
# S11 — D5 stock sourcing
# ==========================================================================
def run_s11(ctx: Ctx) -> dict:
    """Query keyless stock APIs, download best candidate per scene."""
    if ctx.dry_run:
        return {"dry_run": True, "step": "S11_VISUAL_SOURCE", "phase": "Phase 3"}

    from ytfactory.workers import stock
    from ytfactory.workers.license_wall import classify

    scenes = _load_scenes(ctx)
    if not scenes:
        raise RuntimeError("S11 needs scenes from S09")
    project = ctx.bb.get_project(ctx.project_id)
    assets_dir = _project_dir(ctx) / "assets"

    queries: list[str] = []
    scene_queries: dict[int, list[str]] = {}
    for scene in scenes:
        qs = json.loads(scene["stock_queries_json"] or "[]")
        if not qs:
            qs = [project["topic"]]
        scene_queries[scene["ord"]] = qs
        queries.extend(qs)
    queries = list(dict.fromkeys(queries))

    # -- Phase 6.2 rung 1: local reusable library (strict tag match) -----
    linked: dict[int, str] = {}
    library_reused = 0

    # -- Stock rung (primary): query Pixabay, Pexels, NASA for fresh media --
    stock_queries = list(
        dict.fromkeys(q for qs in scene_queries.values() for q in qs)
    )
    results, errors = ([], [])
    if stock_queries:
        results, errors = stock.search_all(
            stock_queries, per_source=int(ctx.config.get("stock_per_source", 4))
        )
    if errors:
        ctx.note(f"stock sources degraded: {len(errors)} query/source errors")

    per_scene: dict[int, list[dict]] = {s["ord"]: [] for s in scenes}
    for item in results:
        for scene_ord, qs in scene_queries.items():
            if item.get("_query") in qs:
                per_scene[scene_ord].append(item)

    # URLs already used by this project's scenes — prefer fresh assets (QC #4)
    used_counts: dict[str, int] = {}
    for row in ctx.bb.conn.execute(
        "SELECT source_url FROM media_assets WHERE project_id = ? AND source_url IS NOT NULL",
        (ctx.project_id,),
    ):
        used_counts[row["source_url"]] = used_counts.get(row["source_url"], 0) + 1

    downloads = 0
    for scene in scenes:
        candidates = per_scene.get(scene["ord"], [])
        if not candidates:
            intent_words = [w for w in (scene.get("visual_intent") or "").split() if len(w) > 3][:2]
            fallback_qs = [f"{project['topic']} {' '.join(intent_words)}".strip(), project["topic"]]
            fb_res, _ = stock.search_all(fallback_qs, per_source=6)
            candidates = fb_res
        # license pre-filter: never download something D6 will block anyway
        usable = []
        for cand in candidates:
            lic_id, ok, lic_url = classify(cand.get("license_name", ""), cand.get("license_url", ""))
            cand["_license"] = (lic_id, ok, lic_url)
            if ok:
                usable.append(cand)
        # prefer fresh assets; cap reuse at max 2 times (QC assertion 4)
        usable_fresh = [c for c in usable if used_counts.get(c.get("url"), 0) == 0]
        usable_reusable = [c for c in usable if 0 < used_counts.get(c.get("url"), 0) < 3]
        ordered = usable_fresh + usable_reusable
        for cand in ordered[:6]:
            try:
                path, sha = stock.download_image(cand["url"], assets_dir)
            except Exception:  # noqa: BLE001 - candidate death skips to next
                continue
            downloads += 1
            used_counts[cand["url"]] = used_counts.get(cand["url"], 0) + 1
            ctx.bb.conn.execute(
                """INSERT OR IGNORE INTO media_assets
                   (sha256, project_id, source_url, source_title, credit,
                    license_id, license_url, commercial_ok, retrieved_at, gate_status)
                   VALUES (?,?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),'pending')""",
                (
                    sha,
                    ctx.project_id,
                    cand["url"],
                    cand.get("title") or "",
                    cand.get("credit") or "",
                    cand["_license"][0],
                    cand["_license"][2] or cand.get("license_url", ""),
                    1 if cand["_license"][1] else 0,
                ),
            )
            linked[scene["ord"]] = sha
            break  # top usable candidate per scene

    # -- Fallback rung: local reusable library for any scenes stock did not cover --
    uncovered = [s for s in scenes if s["ord"] not in linked]
    if uncovered and not _library_off(ctx):
        from ytfactory.workers import vislib

        try:
            vislib.ensure_seeded(
                ctx.bb.conn,
                target=int(ctx.config.get("library_seed", vislib.SEED_TARGET)),
                note=ctx.note,
            )
            used = _used_asset_counts(ctx)
            for scene in uncovered:
                row = vislib.claim(
                    ctx.bb.conn,
                    scene_queries[scene["ord"]],
                    used=used,
                )
                if row is None:
                    continue
                sha = _link_library_asset(ctx, row, scene)
                used[sha] = used.get(sha, 0) + 1
                linked[scene["ord"]] = sha
                library_reused += 1
        except Exception as exc:  # noqa: BLE001 - library is a rung, not a gate
            ctx.note(f"visual library unavailable: {type(exc).__name__}: {exc}")

    for scene in scenes:
        if scene["ord"] in linked:
            ctx.bb.conn.execute(
                "UPDATE scenes SET asset_sha256 = ?, gate_status = 'pending' "
                "WHERE project_id = ? AND ord = ?",
                (linked[scene["ord"]], ctx.project_id, scene["ord"]),
            )

    return {
        "queries_run": len(stock_queries),
        "candidates_found": len(results),
        "scenes_with_asset": len(linked),
        "scenes_total": len(scenes),
        "downloads": downloads,
        "library_reused": library_reused,
        "source_errors": errors[:10],
    }


# ==========================================================================
# S12 — D6 license wall
# ==========================================================================
def run_s12(ctx: Ctx) -> dict:
    """Gate every downloaded asset; snapshot license terms into CAS."""
    if ctx.dry_run:
        return {"dry_run": True, "step": "S12_LICENSE_GATE", "phase": "Phase 3"}

    from ytfactory.workers import license_wall

    rows = [
        dict(r)
        for r in ctx.bb.conn.execute(
            "SELECT * FROM media_assets WHERE project_id = ? AND gate_status = 'pending'",
            (ctx.project_id,),
        ).fetchall()
    ]
    approved = blocked = 0
    blocked_detail: list[dict] = []
    for asset in rows:
        lic_id, ok, lic_url = license_wall.classify(
            asset.get("license_id") or "", asset.get("license_url") or ""
        )
        # re-derive from the raw names stored at ingest (id may be 'unknown')
        if asset["license_id"] == "unknown":
            ok = False
        snapshot_sha = None
        if ok and (asset.get("license_url") or lic_url):
            snapshot_sha = license_wall.snapshot_terms(asset.get("license_url") or lic_url)
        gate = "approved" if ok else "blocked"
        ctx.bb.conn.execute(
            "UPDATE media_assets SET gate_status = ?, commercial_ok = ?, "
            "license_id = ?, terms_html_sha = COALESCE(?, terms_html_sha) "
            "WHERE project_id = ? AND sha256 = ?",
            (gate, 1 if ok else 0, lic_id or asset["license_id"], snapshot_sha,
             ctx.project_id, asset["sha256"]),
        )
        ctx.bb.conn.execute(
            "UPDATE scenes SET gate_status = ? WHERE project_id = ? AND asset_sha256 = ?",
            (gate, ctx.project_id, asset["sha256"]),
        )
        if ok:
            approved += 1
        else:
            blocked += 1
            blocked_detail.append({"sha": asset["sha256"][:12],
                                   "license": asset["license_id"] or "unknown"})
    if blocked:
        ctx.note(f"license wall blocked {blocked} asset(s); typographic fallback will cover")
    return {"approved": approved, "blocked": blocked, "blocked_detail": blocked_detail[:10]}


# ==========================================================================
# S13 — typographic card fallback (rung 7)
# ==========================================================================
def _gpu_fill(ctx: Ctx, scenes: list[dict]) -> set[int]:
    """Phase 6.1: batch open scenes' gen prompts onto the GPU pull-bus.
    -> set of scene ords filled (empty when GPU is disabled/unavailable)."""
    from ytfactory.workers import gpubus

    if not gpubus.gpu_enabled(ctx.config):
        return set()
    project = ctx.bb.get_project(ctx.project_id)
    # per-attempt nonce: stale outbox results from an earlier failed batch
    # must never satisfy a fresh enqueue (job ids are otherwise deterministic)
    nonce = f"{time.time_ns():x}"
    jobs: list[dict] = []
    by_job: dict[str, dict] = {}
    for scene in scenes:
        prompt = (
            scene.get("gen_prompt")
            or scene.get("visual_intent")
            or " ".join(json.loads(scene["stock_queries_json"] or "[]"))
            or project["topic"]
        )
        prompt = str(prompt).strip()[:600]
        jid = f"scene{scene['ord']:02d}-" + hashlib.sha256(
            f"{prompt}|{nonce}".encode()
        ).hexdigest()[:8]
        jobs.append(
            {
                "job_id": jid,
                "prompt": prompt,
                "seed": int(
                    hashlib.sha256(f"{jid}|{ctx.project_id}".encode()).hexdigest()[:8], 16
                ),
            }
        )
        by_job[jid] = scene
    results = gpubus.run_batch(
        jobs, cfg=ctx.config, dest_dir=_project_dir(ctx) / "assets", note=ctx.note
    )
    if not results:
        return set()

    filled: set[int] = set()
    assets_dir = _project_dir(ctx) / "assets"
    for jid, res in results.items():
        scene = by_job.get(jid)
        if scene is None:
            continue
        path = Path(res["path"])
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        dest = assets_dir / f"{sha}{path.suffix or '.png'}"
        if path.resolve() != dest.resolve():
            if dest.exists():
                path.unlink(missing_ok=True)
            else:
                path.replace(dest)
        meta = res["result"]
        license_id = str(meta.get("license_id") or "internal_synthetic")
        ctx.bb.conn.execute(
            """INSERT OR IGNORE INTO media_assets
               (sha256, project_id, source_url, source_title, credit,
                license_id, license_url, commercial_ok, retrieved_at, gate_status)
               VALUES (?,?,NULL,?,?,?,NULL,1,
                       strftime('%Y-%m-%dT%H:%M:%fZ','now'),'approved')""",
            (
                sha,
                ctx.project_id,
                f"generated ({meta.get('model', 'gpu')})",
                f"GPU worker {meta.get('worker', 'unknown')}",
                license_id,
            ),
        )
        ctx.bb.conn.execute(
            "UPDATE scenes SET asset_sha256 = ?, license_id = ?, gate_status = 'approved' "
            "WHERE project_id = ? AND ord = ?",
            (sha, license_id, ctx.project_id, scene["ord"]),
        )
        filled.add(scene["ord"])
    if filled:
        ctx.note(f"S13 gpu bus: {len(filled)} scene(s) filled from generated images")
    return filled


def _ingest_project_assets(ctx: Ctx) -> None:
    """Phase 6.2: feed this project's approved stock back into the shared
    library so the next video can reuse it (rung 1 next time)."""
    if _library_off(ctx):
        return
    try:
        from ytfactory.workers import license_wall, vislib

        assets_dir = _project_dir(ctx) / "assets"
        rows = ctx.bb.conn.execute(
            """SELECT DISTINCT m.sha256, m.license_id, m.license_url, m.credit,
                              m.source_url, m.source_title, s.stock_queries_json
               FROM media_assets m
               JOIN scenes s ON s.asset_sha256 = m.sha256 AND s.project_id = m.project_id
               WHERE m.project_id = ? AND m.gate_status = 'approved'""",
            (ctx.project_id,),
        ).fetchall()
        for row in rows:
            lic = row["license_id"] or ""
            if lic.startswith("internal") or license_wall.is_self_made(lic):
                continue
            matches = list(assets_dir.glob(f"{row['sha256']}.*"))
            if not matches:
                continue
            vislib.ingest(
                ctx.bb.conn,
                matches[0],
                license_id=lic,
                source="stock",
                tags=json.loads(row["stock_queries_json"] or "[]"),
                license_url=row["license_url"] or "",
                credit=row["credit"] or row["source_title"] or "",
                source_url=row["source_url"] or "",
            )
    except Exception as exc:  # noqa: BLE001 - ingest is best-effort
        ctx.note(f"visual library ingest skipped: {type(exc).__name__}: {exc}")


def run_s13(ctx: Ctx) -> dict:
    """Fill scenes without an approved asset: GPU bus (6.1) -> library
    abstract (6.2) -> typographic card (guaranteed last resort)."""
    if ctx.dry_run:
        return {"dry_run": True, "step": "S13_IMAGE_GEN", "phase": "Phase 6"}

    from ytfactory.workers import license_wall, typographic

    project = ctx.bb.get_project(ctx.project_id)
    cards_dir = _project_dir(ctx) / "cards"
    scenes = _load_scenes(ctx)

    def needs_asset(s: dict) -> bool:
        return not (s.get("asset_sha256") and s.get("gate_status") == "approved")

    kept = sum(1 for s in scenes if not needs_asset(s))
    open_scenes = [s for s in scenes if needs_asset(s)]

    filled: set[int] = set()
    if open_scenes:
        filled |= _gpu_fill(ctx, open_scenes)
    remaining = [s for s in open_scenes if s["ord"] not in filled]

    library_reused = 0
    if remaining and not _library_off(ctx):
        try:
            from ytfactory.workers import vislib

            vislib.ensure_seeded(
                ctx.bb.conn,
                target=int(ctx.config.get("library_seed", vislib.SEED_TARGET)),
                note=ctx.note,
            )
            used = _used_asset_counts(ctx)
            for scene in remaining:
                row = vislib.claim_abstract(ctx.bb.conn, used=used)
                if row is None:
                    break
                sha = _link_library_asset(ctx, row, scene)
                used[sha] = used.get(sha, 0) + 1
                library_reused += 1
                filled.add(scene["ord"])
            remaining = [s for s in remaining if s["ord"] not in filled]
        except Exception as exc:  # noqa: BLE001
            ctx.note(f"visual library unavailable: {type(exc).__name__}: {exc}")

    cards = 0
    for scene in remaining:
        texts = _scene_texts(ctx, json.loads(scene["sentence_ids_json"]))
        headline = " ".join(texts)[:140].strip() or project["topic"]
        card_path = typographic.render_card(
            headline,
            cards_dir / f"scene_{scene['ord']}.png",
            kicker=project["topic"][:60],
        )
        sha = _register_artifact(
            ctx, card_path, "image/png", "typographic_card", "S13_IMAGE_GEN"
        )
        ctx.bb.conn.execute(
            """INSERT INTO media_assets
               (sha256, project_id, source_url, license_id, license_url,
                commercial_ok, retrieved_at, gate_status)
               VALUES (?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),'approved')
               ON CONFLICT(project_id, sha256) DO UPDATE SET gate_status='approved'""",
            (sha, ctx.project_id, None, license_wall.SELF_MADE_ID, None, 1),
        )
        ctx.bb.conn.execute(
            "UPDATE scenes SET asset_sha256 = ?, license_id = ?, gate_status = 'approved' "
            "WHERE project_id = ? AND ord = ?",
            (sha, license_wall.SELF_MADE_ID, ctx.project_id, scene["ord"]),
        )
        cards += 1

    _ingest_project_assets(ctx)
    return {
        "cards_rendered": cards,
        "stock_approved": kept,
        "gpu_generated": len(filled) - library_reused,
        "library_reused": library_reused,
        "scenes_total": len(scenes),
    }


# ==========================================================================
# S14 — audio bed (Phase 6.3)
# ==========================================================================
def run_s14(ctx: Ctx) -> dict:
    """Source a licensed music bed, duck it 6-10 dB under the narration
    (FR-A-07), and write the EBU-normalized master_mix for S16."""
    if ctx.dry_run:
        return {"dry_run": True, "step": "S14_AUDIO_BED", "phase": "Phase 6"}

    from ytfactory.workers import music as music_worker

    s10 = step_outputs(ctx, "S10_TTS")
    narration = Path(s10["master_audio"])
    duration_ms = int(s10["master_duration_ms"])
    proj = _project_dir(ctx)

    track = music_worker.fetch_music(
        ctx.config, work_dir=proj, duration_s=duration_ms / 1000.0 + 2.0, note=ctx.note
    )
    if track is None:
        ctx.note("S14: no music source available — shipping narration-only")
        return {"skipped": True, "step": "S14_AUDIO_BED", "reason": "no music source"}

    work = proj / "music"
    ducked, depth = music_worker.duck_bed(narration, track["path"], work)
    mix_path = proj / "master_mix.wav"
    mix_info = music_worker.build_mix(narration, ducked, mix_path)
    mix_sha = _register_artifact(ctx, mix_path, "audio/wav", "master_mix", "S14_AUDIO_BED")

    # provenance row — flows into CREDITS/SOURCES at S18 (FR-A-06, LC-08)
    file_sha = track.get("file_sha256") or hashlib.sha256(
        Path(track["path"]).read_bytes()
    ).hexdigest()
    ctx.bb.conn.execute(
        """INSERT OR IGNORE INTO media_assets
           (sha256, project_id, source_url, source_title, credit, license_id,
            license_url, commercial_ok, retrieved_at, gate_status)
           VALUES (?,?,?,?,?,?,?,1,
                   strftime('%Y-%m-%dT%H:%M:%fZ','now'),'approved')""",
        (
            file_sha,
            ctx.project_id,
            track.get("url") or None,
            track.get("name") or "music bed",
            track.get("credit") or "",
            track.get("license_id") or "",
            track.get("license_url") or None,
        ),
    )
    ctx.note(
        f"S14 music bed: {track['source']} [{track['license_id']}], "
        f"duck {depth:.1f} dB, mix {mix_info.get('integrated_lufs')} LUFS"
    )
    return {
        "music_path": track["path"],
        "music_source": track["source"],
        "license_id": track["license_id"],
        "duck_depth_db": round(float(depth), 2),
        "music_gain_db": music_worker.MUSIC_GAIN_DB,
        "mix_path": str(mix_path),
        "mix_sha256": mix_sha,
        "mix_lufs": mix_info.get("integrated_lufs"),
        "skipped": False,
    }


# ==========================================================================
# S15 — D9 EDL compiler
# ==========================================================================
def _resolve_asset(ctx: Ctx, scene: dict) -> tuple[str, str, str]:
    """-> (sha256, kind, license_id) with gate validation."""
    from ytfactory.workers import license_wall

    sha = scene.get("asset_sha256")
    if not sha or scene.get("gate_status") != "approved":
        raise RuntimeError(
            f"scene {scene['ord']} has no approved asset (S12/S13 must run first)"
        )
    row = ctx.bb.conn.execute(
        "SELECT license_id FROM media_assets WHERE project_id = ? AND sha256 = ?",
        (ctx.project_id, sha),
    ).fetchone()
    license_id = (row["license_id"] if row else None) or "unknown"
    card = _project_dir(ctx) / "cards" / f"scene_{scene['ord']}.png"
    kind = "image"
    if license_wall.is_self_made(license_id):
        kind = "typographic_card"
    elif card.exists() and hashlib.sha256(card.read_bytes()).hexdigest() == sha:
        kind = "typographic_card"
    else:
        matches = list((_project_dir(ctx) / "assets").glob(f"{sha}.*"))
        if matches and matches[0].suffix.lower() in (".mp4", ".webm", ".mov", ".mkv"):
            kind = "video"
    return sha, kind, license_id


def run_s15(ctx: Ctx) -> dict:
    """Frame-exact EDL from the S10 master clock (design.md §6.1)."""
    if ctx.dry_run:
        return {"dry_run": True, "step": "S15_EDL_COMPILE", "phase": "Phase 3"}

    from ytfactory.schemas import EDLScene, EDLSceneAsset

    s10 = step_outputs(ctx, "S10_TTS")
    scenes = _load_scenes(ctx)
    if any(s.get("start_ms") is None or s.get("end_ms") is None for s in scenes):
        raise RuntimeError("S15 needs live S10 clock (scenes.start_ms/end_ms)")
    master_ms = int(s10["master_duration_ms"])
    audio_frames = round(master_ms * FPS / 1000.0)

    start_frames = [round(int(s["start_ms"]) * FPS / 1000.0) for s in scenes]
    frames: list[int] = []
    for i, scene in enumerate(scenes):
        if i + 1 < len(scenes):
            frames.append(start_frames[i + 1] - start_frames[i])
        else:
            frames.append(audio_frames - start_frames[i])
    if any(f < 1 for f in frames):
        raise RuntimeError(f"EDL frame underflow: {frames}")
    if sum(frames) != audio_frames:
        raise RuntimeError(
            f"EDL frame mismatch: sum={sum(frames)} audio_frames={audio_frames}"
        )

    edl_scenes = []
    for scene, n_frames in zip(scenes, frames):
        sha, kind, lic = _resolve_asset(ctx, scene)
        edl_scenes.append(
            EDLScene(
                ord=scene["ord"],
                start_ms=int(scene["start_ms"]),
                end_ms=int(scene["end_ms"]),
                duration_ms=int(scene["end_ms"]) - int(scene["start_ms"]),
                frames=n_frames,
                asset=EDLSceneAsset(
                    sha256=sha, kind=kind, license_id=lic, gate_status="approved"
                ),
                motion={"type": scene.get("motion") or "kenburns_zoom_in"},
            )
        )
        ctx.bb.conn.execute(
            "UPDATE scenes SET frames = ? WHERE project_id = ? AND ord = ?",
            (n_frames, ctx.project_id, scene["ord"]),
        )

    edl = EDLSchema(
        master_audio_sha256=s10["master_sha256"],
        master_audio_lufs=float(s10.get("integrated_lufs", -14.0)),
        master_audio_duration_ms=master_ms,
        scenes=edl_scenes,
    )
    edl_path = _project_dir(ctx) / "edl.json"
    edl_path.write_text(edl.model_dump_json(indent=2), encoding="utf-8")
    _register_artifact(ctx, edl_path, "application/json", "edl", "S15_EDL_COMPILE")
    return {
        "edl_path": str(edl_path),
        "fps": FPS,
        "audio_frames": audio_frames,
        "scene_count": len(edl_scenes),
        "master_duration_ms": master_ms,
    }


# ==========================================================================
# S16 — D10 renderer
# ==========================================================================
def _pick_encoder(ctx: Ctx) -> str:
    pref = ctx.config.get("video_encoder", "auto")
    if pref and pref != "auto":
        return pref
    if not hasattr(_pick_encoder, "_probed"):
        ok = False
        try:
            proc = subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=black:s=320x240:r=30",
                 "-frames:v", "2", "-c:v", "h264_qsv", "-f", "null", "-"],
                capture_output=True, timeout=30,
            )
            ok = proc.returncode == 0
        except Exception:  # noqa: BLE001
            ok = False
        _pick_encoder._probed = "h264_qsv" if ok else "libx264"
        if not ok:
            ctx.note("h264_qsv probe failed; using libx264")
    return _pick_encoder._probed


def _motion_box(motion: str, cw: int, ch: int, t: float) -> tuple[int, int, int, int]:
    """Crop box (x, y, w, h) inside the canvas for frame time t in [0, 1]."""
    w_min, h_min = OUT_W, OUT_H           # tightest (max zoom-in)
    mid_w, mid_h = (cw + OUT_W) // 2, (ch + OUT_H) // 2
    mx, my = cw - mid_w, ch - mid_h
    if motion == "kenburns_zoom_in":
        w = int(round(cw + (w_min - cw) * t))
        h = int(round(ch + (h_min - ch) * t))
    elif motion == "kenburns_zoom_out":
        w = int(round(w_min + (cw - w_min) * t))
        h = int(round(h_min + (ch - h_min) * t))
    elif motion in ("pan_left", "pan_right", "pan_up", "pan_down"):
        w, h = mid_w, mid_h
        x = int(round(mx * (1 - t))) if motion == "pan_left" else int(round(mx * t))
        y = int(round(my * (1 - t))) if motion == "pan_up" else int(round(my * t))
        if motion in ("pan_right", "pan_left"):
            y = my // 2
        else:
            x = mx // 2
        return x, y, w, h
    else:  # static / unknown -> gentle center frame
        w, h = mid_w, mid_h
    return (cw - w) // 2, (ch - h) // 2, w, h


def _leading_black_duration(path: Path) -> float:
    """Return seconds of leading black frames if video starts with black (e.g. fade in), else 0.0."""
    if shutil.which("ffmpeg") is None:
        return 0.0
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-t", "5", "-i", str(path),
                "-vf", "blackdetect=d=0.2:pix_th=0.10",
                "-an", "-f", "null", "-",
            ],
            capture_output=True, timeout=15,
        )
        if proc.returncode == 0:
            stderr = proc.stderr.decode("utf-8", "replace")
            m = re.search(r"black_start:(?:0(?:\.0*)?)\s+black_end:([\d.]+)", stderr)
            if m:
                return float(m.group(1))
    except Exception:
        pass
    return 0.0


def _render_segment(
    source_path: Path, motion: str, n_frames: int, seg_path: Path, encoder: str
) -> None:
    from PIL import Image, ImageOps

    seg_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = source_path.suffix.lower()
    if suffix in (".mp4", ".webm", ".mov", ".mkv"):
        skip_s = _leading_black_duration(source_path)
        cmd = ["ffmpeg", "-y"]
        if skip_s > 0:
            cmd += ["-ss", str(skip_s)]
        cmd += [
            "-stream_loop", "-1",
            "-i", str(source_path),
            "-vf", f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H}",
            "-frames:v", str(n_frames),
            "-r", str(FPS),
            "-an", "-c:v", encoder, "-pix_fmt", "yuv420p",
            "-threads", "2",
        ]
        if encoder == "libx264":
            cmd += ["-preset", "veryfast", "-crf", "20"]
        cmd += [str(seg_path)]
        proc = subprocess.run(cmd, capture_output=True, timeout=300)
        if proc.returncode != 0:
            tail = (proc.stderr or b"")[-500:].decode(errors="replace")
            raise RuntimeError(f"segment video encode failed (rc={proc.returncode}): {tail}")
        return

    canvas_box = (int(OUT_W * CANVAS_SCALE), int(OUT_H * CANVAS_SCALE))
    src = Image.open(source_path).convert("RGB")
    canvas = ImageOps.fit(src, canvas_box, method=Image.LANCZOS)

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s:v", f"{OUT_W}x{OUT_H}",
        "-r", str(FPS), "-i", "pipe:0",
        "-an", "-c:v", encoder, "-pix_fmt", "yuv420p",
        "-threads", "2",
    ]
    if encoder == "libx264":
        cmd += ["-preset", "veryfast", "-crf", "20"]
    cmd += [str(seg_path)]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert proc.stdin is not None
        for i in range(n_frames):
            t = i / max(n_frames - 1, 1)
            x, y, w, h = _motion_box(motion, canvas_box[0], canvas_box[1], t)
            frame = canvas.crop((x, y, x + w, y + h)).resize((OUT_W, OUT_H), Image.LANCZOS)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        # communicate() drains stderr while waiting (avoids pipe-full deadlock)
        _, err = proc.communicate(timeout=300)
        rc = proc.returncode
    except Exception:
        proc.kill()
        proc.communicate()
        raise
    if rc != 0:
        tail = (err or b"")[-500:].decode(errors="replace")
        raise RuntimeError(f"segment encode failed (rc={rc}): {tail}")


def _resolve_audio_input(ctx: Ctx, s10: dict) -> Path:
    """S16 audio: the EBU mix from S14 when it exists, else S10 narration."""
    narration = Path(s10["master_audio"])
    try:  # Phase 6.3: prefer the ducked music mix when S14 produced one
        s14 = step_outputs(ctx, "S14_AUDIO_BED")
        if not s14.get("skipped"):
            mix = s14.get("mix_path")
            if mix and Path(mix).exists():
                return Path(mix)
    except RuntimeError:
        pass
    return narration


def run_s16(ctx: Ctx) -> dict:
    """Render EDL scenes -> segments -> concat -> mux master audio -> final.mp4."""
    if ctx.dry_run:
        return {"dry_run": True, "step": "S16_RENDER", "phase": "Phase 3"}

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH — install ffmpeg for S16_RENDER")

    s15 = step_outputs(ctx, "S15_EDL_COMPILE")
    s10 = step_outputs(ctx, "S10_TTS")
    edl = json.loads(Path(s15["edl_path"]).read_text(encoding="utf-8"))
    encoder = _pick_encoder(ctx)
    proj_dir = _project_dir(ctx)
    seg_dir = proj_dir / "segments"
    cards_dir = proj_dir / "cards"
    assets_dir = proj_dir / "assets"

    rendered = cache_hits = 0
    seg_paths: list[Path] = []
    for scene in edl["scenes"]:
        sha = scene["asset"]["sha256"]
        key = hashlib.sha256(
            "|".join(
                [
                    sha,
                    json.dumps(scene["motion"], sort_keys=True),
                    str(scene["frames"]),
                    encoder,
                    f"{OUT_W}x{OUT_H}@{FPS}",
                ]
            ).encode()
        ).hexdigest()[:24]
        seg_path = seg_dir / f"{key}.mp4"
        if seg_path.exists() and seg_path.stat().st_size > 0:
            cache_hits += 1
            seg_paths.append(seg_path)
            continue
        source = None
        card = cards_dir / f"scene_{scene['ord']}.png"
        if card.exists() and hashlib.sha256(card.read_bytes()).hexdigest() == sha:
            source = card
        if source is None:
            matches = list(assets_dir.glob(f"{sha}.*"))
            if not matches:
                raise RuntimeError(f"asset file missing for scene {scene['ord']}: {sha[:12]}")
            source = matches[0]
        _render_segment(source, scene["motion"]["type"], scene["frames"], seg_path, encoder)
        rendered += 1
        seg_paths.append(seg_path)

    list_file = proj_dir / "concat.txt"
    list_file.write_text(
        "".join(f"file '{p.as_posix()}'\n" for p in seg_paths), encoding="utf-8"
    )
    parts = proj_dir / "video_parts.mp4"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-c", "copy", str(parts)],
        capture_output=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError("concat failed: " + proc.stderr.decode(errors="replace")[-400:])

    final = proj_dir / "final.mp4"
    audio_input = _resolve_audio_input(ctx, s10)
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(parts), "-i", str(audio_input),
         "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", str(final)],
        capture_output=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError("mux failed: " + proc.stderr.decode(errors="replace")[-400:])

    probe = _probe(final)
    master_ms = int(s10["master_duration_ms"])
    if probe:
        if abs(probe["width"] - OUT_W) > 0 or abs(probe["height"] - OUT_H) > 0:
            raise RuntimeError(f"final.mp4 geometry wrong: {probe}")
        if abs(probe["duration_s"] * 1000.0 - master_ms) > 1000.0:
            raise RuntimeError(
                f"final.mp4 duration {probe['duration_s']:.2f}s vs master {master_ms}ms"
            )

    sha = _register_artifact(ctx, final, "video/mp4", "final_video", "S16_RENDER")
    return {
        "final_path": str(final),
        "sha256": sha,
        "encoder": encoder,
        "segments_rendered": rendered,
        "segments_cached": cache_hits,
        "duration_ms": probe["duration_s"] * 1000.0 if probe else master_ms,
        "width": OUT_W,
        "height": OUT_H,
        "fps": FPS,
    }


def _probe(path: Path) -> dict | None:
    if shutil.which("ffprobe") is None:
        return None
    proc = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, timeout=60,
    )
    if proc.returncode != 0:
        return None
    data = json.loads(proc.stdout.decode("utf-8", errors="replace") or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        return None
    return {
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "duration_s": float(data.get("format", {}).get("duration", 0) or 0),
        "avg_frame_rate": video.get("avg_frame_rate") or video.get("r_frame_rate"),
        "pix_fmt": video.get("pix_fmt"),
        "codec": video.get("codec_name"),
    }
