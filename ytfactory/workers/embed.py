"""D3 Evidence Indexer — bge-small ONNX embeddings (lazy, ~400 MB when loaded).

Returns None when fastembed is unavailable so the pipeline can degrade to
lexical-only matching instead of failing (embedding_blob stays NULL).

Model loading is gated on local cache presence: without the ONNX files on
disk the function returns None unless YT_EMBED=1 opts into a download, so
restricted networks never stall in fastembed's retry loop.
"""
from __future__ import annotations

import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Optional

MODEL = "BAAI/bge-small-en-v1.5"
CHUNK_CHARS = 800
CHUNK_OVERLAP = 120


def model_cache_dir() -> Path:
    env = os.environ.get("YT_FASTEMBED_CACHE")
    if env:
        return Path(env)
    return Path(tempfile.gettempdir()) / "fastembed_cache"


def model_cached(model_name: str, cache_dir: Path | None = None) -> bool:
    """True when fastembed's ONNX files are already on disk (offline load OK)."""
    cache_dir = Path(cache_dir or model_cache_dir())
    if not cache_dir.is_dir():
        return False
    slug = model_name.replace("/", "--")
    for base in (cache_dir / f"models--{slug}", *cache_dir.glob(f"*{slug}*")):
        if base.is_dir() and any(base.rglob("*.onnx")):
            return True
    org, repo = model_name.split("/", 1)
    flat = cache_dir / org / repo
    return flat.is_dir() and any(flat.rglob("*.onnx"))


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[tuple[str, int, int]]:
    """-> [(chunk, start_offset, end_offset)] with character spans (FR evidence spans)."""
    chunks: list[tuple[str, int, int]] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + size)
        if end < n:
            # prefer breaking on sentence/word boundary
            cut = max(text.rfind(". ", start, end), text.rfind(" ", start, end))
            if cut > start + size // 2:
                end = cut + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append((chunk, start, end))
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


@lru_cache(maxsize=1)
def _model():
    cache = model_cache_dir()
    if not model_cached(MODEL, cache) and os.environ.get("YT_EMBED") != "1":
        raise RuntimeError(
            f"model {MODEL} not cached in {cache} (set YT_EMBED=1 to download)"
        )
    from fastembed import TextEmbedding  # lazy: ~400 MB RAM only when used

    return TextEmbedding(model_name=MODEL, cache_dir=str(cache))


def embed_texts(texts: list[str]) -> Optional[list[bytes]]:
    """-> list of raw float32 vectors, or None if fastembed is not installed."""
    try:
        model = _model()
    except Exception as exc:
        print(f"[embed] fastembed unavailable ({exc}); continuing without vectors")
        return None
    import numpy as np

    return [np.asarray(v, dtype=np.float32).tobytes() for v in model.embed(texts)]
