"""D6 License Validator (FR-V-03) — the wall between sourcing and rendering.

classify(): maps raw source license strings onto the known registry; anything
unknown or non-commercial resolves to commercial_ok=False.
snapshot_terms(): best-effort fetch of the license/deed page into CAS
(store/cas/<sha[:2]>/<sha>.html) for audit — returns None when unreachable
(snapshot is evidence, not a gate condition).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CAS_DIR = REPO_ROOT / "store" / "cas"
USER_AGENT = "YTFactory/1.0 (batch video research tool)"

# Self-made content (typographic cards) — always commercial-safe.
SELF_MADE_ID = "internal_typographic"

# ordered (pattern, license_id, commercial_ok, canonical_url)
_RULES: list[tuple[str, str, bool, str]] = [
    (r"\bCC0\b|CC0[- ]1", "CC0-1.0", True, "https://creativecommons.org/publicdomain/zero/1.0/"),
    (r"\bPDM\b|PUBLIC DOMAIN|NO KNOWN COPYRIGHT", "PDM", True, "https://creativecommons.org/publicdomain/mark/1.0/"),
    (r"CC[ -]?BY[ -]?SA[ -]?4", "CC-BY-SA-4.0", True, "https://creativecommons.org/licenses/by-sa/4.0/"),
    (r"CC[ -]?BY[ -]?NC[ -]?SA", "CC-BY-NC-SA-4.0", False, "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    (r"CC[ -]?BY[ -]?NC", "CC-BY-NC-4.0", False, "https://creativecommons.org/licenses/by-nc/4.0/"),
    (r"CC[ -]?BY[ -]?ND", "CC-BY-ND-4.0", False, "https://creativecommons.org/licenses/by-nd/4.0/"),
    (r"CC[ -]?BY[ -]?3", "CC-BY-3.0", True, "https://creativecommons.org/licenses/by/3.0/"),
    (r"CC[ -]?BY[ -]?2", "CC-BY-2.0", True, "https://creativecommons.org/licenses/by/2.0/"),
    (r"CC[ -]?BY[ -]?", "CC-BY-4.0", True, "https://creativecommons.org/licenses/by/4.0/"),
    (r"CC[ -]?BY", "CC-BY-4.0", True, "https://creativecommons.org/licenses/by/4.0/"),
    (r"OPEN ACCESS|OPEN ACCESS CC0|MET OPEN ACCESS", "CC0-1.0", True,
     "https://www.metmuseum.org/about-the-met/policies-and-documents/open-access"),
    (r"YOUTUBE AUDIO LIBRARY|YOUTUBE AUDIO LIB\b", "youtube-audio-library", True,
     "https://www.youtube.com/audiolibrary"),
    (r"PIXABAY", "pixabay-license", True, "https://pixabay.com/service/license-summary/"),
    (r"PEXELS", "pexels-license", True, "https://www.pexels.com/license/"),
    (r"\bNASA\b", "nasa-media", True, "https://www.nasa.gov/nasa-brand-media-and-usage-guidelines/"),
    (r"\bPD\b|PD-OLD|PD-OLD-100", "PDM", True, "https://creativecommons.org/publicdomain/mark/1.0/"),
]

_UNKNOWN = ("unknown", "unknown", False, "")


def classify(license_name: str, license_url: str = "") -> tuple[str, bool, str]:
    """-> (license_id, commercial_ok, canonical_url)."""
    raw = (license_name or "").strip()
    if not raw:
        # fall back to the URL itself (e.g. openverse sometimes only gives links)
        raw = license_url or ""
    blob = raw.upper()
    for pattern, lic_id, ok, url in _RULES:
        if re.search(pattern, blob):
            if license_url and lic_id.startswith("CC"):
                return lic_id, ok, license_url  # keep the source's exact deed link
            return lic_id, ok, url
    return _UNKNOWN[0], _UNKNOWN[2], license_url


def is_self_made(license_id: str) -> bool:
    return license_id == SELF_MADE_ID


def snapshot_terms(url: str, cas_dir: str | Path = CAS_DIR, timeout: float = 10.0) -> str | None:
    """Fetch license/deed HTML into CAS. -> sha256 | None (best-effort)."""
    if not url:
        return None
    try:
        resp = httpx.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT},
                         follow_redirects=True)
        resp.raise_for_status()
        data = resp.text.encode("utf-8", errors="replace")[:512 * 1024]
    except Exception:  # noqa: BLE001 - evidence snapshot is never a gate
        return None
    sha = hashlib.sha256(data).hexdigest()
    cas_dir = Path(cas_dir)
    target = cas_dir / sha[:2] / f"{sha}.html"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return sha
