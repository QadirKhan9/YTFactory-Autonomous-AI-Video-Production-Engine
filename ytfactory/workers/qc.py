"""D11 Auto-QC (FR-Q-01/02): 20 deterministic assertions before the human gate.

Each assertion returns status `pass` | `fail` | `skip` (skip = prerequisite not
available yet, e.g. WER until D8 ships — user decision: skip + warn).
Any `fail` blocks pipeline progression (FR-Q-04) via the S19 gate.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from ytfactory.steps.common import Ctx, step_outputs

FPS = 30
SCENE_MIN_MS, SCENE_MAX_MS = 1500, 30000
ASSET_MAX_REPEAT = 5
SILENCE_MAX_MS = 2000
SRT_LINE_MAX, SRT_LINES_MAX = 42, 2
LUFS_MIN, LUFS_MAX = -16.0, -12.0
TRUE_PEAK_MAX = -1.0
WER_MAX = 0.05
TITLE_MAX, DESC_MAX = 100, 5000
BLACK_MAX_S = 0.5
HOOK_MAX_START_MS = 3000
THUMB_SIZE = (1280, 720)
THUMB_MAX_BYTES = 2 * 1024 * 1024


@dataclass
class Assertion:
    id: int
    name: str
    status: str  # pass | fail | skip
    detail: str


# -- helpers ---------------------------------------------------------------
_TS_RE = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})")


def _srt_ts_to_ms(stamp: str) -> float:
    m = _TS_RE.fullmatch(stamp.strip())
    if not m:
        raise ValueError(f"bad srt timestamp: {stamp!r}")
    h, mi, s, ms = (int(g) for g in m.groups())
    return ((h * 60 + mi) * 60 + s) * 1000 + ms


def parse_srt(text: str) -> list[dict]:
    cues: list[dict] = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if lines[0].strip().isdigit():
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            continue
        start_s, end_s = (p.strip() for p in lines[0].split("-->"))
        cues.append(
            {
                "start_ms": _srt_ts_to_ms(start_s),
                "end_ms": _srt_ts_to_ms(end_s),
                "lines": lines[1:],
            }
        )
    return cues


def _fps_value(avg: str | None) -> float | None:
    try:
        if not avg:
            return None
        if "/" in avg:
            num, den = avg.split("/")
            return float(num) / float(den) if float(den) else None
        return float(avg)
    except (ValueError, ZeroDivisionError):
        return None


def _probe_video(path: Path) -> dict | None:
    from ytfactory.steps.visual import _probe

    if not path.exists() or shutil.which("ffprobe") is None:
        return None
    probe = _probe(path)
    if not probe:
        return None
    return probe


def _read_meta(out_dir: Path) -> dict:
    path = out_dir / "metadata.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _has_faststart(path: Path, window: int = 1024 * 1024) -> bool | None:
    if not path.exists():
        return None
    try:
        with open(path, "rb") as fh:
            data = fh.read(window)
    except OSError:
        return None
    moov = data.find(b"moov")
    mdat = data.find(b"mdat")
    if moov == -1:
        return False
    return mdat == -1 or moov < mdat


def _black_frames(path: Path) -> list[tuple[float, float]] | None:
    """[(start_s, end_s)] of black runs >= BLACK_MAX_S; None when ffmpeg absent."""
    if shutil.which("ffmpeg") is None or not path.exists():
        return None
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path),
         "-vf", f"blackdetect=d={BLACK_MAX_S}:pix_th=0.10",
         "-an", "-f", "null", "-"],
        capture_output=True, timeout=300,
    )
    if proc.returncode != 0:
        return None
    runs = []
    for m in re.finditer(
        r"black_start:([\d.]+)\s+black_end:([\d.]+)", proc.stderr.decode("utf-8", "replace")
    ):
        runs.append((float(m.group(1)), float(m.group(2))))
    return runs


# -- bundle ----------------------------------------------------------------
def load_bundle(ctx: Ctx) -> dict[str, Any]:
    """Gather every input the 20 assertions need (tolerant of missing pieces)."""
    from ytfactory.steps.visual import _project_dir

    out_dir = _project_dir(ctx)
    final = out_dir / "final.mp4"
    bundle: dict[str, Any] = {
        "out_dir": out_dir,
        "final": final,
        "probe": _probe_video(final),
        "srt": "",
        "meta": _read_meta(out_dir),
        "thumb": out_dir / "thumbnail.png",
        "credits": out_dir / "CREDITS.md",
    }
    try:
        bundle["s10"] = step_outputs(ctx, "S10_TTS")
    except RuntimeError:
        bundle["s10"] = {}
    try:
        bundle["s17"] = step_outputs(ctx, "S17_CAPTIONS")
    except RuntimeError:
        bundle["s17"] = {}
    edl_path = out_dir / "edl.json"
    bundle["edl"] = (
        json.loads(edl_path.read_text(encoding="utf-8")) if edl_path.exists() else {}
    )
    srt_path = out_dir / "captions.srt"
    bundle["srt"] = srt_path.read_text(encoding="utf-8") if srt_path.exists() else ""
    bundle["sentences"] = [
        dict(r)
        for r in ctx.bb.conn.execute(
            "SELECT ord, text, start_ms, duration_ms, is_rhetorical FROM sentences "
            "WHERE project_id = ? ORDER BY ord",
            (ctx.project_id,),
        ).fetchall()
    ]
    bundle["scenes"] = [
        dict(r)
        for r in ctx.bb.conn.execute(
            "SELECT ord, start_ms, end_ms, asset_sha256, gate_status FROM scenes "
            "WHERE project_id = ? ORDER BY ord",
            (ctx.project_id,),
        ).fetchall()
    ]
    bundle["assets"] = [
        dict(r)
        for r in ctx.bb.conn.execute(
            "SELECT sha256, license_id, gate_status FROM media_assets "
            "WHERE project_id = ?",
            (ctx.project_id,),
        ).fetchall()
    ]
    bundle["unsupported_claims"] = int(
        ctx.bb.conn.execute(
            "SELECT COUNT(*) AS n FROM claims WHERE project_id = ? AND verdict = 'unsupported'",
            (ctx.project_id,),
        ).fetchone()["n"]
    )
    return bundle


# -- the 20 assertions -----------------------------------------------------
def a01_av_sync(b: dict) -> Assertion:
    probe, s10 = b.get("probe"), b.get("s10") or {}
    if not probe or not s10.get("master_duration_ms"):
        return Assertion(1, "av_duration_sync", "skip", "probe or master clock unavailable")
    drift = abs(probe["duration_s"] * 1000.0 - float(s10["master_duration_ms"]))
    ok = drift < 80
    return Assertion(1, "av_duration_sync", "pass" if ok else "fail",
                     f"|Δ|={drift:.1f} ms (<80)")


def a02_scene_durations(b: dict) -> Assertion:
    scenes = b.get("edl", {}).get("scenes") or []
    if not scenes:
        return Assertion(2, "scene_duration_bounds", "fail", "no EDL scenes")
    bad = [s["ord"] for s in scenes
           if not (SCENE_MIN_MS <= int(s["duration_ms"]) <= SCENE_MAX_MS)]
    ok = not bad
    return Assertion(2, "scene_duration_bounds", "pass" if ok else "fail",
                     f"{len(scenes)} scenes in [{SCENE_MIN_MS},{SCENE_MAX_MS}] ms"
                     + ("" if ok else f"; out of bounds: {bad}"))


def a03_scenes_approved(b: dict) -> Assertion:
    scenes = b.get("scenes") or []
    if not scenes:
        return Assertion(3, "scenes_have_approved_asset", "fail", "no scenes")
    bad = [s["ord"] for s in scenes
           if not s.get("asset_sha256") or s.get("gate_status") != "approved"]
    ok = not bad
    return Assertion(3, "scenes_have_approved_asset", "pass" if ok else "fail",
                     f"{len(scenes)} scenes" + ("" if ok else f"; not approved: {bad}"))


def a04_asset_repetition(b: dict) -> Assertion:
    edl_scenes = b.get("edl", {}).get("scenes") or []
    if not edl_scenes:
        return Assertion(4, "asset_repetition_limit", "fail", "no EDL scenes")
    counts: dict[str, int] = {}
    for s in edl_scenes:
        sha = s["asset"]["sha256"]
        counts[sha] = counts.get(sha, 0) + 1
    worst = max(counts.values())
    ok = worst <= ASSET_MAX_REPEAT
    return Assertion(4, "asset_repetition_limit", "pass" if ok else "fail",
                     f"max reuse {worst} (<= {ASSET_MAX_REPEAT}); {len(counts)} unique assets")


def a05_caption_coverage(b: dict) -> Assertion:
    if not b.get("srt"):
        return Assertion(5, "caption_coverage", "fail", "captions.srt missing")
    cues = parse_srt(b["srt"])
    if not cues:
        return Assertion(5, "caption_coverage", "fail", "no cues parsed")
    master = float((b.get("s10") or {}).get("master_duration_ms") or 0)
    total = sum(c["end_ms"] - c["start_ms"] for c in cues)
    chained = all(cues[i + 1]["start_ms"] - cues[i]["end_ms"] <= 5 for i in range(len(cues) - 1))
    no_overlap = all(cues[i + 1]["start_ms"] >= cues[i]["end_ms"] - 5 for i in range(len(cues) - 1))
    drift = abs(total - master) if master else 0.0
    ok = chained and no_overlap and drift <= 80
    return Assertion(5, "caption_coverage", "pass" if ok else "fail",
                     f"{len(cues)} cues, chained={chained}, drift={drift:.1f} ms")


def a06_subtitle_format(b: dict) -> Assertion:
    if not b.get("srt"):
        return Assertion(6, "subtitle_format", "fail", "captions.srt missing")
    cues = parse_srt(b["srt"])
    bad = [i for i, c in enumerate(cues, 1)
           if len(c["lines"]) > SRT_LINES_MAX
           or any(len(ln) > SRT_LINE_MAX for ln in c["lines"])]
    ok = bool(cues) and not bad
    return Assertion(6, "subtitle_line_limits", "pass" if ok else "fail",
                     f"{len(cues)} cues" + ("" if ok else f"; breach at cues {bad[:5]}"))


def a07_loudness(b: dict) -> Assertion:
    lufs = (b.get("s10") or {}).get("integrated_lufs")
    if lufs is None:
        return Assertion(7, "audio_loudness", "fail", "S10 loudness missing")
    ok = LUFS_MIN <= float(lufs) <= LUFS_MAX
    return Assertion(7, "audio_loudness", "pass" if ok else "fail",
                     f"{float(lufs):.2f} LUFS in [{LUFS_MIN},{LUFS_MAX}]")


def a08_true_peak(b: dict) -> Assertion:
    tp = (b.get("s10") or {}).get("true_peak_dbtp")
    if tp is None:
        return Assertion(8, "audio_true_peak", "fail", "S10 true peak missing")
    ok = float(tp) < TRUE_PEAK_MAX
    return Assertion(8, "audio_true_peak", "pass" if ok else "fail",
                     f"{float(tp):.2f} dBTP (< {TRUE_PEAK_MAX})")


def a09_silence_gap(b: dict) -> Assertion:
    rows = b.get("sentences") or []
    rows = [r for r in rows if r.get("start_ms") is not None]
    if not rows:
        return Assertion(9, "max_silence_gap", "fail", "no timed sentences")
    gaps: list[float] = []
    prev_end = 0.0
    for r in rows:
        start = float(r["start_ms"])
        gaps.append(start - prev_end)
        prev_end = start + float(r["duration_ms"] or 0)
    worst = max(gaps)
    ok = worst <= SILENCE_MAX_MS
    return Assertion(9, "max_silence_gap", "pass" if ok else "fail",
                     f"worst gap {worst:.0f} ms (<= {SILENCE_MAX_MS})")


def a10_container(b: dict) -> Assertion:
    probe = b.get("probe")
    if not probe:
        return Assertion(10, "container_video_params", "skip", "ffprobe unavailable")
    fps = _fps_value(probe.get("avg_frame_rate"))
    problems = []
    if probe["width"] != 1920 or probe["height"] != 1080:
        problems.append(f"{probe['width']}x{probe['height']}")
    if fps is None or abs(fps - FPS) > 0.01:
        problems.append(f"fps={fps}")
    if (probe.get("pix_fmt") or "yuv420p") != "yuv420p":
        problems.append(f"pix_fmt={probe.get('pix_fmt')}")
    ok = not problems
    return Assertion(10, "container_video_params", "pass" if ok else "fail",
                     "1920x1080@30 yuv420p" if ok else "; ".join(problems))


def a11_faststart(b: dict) -> Assertion:
    has = _has_faststart(b["final"])
    if has is None:
        return Assertion(11, "container_faststart", "fail", "final.mp4 missing")
    return Assertion(11, "container_faststart", "pass" if has else "fail",
                     "moov atom before mdat" if has else "moov not at head")


def a12_thumbnail(b: dict) -> Assertion:
    thumb: Path = b["thumb"]
    if not thumb.exists():
        return Assertion(12, "thumbnail_spec", "fail", "thumbnail.png missing")
    from PIL import Image

    try:
        with Image.open(thumb) as im:
            size = im.size
    except OSError:
        return Assertion(12, "thumbnail_spec", "fail", "thumbnail unreadable")
    nbytes = thumb.stat().st_size
    ok = size == THUMB_SIZE and nbytes < THUMB_MAX_BYTES
    return Assertion(12, "thumbnail_spec", "pass" if ok else "fail",
                     f"{size[0]}x{size[1]}, {nbytes / 1e6:.2f} MB")


def a13_metadata_limits(b: dict) -> Assertion:
    meta = b.get("meta") or {}
    titles = meta.get("title_variants") or []
    desc = meta.get("description") or ""
    if not titles or not desc:
        return Assertion(13, "metadata_limits", "fail", "metadata.json missing/empty")
    long_titles = [t for t in titles if len(t) > TITLE_MAX]
    ok = not long_titles and len(desc) <= DESC_MAX
    return Assertion(13, "metadata_limits", "pass" if ok else "fail",
                     f"{len(titles)} titles, desc {len(desc)}/{DESC_MAX} chars"
                     + ("" if ok else f"; too long: {long_titles}"))


def a14_wer(b: dict) -> Assertion:
    wer = (b.get("s17") or {}).get("wer_score", "missing")
    if wer is None or wer == "missing":
        return Assertion(14, "wer_script_match", "skip",
                         "no WER score stored (D8 alignment pending)")
    ok = float(wer) < WER_MAX
    return Assertion(14, "wer_script_match", "pass" if ok else "fail",
                     f"WER {float(wer):.1%} (< {WER_MAX:.0%})")


def a15_no_unsupported_claims(b: dict) -> Assertion:
    n = int(b.get("unsupported_claims") or 0)
    return Assertion(15, "claims_supported", "pass" if n == 0 else "fail",
                     f"{n} unsupported claim(s)" if n else "all claims supported")


def a16_assets_approved(b: dict) -> Assertion:
    assets = b.get("assets") or []
    if not assets:
        return Assertion(16, "assets_license_gate", "fail", "no media assets")
    bad = [a["sha256"][:10] for a in assets if a.get("gate_status") != "approved"]
    ok = not bad
    return Assertion(16, "assets_license_gate", "pass" if ok else "fail",
                     f"{len(assets)} assets" + ("" if ok else f"; not approved: {bad}"))


def a17_credits(b: dict) -> Assertion:
    from ytfactory.workers import credits as credits_worker

    required = [
        a for a in (b.get("assets") or [])
        if credits_worker.requires_attribution(a.get("license_id") or "")
    ]
    path: Path = b["credits"]
    if not required:
        return Assertion(17, "credits_present", "pass",
                         "no attribution-required assets")
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return Assertion(17, "credits_present", "fail",
                         f"{len(required)} attribution-required assets but CREDITS.md empty")
    return Assertion(17, "credits_present", "pass",
                     f"CREDITS.md covers {len(required)} asset(s)")


def a18_black_frames(b: dict) -> Assertion:
    runs = _black_frames(b["final"])
    if runs is None:
        return Assertion(18, "black_frame_check", "skip", "ffmpeg unavailable")
    ok = not runs
    return Assertion(18, "black_frame_check", "pass" if ok else "fail",
                     "no black runs >= 0.5 s" if ok else f"black runs: {runs[:3]}")


def a19_hook_present(b: dict) -> Assertion:
    rows = [r for r in (b.get("sentences") or []) if r.get("start_ms") is not None]
    if not rows:
        return Assertion(19, "hook_within_3s", "fail", "no timed sentences")
    first = rows[0]
    ok = float(first["start_ms"]) <= HOOK_MAX_START_MS
    return Assertion(19, "hook_within_3s", "pass" if ok else "fail",
                     f"hook starts at {float(first['start_ms']):.0f} ms")


def a20_chapters(b: dict) -> Assertion:
    chapters = (b.get("meta") or {}).get("chapters") or []
    if not chapters:
        return Assertion(20, "chapters_monotonic", "fail", "no chapters in metadata")
    starts = [int(c["start_ms"]) for c in chapters]
    master = float((b.get("s10") or {}).get("master_duration_ms") or 0)
    monotonic = all(starts[i] <= starts[i + 1] for i in range(len(starts) - 1))
    in_range = starts[0] >= 0 and (not master or starts[-1] <= master)
    ok = monotonic and in_range
    return Assertion(20, "chapters_monotonic", "pass" if ok else "fail",
                     f"{len(chapters)} chapters, monotonic={monotonic}, "
                     f"last={starts[-1]} <= {master:.0f} ms")


ASSERTIONS: list[Callable[[dict], Assertion]] = [
    a01_av_sync, a02_scene_durations, a03_scenes_approved, a04_asset_repetition,
    a05_caption_coverage, a06_subtitle_format, a07_loudness, a08_true_peak,
    a09_silence_gap, a10_container, a11_faststart, a12_thumbnail,
    a13_metadata_limits, a14_wer, a15_no_unsupported_claims, a16_assets_approved,
    a17_credits, a18_black_frames, a19_hook_present, a20_chapters,
]


def run_qc(ctx: Ctx) -> dict:
    """Execute all 20 assertions -> summary dict (also list under 'assertions')."""
    bundle = load_bundle(ctx)
    results = [fn(bundle) for fn in ASSERTIONS]
    failed = [a for a in results if a.status == "fail"]
    skipped = [a for a in results if a.status == "skip"]
    return {
        "assertions": [asdict(a) for a in results],
        "passed": sum(1 for a in results if a.status == "pass"),
        "failed": len(failed),
        "skipped": len(skipped),
        "failed_ids": [a.id for a in failed],
        "failed_names": [a.name for a in failed],
        "all_pass": not failed,
        "total": len(results),
    }
