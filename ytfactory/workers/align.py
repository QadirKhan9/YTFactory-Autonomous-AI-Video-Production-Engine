"""D8 aligner (FR-Sub-03/04): word-level WER against the clean master track.

Order of execution (same policy as everything else — local when possible,
Kaggle batch primary for the heavy model):
  1. local `faster-whisper` (lazy, optional) — fallback;
  2. one Kaggle batch job via the HF bus (primary when karaoke enabled);
  3. no model available → wer_score = None (S17 records it; QC #14 then
     reports 'not run' rather than silently passing).

`compute_wer` is pure Python so QC logic is unit-testable with no models.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Optional

_WORD_RE = re.compile(r"[a-z0-9']+")


def normalize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def edit_distance(a: list[str], b: list[str]) -> int:
    """Levenshtein over token lists (two-row DP)."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ta in enumerate(a, start=1):
        cur = [i]
        for j, tb in enumerate(b, start=1):
            cur.append(
                min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ta != tb))
            )
        prev = cur
    return prev[-1]


def compute_wer(reference: str, hypothesis: str) -> float:
    """Word Error Rate = edits / ref_words (0.0 when reference is empty)."""
    ref = normalize(reference)
    hyp = normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)


def transcribe_local(wav_path: str, model_size: str = "small") -> Optional[str]:
    """Local faster-whisper transcript, or None when unavailable (lazy)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    try:
        from ytfactory.backends.base import model_slot

        with model_slot:  # NFR-M-02
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            segments, _info = model.transcribe(str(wav_path), beam_size=1)
            return " ".join(seg.text.strip() for seg in segments)
    except Exception:
        return None


def submit_kaggle_batch(
    wav_path: str,
    reference_text: str,
    cfg: dict,
    timeout_s: float = 900,
    interval_s: float = 15,
) -> Optional[float]:
    """One D8 batch job on the HF bus; returns WER or None when unavailable."""
    from ytfactory.workers.hfbus import BusError, HfBus

    try:
        bus = HfBus.from_config(cfg)
        bus.ensure_repo()
        job_id = uuid.uuid4().hex[:12]
        wav_remote = f"inbox/{job_id}_master.wav"
        bus.upload_path(Path(wav_path), wav_remote)
        bus.upload_text(
            json.dumps(
                {"job_id": job_id, "kind": "align", "wav": wav_remote, "reference": reference_text}
            ),
            f"inbox/{job_id}.json",
        )
        result = bus.wait_for(f"outbox/{job_id}.result.json", timeout_s, interval_s)
        if result.get("error"):
            raise BusError(result["error"])
        return float(result["wer"])
    except Exception:
        return None


def run_alignment(
    wav_path: str,
    reference_text: str,
    cfg: dict,
    karaoke: bool = False,
) -> dict:
    """Full D8 policy for S17. Never raises — reports what actually ran."""
    if not karaoke:
        return {"wer_score": None, "method": "skipped", "transcript": None}

    transcript = transcribe_local(wav_path, cfg.get("whisper_model", "small"))
    if transcript is not None:
        return {
            "wer_score": round(compute_wer(reference_text, transcript), 4),
            "method": "local_faster_whisper",
            "transcript": transcript,
        }

    wer = submit_kaggle_batch(
        wav_path,
        reference_text,
        cfg,
        timeout_s=float(cfg.get("d8_poll_timeout_s", 900)),
        interval_s=float(cfg.get("d8_poll_interval_s", 15)),
    )
    if wer is not None:
        return {"wer_score": round(wer, 4), "method": "kaggle_batch", "transcript": None}

    return {"wer_score": None, "method": "unavailable", "transcript": None}
