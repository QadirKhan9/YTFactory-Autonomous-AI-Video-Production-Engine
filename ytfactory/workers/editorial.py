"""Deterministic editorial heuristics for A9 (S20).

Runs when no LLM key is present (same zero-cost precedent as A7/A8) and
defines the concrete thresholds the LLM prompt also references. Findings
use the A9 schema vocabulary: dimension in {pacing, tone, hook, visual}.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

WPM_TARGET_MIN, WPM_TARGET_MAX = 130, 150  # FR-S-07 (warning band)
WPM_BLOCK_MIN, WPM_BLOCK_MAX = 80, 250      # catastrophic (blocker band)
HOOK_START_BLOCK_MS = 3000
HOOK_WORDS_WARN = 30
HOOK_MS_WARN = 6000
MOTION_RUN_WARN = 3
CARD_RATIO_WARN = 0.5
AVG_WORDS_WARN = 28
FRAME_DARK_MEAN = 12.0        # /255 mean brightness
FRAME_FLAT_STDDEV = 3.0       # near-zero variation across frames

_META_RE = re.compile(
    r"\b(this sentence|the sentence above|placeholder|todo|lorem ipsum|"
    r"as an ai|i am an ai|fixme|insert text|coming soon)\b",
    re.IGNORECASE,
)


def word_count(text: str) -> int:
    return len((text or "").split())


def pacing_stats(sentences: list[dict], duration_ms: int) -> dict:
    words = sum(word_count(s.get("text", "")) for s in sentences)
    minutes = (duration_ms or 0) / 60000.0
    durations = [
        float(s["duration_ms"]) for s in sentences
        if s.get("duration_ms") is not None
    ]
    return {
        "words": words,
        "wpm": round(words / minutes, 1) if minutes > 0 else None,
        "sentence_count": len(sentences),
        "avg_words": round(words / len(sentences), 1) if sentences else 0,
        "avg_sentence_ms": int(sum(durations) / len(durations)) if durations else None,
        "longest_sentence_ms": int(max(durations)) if durations else None,
    }


def hook_stats(sentences: list[dict]) -> dict:
    if not sentences:
        return {"start_ms": None, "words": 0, "duration_ms": None}
    first = sentences[0]
    return {
        "start_ms": int(first.get("start_ms") or 0),
        "words": word_count(first.get("text", "")),
        "duration_ms": int(first.get("duration_ms") or 0),
    }


def visual_stats(scenes: list[dict]) -> dict:
    motions = [str(s.get("motion") or "static") for s in scenes]
    longest_run, run = 1, 1
    for i in range(1, len(motions)):
        run = run + 1 if motions[i] == motions[i - 1] else 1
        longest_run = max(longest_run, run)
    cards = sum(
        1 for s in scenes
        if (s.get("license_id") or "") == "internal_typographic"
    )
    n = len(scenes)
    return {
        "scene_count": n,
        "unique_motions": len(set(motions)),
        "longest_motion_run": longest_run if motions else 0,
        "card_count": cards,
        "card_ratio": round(cards / n, 2) if n else 0.0,
    }


def sample_frames(video: Path, duration_ms: int, count: int = 6) -> list[dict]:
    """ffmpeg-sample `count` frames -> [{mean, stddev}] brightness stats (Pillow)."""
    if not video.exists() or shutil.which("ffmpeg") is None or duration_ms <= 0:
        return []
    from PIL import Image, ImageStat

    step_s = max(duration_ms / 1000.0 / (count + 1), 0.5)
    out: list[dict] = []
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ytf-frames-") as tmp:
        pattern = str(Path(tmp) / "f_%03d.png")
        try:
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
                 "-vf", f"fps={1.0 / step_s}", "-frames:v", str(count),
                 "-y", pattern],
                capture_output=True, timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if proc.returncode != 0:
            return []
        for path in sorted(Path(tmp).glob("f_*.png")):
            try:
                with Image.open(path) as im:
                    stat = ImageStat.Stat(im.convert("L"))
                    out.append({
                        "mean": round(stat.mean[0], 1),
                        "stddev": round(stat.stddev[0], 1),
                    })
            except OSError:
                continue
    return out


def deterministic_review(payload: dict) -> dict:
    """Heuristic A9 verdict -> dict matching QcReviewSchema."""
    blockers: list[dict] = []
    warnings: list[dict] = []
    notes: list[str] = []

    sentences = payload.get("sentences") or []
    pacing = payload.get("pacing") or {}
    hook = payload.get("hook") or {}
    scenes = payload.get("scenes") or []
    vis = visual_stats(scenes)

    # -- tone: meta/self-referential narration is a must-fix defect
    meta_hits = 0
    for s in sentences[:80]:
        m = _META_RE.search(s.get("text", ""))
        if m:
            meta_hits += 1
            if meta_hits <= 3:
                blockers.append({
                    "dimension": "tone",
                    "message": (f"sentence {s.get('ord')} is self-referential/meta "
                                f"(\"{m.group(0)}\") — rewrite as narration"),
                })
    if meta_hits > 3:
        blockers.append({
            "dimension": "tone",
            "message": f"{meta_hits - 3} more meta/self-referential sentence(s) found",
        })

    # -- pacing
    wpm = pacing.get("wpm")
    if pacing.get("sentence_count", 0) == 0:
        blockers.append({"dimension": "pacing", "message": "script is empty"})
    elif wpm is None:
        notes.append("duration unavailable — WPM not evaluated")
    elif wpm < WPM_BLOCK_MIN or wpm > WPM_BLOCK_MAX:
        blockers.append({
            "dimension": "pacing",
            "message": f"pacing {wpm} WPM is catastrophic (outside {WPM_BLOCK_MIN}–{WPM_BLOCK_MAX})",
        })
    elif wpm < WPM_TARGET_MIN or wpm > WPM_TARGET_MAX:
        warnings.append({
            "dimension": "pacing",
            "message": f"pacing {wpm} WPM outside the 130–150 target (FR-S-07)",
        })
    if (pacing.get("avg_words") or 0) > AVG_WORDS_WARN:
        warnings.append({
            "dimension": "pacing",
            "message": f"sentences average {pacing['avg_words']} words — consider splitting",
        })

    # -- hook
    if not sentences:
        blockers.append({"dimension": "hook", "message": "no hook sentence present"})
    else:
        if hook.get("start_ms") is not None and hook["start_ms"] > HOOK_START_BLOCK_MS:
            blockers.append({
                "dimension": "hook",
                "message": f"hook starts at {hook['start_ms']} ms (> {HOOK_START_BLOCK_MS} ms)",
            })
        if hook.get("words", 0) > HOOK_WORDS_WARN:
            warnings.append({
                "dimension": "hook",
                "message": f"hook is {hook['words']} words — trim to {HOOK_WORDS_WARN} or fewer",
            })
        if hook.get("duration_ms") and hook["duration_ms"] > HOOK_MS_WARN:
            warnings.append({
                "dimension": "hook",
                "message": f"hook runs {hook['duration_ms']} ms — too long before the payoff",
            })

    # -- visual
    if vis["longest_motion_run"] >= MOTION_RUN_WARN:
        warnings.append({
            "dimension": "visual",
            "message": f"{vis['longest_motion_run']} consecutive scenes share one motion type",
        })
    if vis["scene_count"] and vis["card_ratio"] > CARD_RATIO_WARN:
        warnings.append({
            "dimension": "visual",
            "message": f"{int(vis['card_ratio'] * 100)}% of scenes are typographic cards",
        })
    frames = payload.get("frame_stats") or []
    if frames:
        mean_brightness = sum(f["mean"] for f in frames) / len(frames)
        spread = max(f["mean"] for f in frames) - min(f["mean"] for f in frames)
        mean_stddev = sum(f["stddev"] for f in frames) / len(frames)
        if mean_brightness < FRAME_DARK_MEAN:
            warnings.append({
                "dimension": "visual",
                "message": f"sampled frames are very dark (mean {mean_brightness:.0f}/255)",
            })
        if spread < FRAME_FLAT_STDDEV and mean_stddev < FRAME_FLAT_STDDEV:
            warnings.append({
                "dimension": "visual",
                "message": "sampled frames show almost no brightness variation",
            })
        notes.append(f"sampled {len(frames)} frames, mean brightness {mean_brightness:.0f}/255")

    notes.append(
        f"pacing {wpm if wpm is not None else 'n/a'} WPM over {len(sentences)} sentences; "
        f"{vis['scene_count']} scenes, card ratio {int(vis['card_ratio'] * 100)}%, "
        f"{vis['unique_motions']} distinct motions"
    )
    return {"blockers": blockers, "warnings": warnings, "notes": notes}
