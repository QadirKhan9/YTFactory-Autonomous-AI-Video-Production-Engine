"""TTS backend protocol (FR-A-05): identical `synthesize(scenes, out_dir)`
across Kaggle batch, local Kokoro, and Piper — plus the shared hash cache.

NFR-M-02: `model_slot` guarantees at most one heavy ML model in RAM at a time.
"""
from __future__ import annotations

import hashlib
import threading
from typing import Protocol, runtime_checkable

# Global mutual-exclusion semaphore for heavy model load/synthesis (NFR-M-02).
model_slot = threading.Semaphore(1)

DEFAULT_VOICE = "af_heart"       # Kokoro voice (Apache 2.0)
DEFAULT_SPEED = 1.0


class BackendError(RuntimeError):
    """A TTS backend failed; caller may fall through the selection chain."""


@runtime_checkable
class TTSBackend(Protocol):
    name: str

    def available(self) -> bool:
        """Cheap probe: no downloads, no model loads."""
        ...

    def synthesize(self, scenes: list[dict], out_dir: str) -> list[str]:
        """Synthesize every sentence under `scenes` into `out_dir`.

        scenes: [{"ord": int, "sentences": [{"ord": int, "text": str, "voice": str}]}]
        Returns the WAV paths for ALL sentences (cached hits included),
        flattened in scene/sentence order.
        """
        ...


def sentence_cache_name(sentence_ord: int, text: str, voice: str = DEFAULT_VOICE) -> str:
    """`sent_<ord>_<md5(text+voice)[:10]>.wav` — prd.md §10 mandates md5(text+voice)."""
    digest = hashlib.md5(f"{text}{voice}".encode("utf-8")).hexdigest()[:10]
    return f"sent_{sentence_ord}_{digest}.wav"


def iter_sentences(scenes: list[dict]):
    """Yield (scene_ord, sentence_dict) in order."""
    for scene in scenes:
        for sent in scene.get("sentences", []):
            yield scene.get("ord"), sent


def flatten_paths(out_dir: str, scenes: list[dict]) -> list[str]:
    from pathlib import Path

    base = Path(out_dir)
    return [
        str(base / sentence_cache_name(s["ord"], s["text"], s.get("voice", DEFAULT_VOICE)))
        for _, s in iter_sentences(scenes)
    ]


def missing_sentences(scenes: list[dict], out_dir: str) -> list[dict]:
    """Sentences whose cached WAV does not exist yet."""
    from pathlib import Path

    base = Path(out_dir)
    todo = []
    for _, sent in iter_sentences(scenes):
        path = base / sentence_cache_name(
            sent["ord"], sent["text"], sent.get("voice", DEFAULT_VOICE)
        )
        if not path.exists():
            todo.append(sent)
    return todo
