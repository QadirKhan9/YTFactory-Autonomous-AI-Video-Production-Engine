"""Phase 6.2 reusable visual library (FR-V-09): cross-project, commercial-safe
assets with a CLIP embedding when fastembed's model is available, and an
offline `visual_v1` feature vector otherwise (dHash bits + color histogram +
4x4 spatial RGB grid, 136-d, L2-normalized — zero downloads, zero new deps).

Match strategy is lexical-overlap of query tokens vs. asset tags (topic tags
for ingested stock, style/color tags for generated abstracts); when vectors
exist, results are re-ranked by cosine similarity to the tag-centroid query
vector in visual-feature space (visual outliers demoted among tag-mates).
`stats` reports which encoder produced the vectors — CLIP when the ONNX model
is on disk, `visual_v1` otherwise.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIB_DIR = REPO_ROOT / "library"
SEED_TARGET = 10
SEED_SIZE = (1024, 576)
_CLIP_MODEL = "Qdrant/clip-ViT-B-32-vision"
FEATURE_MODEL = "visual_v1"
FEATURE_DIM = 136          # 64 dHash + 24 histogram + 48 grid RGB

_STOP = {
    "the", "a", "an", "of", "and", "or", "for", "to", "in", "on", "at",
    "with", "from", "by", "is", "are", "how", "why", "what", "when",
    "does", "do", "did", "this", "that", "it", "its", "into", "as",
}

# muted pairs: (base, accent) — abstract backgrounds must not fight narration
_PALETTE: list[tuple[tuple[int, int, int], tuple[int, int, int]]] = [
    ((32, 53, 71), (76, 151, 175)),
    ((46, 42, 66), (129, 113, 175)),
    ((40, 66, 55), (97, 168, 133)),
    ((74, 47, 47), (178, 111, 100)),
    ((35, 44, 66), (90, 124, 196)),
    ((60, 55, 35), (171, 154, 96)),
    ((52, 38, 62), (156, 106, 176)),
    ((30, 58, 62), (86, 166, 172)),
    ((66, 44, 30), (186, 134, 84)),
    ((44, 44, 48), (128, 130, 138)),
    ((30, 52, 44), (110, 170, 140)),
    ((58, 36, 48), (172, 106, 133)),
]


def _hue_name(rgb: tuple[int, int, int]) -> str:
    import colorsys

    h, _, v = colorsys.rgb_to_hsv(*[c / 255 for c in rgb])
    if v < 0.2:
        return "black"
    if v < 0.45 and h < 0.99:
        return "grey"
    for name, lo, hi in (
        ("red", 0.94, 1.0), ("orange", 0.04, 0.11), ("yellow", 0.11, 0.18),
        ("green", 0.25, 0.45), ("teal", 0.45, 0.55), ("blue", 0.55, 0.70),
        ("purple", 0.70, 0.80), ("pink", 0.80, 0.94),
    ):
        if lo <= h < hi or (name == "red" and h >= lo):
            return name
    return "blue"


def make_abstract(index: int, size: tuple[int, int] = SEED_SIZE) -> Image.Image:
    """Deterministic abstract artwork — self-made, commercial-safe."""
    rng = np.random.default_rng(4200 + index)
    base, accent = _PALETTE[index % len(_PALETTE)]
    sw, sh = size[0] // 4, size[1] // 4
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    g = ((xx / sw) * 0.6 + (yy / sh) * 0.4)
    g = g if index % 2 == 0 else 1.0 - g
    img = (
        np.array(base, dtype=np.float32)[None, None, :] * (1 - g[..., None])
        + np.array(accent, dtype=np.float32)[None, None, :] * g[..., None]
    )
    style = index % 3
    if style == 0:  # soft orbs
        for _ in range(4 + int(rng.integers(0, 4))):
            cx, cy = rng.uniform(0, sw), rng.uniform(0, sh)
            r = rng.uniform(0.12, 0.42) * min(sw, sh)
            d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
            mask = np.clip(1.0 - (d / max(r, 1.0)) ** 2, 0.0, 1.0) ** 1.5
            tone = np.array(accent, dtype=np.float32) if rng.random() < 0.5 else np.array(base, dtype=np.float32) * 1.4
            img = img * (1 - 0.55 * mask[..., None]) + tone[None, None, :] * (0.55 * mask[..., None])
    elif style == 1:  # bars
        for _ in range(3 + int(rng.integers(0, 4))):
            if rng.random() < 0.5:
                x0 = int(rng.uniform(0, sw - 4)); w0 = int(rng.uniform(3, sw * 0.2))
                img[:, x0:x0 + w0] = img[:, x0:x0 + w0] * 0.45 + np.array(accent, dtype=np.float32) * 0.55
            else:
                y0 = int(rng.uniform(0, sh - 4)); h0 = int(rng.uniform(2, sh * 0.25))
                img[y0:y0 + h0, :] = img[y0:y0 + h0, :] * 0.45 + np.array(accent, dtype=np.float32) * 0.55
    else:  # diagonal waves
        freq = rng.uniform(1.5, 3.5)
        phase = rng.uniform(0, 6.28)
        wave = 0.5 + 0.5 * np.sin(2 * np.pi * freq * (g + phase / 6.28))
        img = img * (1 - 0.35 * wave[..., None]) + np.array(accent, dtype=np.float32) * (0.35 * wave[..., None])
    img += rng.normal(0, 3.0, img.shape)
    small = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    small = small.filter(ImageFilter.GaussianBlur(radius=2))  # blur while small
    return small.resize(size, Image.BICUBIC)


def abstract_tags(index: int) -> list[str]:
    base, accent = _PALETTE[index % len(_PALETTE)]
    style = ("orbs", "bars", "waves")[index % 3]
    return sorted(
        {"abstract", "background", "texture", "gradient", style,
         _hue_name(base), _hue_name(accent), "calm", "muted"}
    )


# --------------------------------------------------------------------------
# features
# --------------------------------------------------------------------------
def dhash_hex(path: str | Path, size: int = 8) -> str:
    with Image.open(path) as im:
        small = im.convert("L").resize((size + 1, size), Image.BILINEAR)
        pixels = list(small.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            left = pixels[row * (size + 1) + col]
            right = pixels[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"


def hist_json(path: str | Path, bins: int = 8) -> list[float]:
    with Image.open(path) as im:
        arr = np.asarray(im.convert("RGB").resize((64, 64)), dtype=np.float32)
    hist = np.concatenate([np.histogram(arr[..., c], bins=bins, range=(0, 255))[0] for c in range(3)])
    total = float(hist.sum()) or 1.0
    return [round(float(v) / total, 4) for v in hist]


def embed_image(path: str | Path) -> bytes | None:
    """CLIP image vector when fastembed is installed AND the model is cached
    locally (or YT_CLIP=1 opts into a download); None otherwise."""
    from ytfactory.workers.embed import model_cache_dir, model_cached

    if not model_cached(_CLIP_MODEL) and os.environ.get("YT_CLIP") != "1":
        return None
    try:
        from fastembed import ImageEmbedding  # lazy: only when available
    except Exception:  # noqa: BLE001
        return None
    try:
        # pyrefly: ignore[unknown-name]
        global _CLIP
        model = globals().get("_CLIP")
        if model is None:
            model = ImageEmbedding(model_name=_CLIP_MODEL, cache_dir=str(model_cache_dir()))
            globals()["_CLIP"] = model
        vec = next(iter(model.embed([str(path)])))
        return np.asarray(vec, dtype=np.float32).tobytes()
    except Exception:  # noqa: BLE001 - embedding is optional metadata
        return None


def feature_vector(path: str | Path) -> np.ndarray:
    """Offline visual descriptor (visual_v1): dHash signs + color histogram +
    4x4 spatial mean-RGB. Each block is L2-normalized (equal say for shape
    and color), then the concat is scaled to unit norm. Deterministic, no
    model download."""
    with Image.open(path) as im:
        rgb = im.convert("RGB")
        small = rgb.resize((64, 64), Image.BILINEAR)
        arr = np.asarray(small, dtype=np.float32)
        gray = np.asarray(rgb.convert("L").resize((9, 8), Image.BILINEAR), dtype=np.float32)

    def unit(v: np.ndarray) -> np.ndarray:
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v

    dhash = unit(np.where(gray[:, 1:] > gray[:, :-1], 1.0, -1.0).reshape(-1).astype(np.float32))
    hist = np.concatenate(
        [np.histogram(arr[..., c], bins=8, range=(0, 255))[0] for c in range(3)]
    ).astype(np.float32)
    hist = unit(hist)
    grid = unit(arr.reshape(4, 16, 4, 16, 3).mean(axis=(1, 3)).reshape(-1) / 255.0)
    vec = (np.concatenate([dhash, hist, grid]) / np.sqrt(3.0)).astype(np.float32)
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec /= norm
    return vec


def embed_asset(path: str | Path) -> tuple[bytes, str] | None:
    """-> (vector bytes, model name). CLIP when its model is available,
    offline visual_v1 otherwise (never both — one label per row)."""
    vec = embed_image(path)
    if vec is not None:
        return vec, _CLIP_MODEL
    try:
        return feature_vector(path).tobytes(), FEATURE_MODEL
    except Exception:  # noqa: BLE001 - unreadable image, leave NULL
        return None


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOP and len(t) > 1}


# --------------------------------------------------------------------------
# ingest / query
# --------------------------------------------------------------------------
def ingest(
    conn,
    path: str | Path,
    *,
    license_id: str,
    source: str = "generated",
    tags: list[str] | tuple[str, ...] = (),
    license_url: str = "",
    credit: str = "",
    source_url: str = "",
    commercial_ok: int = 1,
) -> str | None:
    """Add one asset (files outside LIB_DIR are copied in). -> sha256 | None"""
    if not commercial_ok:
        raise ValueError("visual library holds commercial-safe assets only")
    path = Path(path)
    blob = path.read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    if conn.execute("SELECT 1 FROM vislib_assets WHERE sha256 = ?", (sha,)).fetchone():
        return None  # already indexed
    if LIB_DIR.resolve() not in path.resolve().parents and path.resolve().parent != LIB_DIR.resolve():
        LIB_DIR.mkdir(parents=True, exist_ok=True)
        dest = LIB_DIR / f"{sha[:16]}{path.suffix or '.png'}"
        if not dest.exists():
            dest.write_bytes(blob)
        path, blob = dest, dest.read_bytes()
    with Image.open(path) as im:
        width, height = im.size
    emb = embed_asset(path)
    conn.execute(
        """INSERT INTO vislib_assets
           (sha256, file_path, width, height, license_id, license_url, credit,
            source_url, source, tags_json, phash, hist_json, embedding,
            embedding_model)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            sha, str(path), width, height, license_id, license_url or None,
            credit or None, source_url or None, source,
            json.dumps(sorted(set(tags))), dhash_hex(path), json.dumps(hist_json(path)),
            emb[0] if emb else None, emb[1] if emb else None,
        ),
    )
    return sha


