"""Phase 2 steps: S09 minimal scene grouping, S10 TTS master clock, S17 captions.

S09 here is the deterministic sentence→scene grouper; Phase 3's A7 Scene
Director upgrades the visual-intent fields in place (visual_intent,
gen_prompt, stock_queries_json).
"""
from __future__ import annotations

import json
from pathlib import Path

from ytfactory.steps.common import Ctx, step_outputs
from ytfactory.workers import align as aligner
from ytfactory.workers import captions as cap
from ytfactory.workers import audio as wav

OUTPUTS_DIR = Path(__file__).resolve().parent.parent.parent / "outputs"


def _project_dir(ctx: Ctx) -> Path:
    slug = ctx.bb.get_project(ctx.project_id)["slug"]
    path = OUTPUTS_DIR / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def _register_artifact(ctx: Ctx, path: Path, mime: str, role: str, step: str) -> str:
    sha = wav.sha256_file(str(path))
    ctx.bb.conn.execute(
        """INSERT OR IGNORE INTO artifacts (sha256, mime, size_bytes, storage_path, role, provenance)
           VALUES (?,?,?,?,?,?)""",
        (sha, mime, path.stat().st_size, str(path), role, step),
    )
    ctx.bb.conn.execute(
        "DELETE FROM project_artifacts WHERE project_id = ? AND role = ?",
        (ctx.project_id, role),
    )
    ctx.bb.conn.execute(
        "INSERT OR IGNORE INTO project_artifacts (project_id, sha256, role) VALUES (?,?,?)",
        (ctx.project_id, sha, role),
    )
    return sha


# --------------------------------------------------------------------------
def run_s10(ctx: Ctx) -> dict:
    """TTS → measured durations → sample-level concat → EBU R128 master track."""
    if ctx.dry_run:
        return {"dry_run": True, "step": "S10_TTS", "phase": "Phase 2"}

    from ytfactory.backends import selection_chain
    from ytfactory.backends.base import sentence_cache_name

    sentences = [
        dict(r)
        for r in ctx.bb.conn.execute(
            "SELECT ord, text FROM sentences WHERE project_id = ? ORDER BY ord",
            (ctx.project_id,),
        ).fetchall()
    ]
    if not sentences:
        raise RuntimeError("S10 needs sentences from S06")

    scenes = [
        dict(r)
        for r in ctx.bb.conn.execute(
            "SELECT ord, sentence_ids_json FROM scenes WHERE project_id = ? ORDER BY ord",
            (ctx.project_id,),
        ).fetchall()
    ]
    if not scenes:
        raise RuntimeError("S10 needs scenes from S09")

    voice = ctx.config.get("tts_voice", "af_heart")
    by_ord = {s["ord"]: s for s in sentences}
    scene_payload = []
    for scene in scenes:
        member_ords = json.loads(scene["sentence_ids_json"])
        scene_payload.append(
            {
                "ord": scene["ord"],
                "sentences": [
                    {
                        "ord": o,
                        "text": by_ord[o]["text"],
                        "voice": voice,
                    }
                    for o in member_ords
                    if o in by_ord
                ],
            }
        )

    tts_dir = _project_dir(ctx) / "tts"
    chain = selection_chain(ctx.config)
    if not chain:
        raise RuntimeError(
            "no TTS backend available: install edge-tts (`pip install edge-tts`), "
            "kokoro-onnx, piper-tts, or configure tts_hf_repo for Kaggle batch"
        )

    used = None
    errors: list[str] = []
    for backend in chain:
        try:
            backend.synthesize(scene_payload, str(tts_dir))
            used = backend.name
            break
        except Exception as exc:
            errors.append(f"{backend.name}: {exc}")
            ctx.note(f"TTS backend {backend.name} failed, trying next: {exc}")
    if used is None:
        raise RuntimeError("all TTS backends failed: " + " | ".join(errors))

    gap_ms = float(ctx.config.get("sentence_gap_ms", wav.DEFAULT_GAP_MS))
    sentence_wavs = []
    audio_sha: dict[int, str] = {}
    for sent in sentences:
        path = tts_dir / sentence_cache_name(sent["ord"], sent["text"], voice)
        if not path.exists():
            raise RuntimeError(f"backend {used} produced no WAV for sentence {sent['ord']}")
        samples, sr = wav.read_wav(str(path))
        sentence_wavs.append((sent["ord"], samples, sr))
        audio_sha[sent["ord"]] = wav.sha256_file(str(path))

    master, clock, sr = wav.sentence_timeline(sentence_wavs, gap_ms=gap_ms)
    master, measurements = wav.normalize_ebu(master, sr)
    master_path = _project_dir(ctx) / "master_audio.wav"
    wav.write_wav(str(master_path), master, sr)
    master_sha = _register_artifact(ctx, master_path, "audio/wav", "master_audio", "S10_TTS")

    for sent in sentences:
        tick = clock[sent["ord"]]
        ctx.bb.conn.execute(
            "UPDATE sentences SET start_ms = ?, duration_ms = ?, audio_sha256 = ? "
            "WHERE project_id = ? AND ord = ?",
            (
                round(tick["start_ms"], 3),
                round(tick["duration_ms"], 3),
                audio_sha[sent["ord"]],
                ctx.project_id,
                sent["ord"],
            ),
        )

    for scene in scenes:
        member_ords = json.loads(scene["sentence_ids_json"])
        present = [o for o in member_ords if o in clock]
        if not present:
            continue
        ctx.bb.conn.execute(
            "UPDATE scenes SET start_ms = ?, end_ms = ? WHERE project_id = ? AND ord = ?",
            (
                round(clock[present[0]]["start_ms"], 3),
                round(clock[present[-1]]["start_ms"] + clock[present[-1]]["duration_ms"], 3),
                ctx.project_id,
                scene["ord"],
            ),
        )

    total_ms = wav.duration_ms(master, sr)
    return {
        "backend_used": used,
        "master_audio": str(master_path),
        "master_sha256": master_sha,
        "sample_rate": sr,
        "master_duration_ms": round(total_ms, 3),
        "sentence_count": len(sentences),
        "gap_ms": gap_ms,
        "integrated_lufs": measurements["integrated_lufs"],
        "true_peak_dbtp": measurements["true_peak_dbtp"],
    }


