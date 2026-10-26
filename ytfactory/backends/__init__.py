"""TTS backend selection (FR-A-05): Kaggle batch → local Kokoro → Piper.

Selection is availability-driven; S10 walks the chain on mid-run failure so
the DAG never stalls on TTS availability (rules.md §3.x).
"""
from __future__ import annotations

from typing import Any

from ytfactory.backends.base import (  # noqa: F401  (re-exported)
    BackendError,
    TTSBackend,
    iter_sentences,
    model_slot,
    sentence_cache_name,
)
from ytfactory.backends.tts_kaggle_batch import KaggleBatchBackend
from ytfactory.backends.tts_local_kokoro import KokoroBackend
from ytfactory.backends.tts_local_piper import PiperBackend

# Selection order: primary first, local floors last (FR-A-05).
BACKEND_ORDER = ("kaggle_batch", "local_kokoro", "local_piper")

_REGISTRY: dict[str, type] = {
    "kaggle_batch": KaggleBatchBackend,
    "local_kokoro": KokoroBackend,
    "local_piper": PiperBackend,
}


def build_backend(name: str, cfg: dict | None = None) -> Any:
    if name not in _REGISTRY:
        raise ValueError(f"unknown TTS backend {name!r}; choices: {list(_REGISTRY)}")
    return _REGISTRY[name](cfg)


def selection_chain(cfg: dict | None = None) -> list[Any]:
    """Available backends in FR-A-05 order; config `tts_backend` pins first."""
    cfg = cfg or {}
    preferred = cfg.get("tts_backend") or "auto"
    order = list(BACKEND_ORDER)
    if preferred != "auto":
        if preferred not in _REGISTRY:
            raise ValueError(f"unknown tts_backend {preferred!r}")
        order = [preferred] + [n for n in order if n != preferred]

    chain = []
    for name in order:
        backend = build_backend(name, cfg)
        if backend.available():
            chain.append(backend)
    return chain