def seed_abstract(count: int = SEED_TARGET, conn=None, out_dir: Path | None = None) -> int:
    """Generate + index `count` abstract assets. Deterministic per-index
    filenames (abs_0042.png) make this idempotent across fresh databases."""
    if conn is None:
        raise ValueError("seed_abstract needs a connection")
    out_dir = Path(out_dir or LIB_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    added = 0
    existing = int(conn.execute("SELECT COUNT(*) AS n FROM vislib_assets").fetchone()["n"])
    for i in range(existing, existing + count):
        path = out_dir / f"abs_{i:04d}.png"
        if not path.exists():
            make_abstract(i).save(path, "PNG")
        got = ingest(
            conn, path, license_id="internal_abstract", source="generated",
            tags=abstract_tags(i),
        )
        if got:
            added += 1
    return added


def ensure_seeded(conn, target: int = SEED_TARGET, note=None) -> int:
    """Idempotent: seed only until the library reaches `target` rows."""
    have = count(conn)
    if have >= target:
        return 0
    added = seed_abstract(target - have, conn=conn)
    if added and note:
        note(f"visual library seeded +{added} abstract assets (total {count(conn)})")
    return added


def count(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM vislib_assets").fetchone()["n"])


def backfill_embeddings(conn, note=None) -> int:
    """Compute vectors for rows ingested before the current encoder existed
    (e.g. rows from when the CLIP model was unavailable). -> rows updated"""
    rows = conn.execute(
        "SELECT sha256, file_path FROM vislib_assets WHERE embedding IS NULL"
    ).fetchall()
    updated = 0
    for row in rows:
        path = Path(row["file_path"])
        if not path.exists():
            continue
        emb = embed_asset(path)
        if not emb:
            continue
        conn.execute(
            "UPDATE vislib_assets SET embedding = ?, embedding_model = ? "
            "WHERE sha256 = ?",
            (emb[0], emb[1], row["sha256"]),
        )
        updated += 1
    if updated and note:
        note(f"visual library: backfilled vectors for {updated} asset(s)")
    return updated


def _score(row_tags: list[str], wanted: set[str]) -> int:
    have = set(row_tags)
    return len(have & wanted)


def query(conn, text: str, limit: int = 5) -> list:
    """Lexical tag match, then visual-feature re-rank; deterministic order
    (lexical score desc, cosine desc, sha asc)."""
    wanted = _tokens(text)
    if not wanted:
        return []
    rows = conn.execute("SELECT * FROM vislib_assets").fetchall()
    scored = []
    for row in rows:
        tags = json.loads(row["tags_json"] or "[]")
        score = _score(tags, wanted)
        if score > 0:
            scored.append((score, row))
    vis = _visual_scores(scored, wanted)
    scored.sort(
        key=lambda pair: (-pair[0], -vis.get(pair[1]["sha256"], 0.0), pair[1]["sha256"])
    )
    return [row for _, row in scored[:limit]]


def _visual_scores(scored: list[tuple[int, object]], wanted: set[str]) -> dict[str, float]:
    """Cosine of each candidate to the query's tag-centroid vector in
    visual-feature space: mean of per-token centroids (centroid of a token =
    mean vector of candidates carrying that tag). Rows without vectors, or a
    library with no vectors at all, score 0 -> order falls back to sha."""
    vectors = {
        row["sha256"]: np.frombuffer(bytes(row["embedding"]), dtype=np.float32)
        for _, row in scored
        if row["embedding"]
    }
    if not vectors:
        return {}
    centroids = []
    for tok in wanted:
        members = [
            vectors[row["sha256"]]
            for _, row in scored
            if tok in set(json.loads(row["tags_json"] or "[]"))
            and row["sha256"] in vectors
        ]
        if members:
            centroids.append(np.mean(members, axis=0))
    if not centroids:
        return {}
    qvec = np.mean(centroids, axis=0)
    nq = float(np.linalg.norm(qvec))
    if nq <= 0:
        return {}
    qvec = qvec / nq
    out: dict[str, float] = {}
    for sha, vec in vectors.items():
        nv = float(np.linalg.norm(vec))
        sim = float(np.dot(qvec, vec)) / max(nv, 1e-9)
        out[sha] = round(max(sim, 0.0), 6)
    return out


def claim(
    conn,
    queries: list[str] | tuple[str, ...],
    *,
    used: dict[str, int] | None = None,
    max_repeat: int = 3,
    limit: int = 5,
):
    """Best library row for these queries that respects the per-project
    repetition cap (QC assertion 4). -> sqlite3.Row | None"""
    used = used or {}
    for q in queries:
        for row in query(conn, q, limit=limit):
            if used.get(row["sha256"], 0) >= max_repeat:
                continue
            path = Path(row["file_path"])
            if not path.exists():  # stale row (library dir moved)
                conn.execute("DELETE FROM vislib_assets WHERE sha256 = ?", (row["sha256"],))
                continue
            return row
    return None


def claim_abstract(
    conn, *, used: dict[str, int] | None = None, max_repeat: int = 3
):
    """Relaxed rung: any generated abstract (S13 gap-fill). -> Row | None"""
    used = used or {}
    rows = conn.execute(
        "SELECT * FROM vislib_assets WHERE source = 'generated' ORDER BY sha256"
    ).fetchall()
    for row in rows:
        if used.get(row["sha256"], 0) >= max_repeat:
            continue
        if not Path(row["file_path"]).exists():
            conn.execute("DELETE FROM vislib_assets WHERE sha256 = ?", (row["sha256"],))
            continue
        return row
    return None


def stats(conn) -> dict:
    row = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END) AS embedded,
                  SUM(CASE WHEN embedding_model = ? THEN 1 ELSE 0 END) AS clip,
                  SUM(CASE WHEN embedding_model = ? THEN 1 ELSE 0 END) AS feature,
                  SUM(CASE WHEN source = 'generated' THEN 1 ELSE 0 END) AS generated
           FROM vislib_assets""",
        (_CLIP_MODEL, FEATURE_MODEL),
    ).fetchone()
    return {
        "total": int(row["total"] or 0),
        "embedded": int(row["embedded"] or 0),
        "generated": int(row["generated"] or 0),
        "clip_model": _CLIP_MODEL if row["clip"] else None,
        "feature_model": FEATURE_MODEL if row["feature"] else None,
    }