def run_s17(ctx: Ctx) -> dict:
    """Arithmetic SRT/VTT from the master clock + optional D8 alignment/WER."""
    if ctx.dry_run:
        return {"dry_run": True, "step": "S17_CAPTIONS", "phase": "Phase 2"}

    rows = [
        dict(r)
        for r in ctx.bb.conn.execute(
            "SELECT ord, text, start_ms, duration_ms FROM sentences "
            "WHERE project_id = ? ORDER BY ord",
            (ctx.project_id,),
        ).fetchall()
    ]
    if not rows or any(r["start_ms"] is None for r in rows):
        raise RuntimeError("S17 needs measured sentences from S10")

    master = step_outputs(ctx, "S10_TTS")
    master_ms = float(master["master_duration_ms"])

    cues, stats = cap.build_cues(rows)
    drift = cap.assert_total(cues, master_ms)

    out_dir = _project_dir(ctx)
    srt_path = out_dir / "captions.srt"
    vtt_path = out_dir / "captions.vtt"
    srt_path.write_text(cap.to_srt(cues), encoding="utf-8")
    vtt_path.write_text(cap.to_vtt(cues), encoding="utf-8")
    _register_artifact(ctx, srt_path, "application/x-subrip", "captions_srt", "S17_CAPTIONS")
    _register_artifact(ctx, vtt_path, "text/vtt", "captions_vtt", "S17_CAPTIONS")

    reference = " ".join(r["text"] for r in rows)
    alignment = aligner.run_alignment(
        master["master_audio"],
        reference,
        ctx.config,
        karaoke=bool(ctx.config.get("karaoke", False)),
    )
    if alignment["wer_score"] is not None:
        ctx.bb.add_event(
            ctx.project_id,
            "d8_wer",
            f"WER {alignment['wer_score']:.1%} via {alignment['method']}",
            metadata={"wer_score": alignment["wer_score"], "method": alignment["method"]},
        )

    return {
        "srt": str(srt_path),
        "vtt": str(vtt_path),
        "cue_count": stats["cue_count"],
        "sum_ms": stats["sum_ms"],
        "master_ms": master_ms,
        "drift_ms": round(drift, 3),
        "max_line_breach": stats["max_line_breach"],
        "wer_score": alignment["wer_score"],
        "alignment_method": alignment["method"],
        "word_timings": None,  # karaoke pass lands with Phase 3 render
    }
