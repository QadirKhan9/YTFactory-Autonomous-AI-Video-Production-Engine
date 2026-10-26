"""Local Kokoro backend (`kokoro-onnx`, CPU) — fallback rung 1 of FR-A-05.

Model files (one-time local download, $0):
  models/kokoro-v1.0.onnx   (~310 MB, onnx-community/Kokoro-82M-v1.0-ONNX)
  models/voices-v1.0.bin    (~80 MB, same repo)
Overrides: config keys `kokoro_model`/`kokoro_voices` or env
`KOKORO_MODEL_PATH`/`KOKORO_VOICES_PATH`.
"""
from __future__ import annotations

import os
from pathlib import Path

from ytfactory.backends.base import (
    DEFAULT_SPEED,
    DEFAULT_VOICE,
    BackendError,
    flatten_paths,
    missing_sentences,
    model_slot,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_MODEL = REPO_ROOT / "models" / "kokoro-v1.0.onnx"
DEFAULT_VOICES = REPO_ROOT / "models" / "voices-v1.0.bin"
# kokoro-onnx's own release artifacts (layout-compatible with Kokoro.create()).
RELEASE_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
RELEASE_FILES = {
    "kokoro-v1.0.onnx": f"{RELEASE_BASE}/kokoro-v1.0.onnx",
    "voices-v1.0.bin": f"{RELEASE_BASE}/voices-v1.0.bin",
}


def resolve_paths(cfg: dict | None = None) -> tuple[Path, Path]:
    cfg = cfg or {}
    model = Path(
        cfg.get("kokoro_model")
        or os.environ.get("KOKORO_MODEL_PATH")
        or DEFAULT_MODEL
    )
    voices = Path(
        cfg.get("kokoro_voices")
        or os.environ.get("KOKORO_VOICES_PATH")
        or DEFAULT_VOICES
    )
    return model, voices


def _download(url: str, target: Path) -> None:
    """Streaming download with progress (httpx; one-time ~310 MB / ~80 MB)."""
    import httpx

    tmp = target.with_suffix(target.suffix + ".part")
    done = 0
    with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length") or 0)
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_bytes(1 << 20):
                fh.write(chunk)
                done += len(chunk)
                if total and done // (50 << 20) != (done - len(chunk)) // (50 << 20):
                    print(f"[kokoro] {target.name}: {done // (1 << 20)} / {total // (1 << 20)} MB")
    tmp.replace(target)


def ensure_models() -> tuple[Path, Path]:
    """Download model+voices once (~390 MB). Lazy."""
    model, voices = resolve_paths()
    for target in (model, voices):
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            _download(RELEASE_FILES[target.name], target)
    return model, voices


class KokoroBackend:
    name = "local_kokoro"

    def __init__(self, cfg: dict | None = None):
        self.cfg = dict(cfg or {})
        self.model_path, self.voices_path = resolve_paths(self.cfg)
        self.voice = self.cfg.get("tts_voice", DEFAULT_VOICE)
        self.speed = float(self.cfg.get("tts_speed", DEFAULT_SPEED))

    def available(self) -> bool:
        try:
            import kokoro_onnx  # noqa: F401
        except ImportError:
            return False
        if self.model_path.exists() and self.voices_path.exists():
            return True
        # If files are the standard release files, ensure_models() will auto-download them on demand
        if self.model_path.name in RELEASE_FILES and self.voices_path.name in RELEASE_FILES:
            return True
        return False

    def synthesize(self, scenes: list[dict], out_dir: str) -> list[str]:
        from ytfactory.workers.audio import write_wav

        Path(out_dir).mkdir(parents=True, exist_ok=True)
        todo = missing_sentences(scenes, out_dir)
        if todo:
            model, voices = ensure_models()
            with model_slot:  # NFR-M-02
                from kokoro_onnx import Kokoro

                kokoro = Kokoro(str(model), str(voices))
                for sent in todo:
                    try:
                        samples, sr = kokoro.create(
                            sent["text"],
                            voice=sent.get("voice", self.voice),
                            speed=self.speed,
                            lang=sent.get("lang", "en-us"),
                        )
                    except Exception as exc:
                        raise BackendError(f"kokoro failed on sentence {sent['ord']}: {exc}")
                    target = Path(out_dir) / _name_for(sent, self)
                    write_wav(target, samples, int(sr))
        return flatten_paths(out_dir, scenes)


def _name_for(sent: dict, backend: KokoroBackend) -> str:
    from ytfactory.backends.base import sentence_cache_name

    return sentence_cache_name(
        sent["ord"], sent["text"], sent.get("voice", backend.voice)
    )
