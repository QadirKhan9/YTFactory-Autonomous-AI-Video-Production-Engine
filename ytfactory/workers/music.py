"""Phase 6.3 music bed worker (S14): keyless music sources, a self-made
ambient pad fallback, and FR-A-07 sidechain ducking (6-10 dB under narration).

Sources — every track must pass D6 `license_wall.classify` (FR-A-06
commercial_ok, FR-A-08 no NC/ND):
  wikimedia — Commons audio search (keyless)
  freesound — Freesound API (FREESOUND_API_KEY, key-gated)
  youtube   — YouTube Audio Library catalog (keyless; static mirror of the
              official library, tracks on Google Drive; commercial use in
              YouTube videos is what the library is for)
  generated — self-made ambient pad (internal license, always available;
              disable with config music_fallback=false)

Ducking: ffmpeg `sidechaincompress` with the narration as sidechain. The
resulting depth is *measured* (RMS of the ducked bed inside speech windows vs.
silence gaps) and the threshold auto-tunes until it lands in 6-10 dB.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import numpy as np

from ytfactory.workers import license_wall

USER_AGENT = license_wall.USER_AGENT

GENERATED_LICENSE_ID = "internal_music_bed"
GENERATED_SOURCE = "generated"
DEFAULT_QUERY = "ambient calm instrumental"

# YouTube Audio Library (unofficial static mirror of the official catalog:
# 5k+ free tracks, drive-hosted mp3s, licensed for use *in* YouTube videos —
# including monetized ones — under the Standard YouTube Audio Library License).
YTAL_CATALOG_URL = os.environ.get(
    "YT_YTAL_CATALOG",
    "https://thibaultjanbeyer.github.io/YouTube-Free-Audio-Library-API/api.json",
)
YTAL_LICENSE_NAME = "YouTube Audio Library"
YTAL_SOURCE = "youtube"
YTAL_TIMEOUT = 25.0
_YTAL_CATALOG: list[dict] | None = None

DUCK_MIN_DB = 6.0
DUCK_MAX_DB = 10.0
MUSIC_GAIN_DB = -18.0          # bed level under narration (pre-duck)
MAX_DOWNLOAD_BYTES = 40 * 1024 * 1024
SOURCE_TIMEOUT = 12.0
DOWNLOAD_TIMEOUT = 60.0
DUCK_TUNING_ROUNDS = 8


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------
def _meta_value(extmeta: dict, key: str) -> str:
    entry = extmeta.get(key) or {}
    return str(entry.get("value") or "").strip()


def search_wikimedia_audio(
    query: str, limit: int = 5, client: httpx.Client | None = None
) -> list[dict]:
    """Keyless Commons audio search -> commercial-safe tracks only."""
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"filetype:audio {query}",
        "gsrnamespace": 6,
        "gsrlimit": 10,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|ObjectName",
    }
    own = client is None
    client = client or httpx.Client(timeout=SOURCE_TIMEOUT, headers={"User-Agent": USER_AGENT})
    try:
        resp = client.get("https://commons.wikimedia.org/w/api.php", params=params)
        resp.raise_for_status()
        pages = (resp.json().get("query") or {}).get("pages") or {}
    finally:
        if own:
            client.close()

    out: list[dict] = []
    for page in pages.values():
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        url = info.get("url")
        if not url:
            continue
        meta = info.get("extmetadata") or {}
        lic_name = f"{page.get('title', '')} {_meta_value(meta, 'LicenseShortName')}"
        lic_url = _meta_value(meta, "LicenseUrl")
        lic_id, ok, canon = license_wall.classify(lic_name, lic_url)
        if not ok:
            continue
        out.append(
            {
                "name": page.get("title") or url.rsplit("/", 1)[-1],
                "url": url,
                "license_id": lic_id,
                "license_url": canon or lic_url,
                "credit": _meta_value(meta, "Artist") or "Wikimedia Commons",
                "source": "wikimedia",
            }
        )
        if len(out) >= limit:
            break
    return out


def search_freesound(
    query: str, limit: int = 5, api_key: str | None = None,
    client: httpx.Client | None = None,
) -> list[dict]:
    """Freesound search (key-gated). Empty list without FREESOUND_API_KEY."""
    key = api_key or os.environ.get("FREESOUND_API_KEY") or ""
    if not key:
        return []
    params = {
        "query": query,
        "filter": "duration:[3 TO 600]",
        "fields": "id,name,license,duration,previews",
        "page_size": limit,
        "token": key,
    }
    own = client is None
    client = client or httpx.Client(timeout=SOURCE_TIMEOUT, headers={"User-Agent": USER_AGENT})
    try:
        resp = client.get("https://freesound.org/apiv2/search/text/", params=params)
        resp.raise_for_status()
        results = resp.json().get("results") or []
    finally:
        if own:
            client.close()

    out: list[dict] = []
    for item in results:
        license_name = str(item.get("license") or "")
        lic_id, ok, canon = license_wall.classify(
            f"{item.get('name', '')} {license_name}"
        )
        if not ok:
            continue
        previews = item.get("previews") or {}
        url = previews.get("preview-hq-mp3") or previews.get("preview-hq-ogg")
        if not url:
            continue
        out.append(
            {
                "name": str(item.get("name") or "freesound track"),
                "url": url,
                "license_id": lic_id,
                "license_url": canon,
                "credit": f"Freesound user audio ({license_name})",
                "source": "freesound",
            }
        )
        if len(out) >= limit:
            break
    return out


def _qtok(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def fetch_youtube_catalog(client: httpx.Client | None = None) -> list[dict]:
    """Catalog -> [{id, name, stem}] (1.3 MB JSON, cached for the process)."""
    global _YTAL_CATALOG
    if _YTAL_CATALOG is not None:
        return _YTAL_CATALOG
    own = client is None
    client = client or httpx.Client(
        timeout=YTAL_TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )
    try:
        resp = client.get(YTAL_CATALOG_URL)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if own:
            client.close()
    tracks: list[dict] = []
    for item in data.get("all") or []:
        if not isinstance(item, dict):
            continue
        fid = str(item.get("id") or "")
        name = str(item.get("name") or "")
        if not fid or not name.lower().endswith(".mp3"):
            continue
        stem = re.sub(r"\.mp3$", "", name, flags=re.IGNORECASE)
        tracks.append({"id": fid, "name": name, "stem": re.sub(r"[_\-]+", " ", stem).lower()})
    if not tracks:
        raise RuntimeError("youtube audio library catalog empty or unparsable")
    _YTAL_CATALOG = tracks
    return tracks


def search_youtube_audio_library(
    query: str, limit: int = 5, client: httpx.Client | None = None
) -> list[dict]:
    """YouTube Audio Library search (keyless) -> commercial-safe tracks.

    Matches query tokens against track filenames; when nothing matches the
    query still returns a deterministic slice of the library (a background
    music bed only needs to be license-clean, not semantically exact)."""
    catalog = fetch_youtube_catalog(client=client)
    lic_id, ok, canon = license_wall.classify(YTAL_LICENSE_NAME)
    if not ok:  # pragma: no cover - registry keeps the library commercial-ok
        return []
    want = _qtok(query)
    scored = []
    for track in catalog:
        score = len(want & set(track["stem"].split()))
        scored.append((score, track["name"].lower(), track))
    if not any(score > 0 for score, _, _ in scored):
        offset = int(hashlib.sha256((query or "").encode()).hexdigest()[:8], 16)
        picked = [scored[(offset + i) % len(scored)] for i in range(min(limit, len(scored)))]
    else:
        picked = [s for s in scored if s[0] > 0]
        picked.sort(key=lambda s: (-s[0], s[1]))
        picked = picked[:limit]
    out: list[dict] = []
    for _, _, track in picked:
        out.append(
            {
                "name": track["name"],
                "url": f"https://docs.google.com/uc?export=open&id={track['id']}",
                "license_id": lic_id,
                "license_url": canon,
                "credit": f"YouTube Audio Library ({track['stem']})",
                "source": YTAL_SOURCE,
            }
        )
    return out


def download(url: str, dest_dir: Path, client: httpx.Client | None = None) -> Path:
    """Fetch a music file into dest_dir/<sha16><ext>. Capped at 40 MB."""
    own = client is None
    client = client or httpx.Client(
        timeout=DOWNLOAD_TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )
    try:
        resp = client.get(url)
        resp.raise_for_status()
        blob = resp.content
    finally:
        if own:
            client.close()
    if len(blob) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"music file too large: {len(blob)} bytes")
    ext = ".wav"
    ctype = resp.headers.get("content-type", "").lower()
    for candidate, names in (
        (".mp3", ("mpeg", "mp3")),
        (".ogg", ("ogg",)),
        (".flac", ("flac",)),
        (".wav", ("wav", "x-wav", "wave")),
    ):
        if any(n in ctype for n in names):
            ext = candidate
            break
    else:
        suffix = Path(url.split("?", 1)[0]).suffix.lower()
        if suffix in {".mp3", ".ogg", ".flac", ".wav"}:
            ext = suffix
    sha = hashlib.sha256(blob).hexdigest()
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{sha[:16]}{ext}"
    if not path.exists():
        path.write_bytes(blob)
    return path


# --------------------------------------------------------------------------
# self-made ambient pad (deterministic, license-clean)
# --------------------------------------------------------------------------
_PAD_FREQS = (110.0, 164.81, 220.0, 261.63, 329.63, 440.0)   # A minor add9 voicing
_PAD_AMPS = (0.50, 0.34, 0.30, 0.22, 0.16, 0.10)


def generate_pad(duration_s: float, sample_rate: int = 24000) -> np.ndarray:
    """Quiet evolving drone — self-made, always available as last resort."""
    n = max(int(duration_s * sample_rate), sample_rate)
    t = np.arange(n, dtype=np.float64) / sample_rate
    out = np.zeros(n, dtype=np.float64)
    for i, (freq, amp) in enumerate(zip(_PAD_FREQS, _PAD_AMPS)):
        lfo = 0.8 + 0.2 * np.sin(2 * np.pi * (0.05 + 0.013 * i) * t + i * 1.7)
        out += amp * lfo * np.sin(2 * np.pi * freq * t + i * 0.9)
    out /= max(np.max(np.abs(out)), 1e-9)
    out *= 0.5
    fade = int(1.5 * sample_rate)
    if fade * 2 < n:
        ramp = np.linspace(0.0, 1.0, fade)
        out[:fade] *= ramp
        out[-fade:] *= ramp[::-1]
    return out.astype(np.float32)


def write_pad(path: Path, duration_s: float, sample_rate: int = 24000) -> Path:
    from ytfactory.workers import audio as wav

    path.parent.mkdir(parents=True, exist_ok=True)
    wav.write_wav(str(path), generate_pad(duration_s, sample_rate), sample_rate)
    return path


# --------------------------------------------------------------------------
# sidechain ducking (FR-A-07: 6-10 dB under narration)
# --------------------------------------------------------------------------
def _rms_db(frames: np.ndarray) -> float:
    if frames.size == 0:
        return -120.0
    return 20.0 * np.log10(max(float(np.sqrt(np.mean(frames.astype(np.float64) ** 2))), 1e-9))


def estimate_duck_depth_db(
    narration: np.ndarray, ducked: np.ndarray, sample_rate: int,
    frame_ms: float = 20.0, release_ms: float = 250.0, attack_ms: float = 15.0,
) -> float:
    """Duck depth = gap RMS minus speech RMS of the *ducked bed* (dB > 0).

    Gap frames are only trusted outside [speech - attack, speech + release]
    (the compressor has fully recovered there); frames *before* speech are
    valid baselines, so short head gaps still count."""
    n = min(len(narration), len(ducked))
    if n < sample_rate // 2:
        return 0.0
    narr = np.asarray(narration[:n], dtype=np.float32)
    bed = np.asarray(ducked[:n], dtype=np.float32)
    frame = max(int(sample_rate * frame_ms / 1000.0), 1)
    n_frames = n // frame
    narr_rms = np.empty(n_frames, dtype=np.float64)
    bed_rms = np.empty(n_frames, dtype=np.float64)
    for f in range(n_frames):
        seg = slice(f * frame, (f + 1) * frame)
        narr_rms[f] = float(np.sqrt(np.mean(np.square(narr[seg], dtype=np.float64))))
        bed_rms[f] = float(np.sqrt(np.mean(np.square(bed[seg], dtype=np.float64))))
    peak = float(np.max(narr_rms)) if n_frames else 0.0
    if peak <= 1e-6:
        return 0.0
    speech = narr_rms > 0.15 * peak
    quiet = narr_rms < 0.02 * peak
    guard_in = int(attack_ms / frame_ms) + 1
    guard_out = int(release_ms / frame_ms) + 1
    clear = np.ones(n_frames, dtype=bool)
    for i in np.flatnonzero(speech):
        clear[max(i - guard_in, 0): min(i + guard_out + 1, n_frames)] = False
    gaps = quiet & clear
    if int(speech.sum()) < 3 or int(gaps.sum()) < 3:
        return 0.0
    return max(_rms_db(bed_rms[gaps]) - _rms_db(bed_rms[speech]), 0.0)


def _run_ffmpeg(args: list[str]) -> str:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True, timeout=300,
    )
    if proc.returncode != 0:
        tail = proc.stderr.decode(errors="replace")[-300:]
        raise RuntimeError(f"ffmpeg failed ({args[0]}): {tail}")
    return ""


def _threshold_mode(work_dir: Path, sample_rate: int) -> str:
    """Probe: does this ffmpeg build take threshold in dB (-50..0) or linear?"""
    probe = work_dir / "_probe.wav"
    try:
        _run_ffmpeg(
            ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.2",
             "-af", "acompressor=threshold=-5:ratio=2",
             "-ar", str(sample_rate), "-y", str(probe)]
        )
        return "db"
    except RuntimeError:
        return "linear"
    finally:
        probe.unlink(missing_ok=True)


def duck_bed(
    narration_path: str | Path,
    music_path: str | Path,
    work_dir: Path,
    *,
    gain_db: float = MUSIC_GAIN_DB,
    sample_rate: int | None = None,
) -> tuple[Path, float]:
    """-> (ducked_bed_path, measured_depth_db). Auto-tunes into 6-10 dB."""
    from ytfactory.workers import audio as wav

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH — S14 needs ffmpeg")
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    narr, sr = wav.read_wav(str(narration_path))
    sr = sample_rate or sr
    duration_s = len(narr) / float(sr) + 0.5

    trimmed = work_dir / "music_trimmed.wav"
    _run_ffmpeg(
        ["-y", "-stream_loop", "-1", "-i", str(music_path),
         "-t", f"{duration_s:.3f}", "-ar", str(sr), "-ac", "1", str(trimmed)]
    )

    mode = _threshold_mode(work_dir, sr)
    ducked_path = work_dir / "music_ducked.wav"
    best_path = work_dir / "music_ducked_best.wav"
    best_depth, best_delta = 0.0, float("inf")
    thr = -20.0 if mode == "db" else 0.125
    for _ in range(DUCK_TUNING_ROUNDS):
        _run_ffmpeg(
            ["-y", "-i", str(trimmed), "-i", str(narration_path),
             "-filter_complex",
             f"[0:a]volume={gain_db}dB,aresample={sr},aformat=channel_layouts=mono[main];"
             f"[1:a]aresample={sr},aformat=channel_layouts=mono[sc];"
             f"[main][sc]sidechaincompress=threshold={thr:.4f}:ratio=8:attack=10:release=250[out]",
             "-map", "[out]", "-ar", str(sr), "-ac", "1", str(ducked_path)]
        )
        bed, _ = wav.read_wav(str(ducked_path))
        depth = estimate_duck_depth_db(narr, bed, sr)
        delta = abs(depth - (DUCK_MIN_DB + DUCK_MAX_DB) / 2.0)
        if delta < best_delta:
            shutil.copyfile(ducked_path, best_path)
            best_depth, best_delta = depth, delta
        if DUCK_MIN_DB - 0.75 <= depth <= DUCK_MAX_DB + 0.75:
            break
        if depth < DUCK_MIN_DB:  # deepen: threshold closer to full level
            thr = min(thr + abs(thr) * 0.6, -1.0) if mode == "db" else max(thr * 0.55, 0.001)
        else:                    # shallower
            thr = max(thr - abs(thr) * 0.6, -45.0) if mode == "db" else min(thr * 1.6, 0.9)
    if best_path.exists():
        shutil.copyfile(best_path, ducked_path)
    return ducked_path, best_depth


def build_mix(
    narration_path: str | Path, ducked_path: str | Path, out_path: Path
) -> dict:
    """narration + ducked bed -> EBU-normalized master_mix.wav (S16 uses it)."""
    from ytfactory.workers import audio as wav

    narr, sr = wav.read_wav(str(narration_path))
    bed, bed_sr = wav.read_wav(str(ducked_path))
    if bed_sr != sr:
        bed = wav.resample(bed, bed_sr, sr)
    if len(bed) < len(narr):
        bed = np.pad(bed, (0, len(narr) - len(bed)))
    else:
        bed = bed[: len(narr)]
    mix = narr.astype(np.float32) + bed.astype(np.float32)
    mix, measurements = wav.normalize_ebu(mix, sr)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wav.write_wav(str(out_path), mix, sr)
    return {
        "integrated_lufs": measurements.get("integrated_lufs"),
        "true_peak_dbtp": measurements.get("true_peak_dbtp"),
        "duration_ms": round(wav.duration_ms(mix, sr), 3),
    }


# --------------------------------------------------------------------------
# source selection
# --------------------------------------------------------------------------
def fetch_music(
    cfg: dict,
    *,
    work_dir: Path,
    duration_s: float,
    query: str | None = None,
    note=None,
) -> dict | None:
    """Resolve a licensed track per config music_source (default 'auto'):
    wikimedia -> freesound -> youtube -> generated pad (music_fallback, on)."""
    say = note or (lambda msg: None)
    source = str(cfg.get("music_source", "auto")).lower()
    if source in {"off", "none", "disabled"}:
        return None
    auto = ["wikimedia", "freesound", "youtube", "generated"]
    order = {
        "auto": auto,
        "wikimedia": ["wikimedia"],
        "freesound": ["freesound"],
        "youtube": ["youtube"],
        "generated": ["generated"],
    }.get(source, auto)
    fallback_ok = bool(cfg.get("music_fallback", True))
    music_query = query or str(cfg.get("music_query") or DEFAULT_QUERY)
    music_dir = Path(work_dir) / "music"

    for src in order:
        if src == "generated" and not fallback_ok:
            continue
        try:
            if src == "wikimedia":
                hits = search_wikimedia_audio(music_query)
            elif src == "freesound":
                hits = search_freesound(music_query)
            elif src == "youtube":
                hits = search_youtube_audio_library(music_query)
            else:
                pad_path = write_pad(music_dir / "generated_pad.wav", duration_s + 2.0)
                hits = [
                    {
                        "name": "self-made ambient pad",
                        "url": "",
                        "license_id": GENERATED_LICENSE_ID,
                        "license_url": "",
                        "credit": "YTFactory (generated)",
                        "source": GENERATED_SOURCE,
                        "local": pad_path,
                    }
                ]
            for hit in hits:
                path = hit.pop("local", None) or download(hit["url"], music_dir)
                hit["path"] = str(path)
                hit["file_sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
                say(f"S14 music source '{src}': {hit['name']} [{hit['license_id']}]")
                return hit
            say(f"S14 music source '{src}' returned no usable (commercial-safe) tracks")
        except Exception as exc:  # noqa: BLE001 - source death falls through
            say(f"S14 music source '{src}' failed: {type(exc).__name__}: {exc}")
    return None
