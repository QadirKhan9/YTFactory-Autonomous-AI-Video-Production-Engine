"""Master-clock audio worker (Phase 2): durations, resampling, sample-level
concat with silence padding, EBU R128 loudness normalization.

Invariant (architecture.md §3): the audio *is* the timeline — every later
phase reads `sentences.duration_ms` produced here.
"""
from __future__ import annotations

import hashlib
from typing import Optional

import numpy as np
import soundfile as sf

TARGET_SR = 24000          # master sample rate (Kokoro native)
TARGET_LUFS = -14.0        # EBU R128 integrated loudness
TRUE_PEAK_DBTP = -1.5      # True-peak ceiling
DEFAULT_GAP_MS = 200       # silence between sentences (config: sentence_gap_ms)


def duration_ms(samples: np.ndarray, sample_rate: int) -> float:
    """Exact sample-count duration: samples / sr * 1000 (design.md §5)."""
    return len(samples) / float(sample_rate) * 1000.0


def read_wav(path: str) -> tuple[np.ndarray, int]:
    """-> (float32 mono samples, sample_rate). Downmixes stereo to mono."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1) if data.shape[1] > 1 else data.reshape(-1)
    return mono, int(sr)


def write_wav(path: str, samples: np.ndarray, sample_rate: int) -> None:
    sf.write(str(path), np.asarray(samples, dtype=np.float32), sample_rate)


def resample(samples: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    """Polyphase resample (scipy); no-op when rates match."""
    if sr_from == sr_to:
        return np.asarray(samples, dtype=np.float32)
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(sr_from, sr_to)
    up, down = sr_to // g, sr_from // g
    return resample_poly(np.asarray(samples, dtype=np.float32), up, down).astype(
        np.float32
    )


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def concat_with_padding(
    segments: list[np.ndarray],
    sample_rate: int,
    gap_ms: float = DEFAULT_GAP_MS,
    head_ms: float = 0.0,
    tail_ms: float = 0.0,
) -> np.ndarray:
    """Sample-level concatenation with exact silence gaps (no frame rounding)."""
    if not segments:
        return np.zeros(0, dtype=np.float32)
    gap = int(round(sample_rate * gap_ms / 1000.0))
    head = int(round(sample_rate * head_ms / 1000.0))
    tail = int(round(sample_rate * tail_ms / 1000.0))
    parts: list[np.ndarray] = []
    if head:
        parts.append(np.zeros(head, dtype=np.float32))
    for i, seg in enumerate(segments):
        if i:
            parts.append(np.zeros(gap, dtype=np.float32))
        parts.append(np.asarray(seg, dtype=np.float32).reshape(-1))
    if tail:
        parts.append(np.zeros(tail, dtype=np.float32))
    return np.concatenate(parts)


def true_peak_dbtp(samples: np.ndarray, sample_rate: int) -> float:
    """True-peak estimate (ITU-R BS.1770-4): 4x oversampled peak, dBTP."""
    arr = np.asarray(samples, dtype=np.float64)
    peak = float(np.max(np.abs(arr))) if arr.size else 0.0
    if arr.size > 8:
        from scipy.signal import resample_poly

        up = np.abs(resample_poly(arr, 4, 1))
        peak = max(peak, float(np.max(up)))
    return 20.0 * np.log10(max(peak, 1e-12))


def limit_true_peak(
    samples: np.ndarray, sample_rate: int, ceiling_db: float = TRUE_PEAK_DBTP
) -> tuple[np.ndarray, float]:
    """Look-ahead peak limiter preserving loudness (speech crest ~21 dB).

    A plain linear gain to TARGET_LUFS can push true peak well past the
    ceiling; attenuating uniformly would destroy the loudness target. This
    slides a 2 ms gain envelope so only transients are reduced, retrying
    with a bigger sample-domain margin until measured TP <= ceiling_db.
    """
    from scipy.ndimage import maximum_filter1d

    arr = np.asarray(samples, dtype=np.float64)
    if arr.size == 0:
        return samples, -120.0
    win = max(3, int(round(sample_rate * 0.002))) | 1  # 2 ms lookahead
    pad = win // 2
    kernel = np.ones(win) / win
    out, tp_db = arr, true_peak_dbtp(arr, sample_rate)
    for margin in (1.0, 2.0, 4.0):
        ceil_lin = 10.0 ** ((ceiling_db - margin) / 20.0)
        env = maximum_filter1d(np.abs(arr), size=win, mode="nearest")
        gain = np.minimum(1.0, ceil_lin / np.maximum(env, 1e-12))
        gain = np.convolve(
            np.pad(gain, pad, mode="edge"), kernel, mode="same"
        )[pad : pad + arr.size]
        out = np.clip(arr * np.minimum(gain, 1.0), -ceil_lin, ceil_lin)
        tp_db = true_peak_dbtp(out, sample_rate)
        if tp_db <= ceiling_db + 0.01:
            return out, tp_db
    # safety net: uniform attenuation (should never trigger)
    out = out * (10.0 ** ((ceiling_db - tp_db) / 20.0))
    tp_db = true_peak_dbtp(out, sample_rate)
    return out, tp_db


def normalize_ebu(
    samples: np.ndarray, sample_rate: int
) -> tuple[np.ndarray, dict]:
    """EBU R128: gain to TARGET_LUFS integrated, then look-ahead limit
    true peak to TRUE_PEAK_DBTP without giving the loudness back.

    Returns (normalized_samples, measurements {integrated_lufs, true_peak_dbtp}).
    """
    import pyloudnorm as pyln

    arr = np.asarray(samples, dtype=np.float64)
    if arr.size == 0 or np.allclose(arr, 0.0):
        return samples, {"integrated_lufs": float("-inf"), "true_peak_dbtp": -120.0}

    meter = pyln.Meter(sample_rate)
    try:
        measured = meter.integrated_loudness(arr)
    except Exception:
        return samples, {"integrated_lufs": float("-inf"), "true_peak_dbtp": -120.0}
    if not np.isfinite(measured):
        return samples, {"integrated_lufs": float("-inf"), "true_peak_dbtp": -120.0}

    gain_db = TARGET_LUFS - measured
    for _ in range(3):
        arr = arr * (10.0 ** (gain_db / 20.0))
        arr, tp_db = limit_true_peak(arr, sample_rate)
        try:
            measured = meter.integrated_loudness(arr)
        except Exception:
            break
        if not np.isfinite(measured) or abs(TARGET_LUFS - measured) <= 0.3:
            break
        gain_db = TARGET_LUFS - measured  # limiter shaved a little loudness

    try:
        final_loudness = meter.integrated_loudness(arr)
    except Exception:
        final_loudness = float("-inf")
    return arr.astype(np.float32), {
        "integrated_lufs": round(float(final_loudness), 2),
        "true_peak_dbtp": round(float(tp_db), 2),
    }


def sentence_timeline(
    sentence_wavs: list[tuple[int, np.ndarray, int]],
    gap_ms: float = DEFAULT_GAP_MS,
    target_sr: int = TARGET_SR,
) -> tuple[np.ndarray, dict[int, dict], int]:
    """Build the master track + per-sentence clock.

    sentence_wavs: [(sentence_ord, samples, sr)] in script order.
    Returns (master_samples, {ord: {start_ms, duration_ms}}, target_sr).
    """
    if not sentence_wavs:
        return np.zeros(0, dtype=np.float32), {}, target_sr

    gap = int(round(target_sr * gap_ms / 1000.0))
    resampled = [
        (ord_, resample(samples, sr, target_sr))
        for ord_, samples, sr in sentence_wavs
    ]

    clock: dict[int, dict] = {}
    parts: list[np.ndarray] = []
    offset = 0
    for i, (ord_, seg) in enumerate(resampled):
        if i:
            parts.append(np.zeros(gap, dtype=np.float32))
            offset += gap
        parts.append(seg)
        clock[ord_] = {
            "start_ms": offset / target_sr * 1000.0,
            "duration_ms": len(seg) / target_sr * 1000.0,
        }
        offset += len(seg)
    return np.concatenate(parts), clock, target_sr


def master_duration_ms(samples: np.ndarray, sample_rate: int) -> float:
    return duration_ms(samples, sample_rate)


def measure_file(path: str) -> Optional[dict]:
    """Loudness measurements for an existing wav (for QC / debugging)."""
    import pyloudnorm as pyln

    data, sr = read_wav(path)
    if data.size == 0:
        return None
    meter = pyln.Meter(sr)
    try:
        loud = meter.integrated_loudness(data.astype(np.float64))
    except Exception:
        loud = float("-inf")
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    return {
        "integrated_lufs": round(float(loud), 2),
        "sample_peak_dbfs": round(20.0 * np.log10(max(peak, 1e-12)), 2),
        "duration_ms": duration_ms(data, sr),
        "sample_rate": sr,
    }
