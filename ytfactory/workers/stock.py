"""D5 Stock Searcher — parallel queries across keyless permissive sources.

Phase 3 ships the keyless tier (prd §5.x): Wikimedia Commons, Openverse,
Met Museum. Pexels/Pixabay/Smithsonian/NASA slot in later by extending
SOURCE_REGISTRY with their client functions + API keys (no step changes).

Normalized result shape:
  {source, asset_id, title, url, width, height, license_name, license_url, credit}
All network failures are swallowed per-source — an unavailable API degrades
result count, never fails the step (R-03).
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import os
from pathlib import Path
from typing import Callable

import httpx

DEFAULT_SOURCES = ("pixabay", "pexels", "nasa")
DEFAULT_TIMEOUT = 8.0
USER_AGENT = "YTFactory/1.0 (batch video research tool)"

# commons/openverse/met image mime whitelist
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _get_json(url: str, params: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    resp = httpx.get(
        url,
        params=params,
        timeout=httpx.Timeout(timeout, connect=3.0),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    resp.raise_for_status()
    return resp.json()


# ------------------------------------------------------------------ sources
def search_wikimedia(query: str, limit: int = 5) -> list[dict]:
    data = _get_json(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query",
            "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}",
            "gsrnamespace": 6,
            "gsrlimit": limit,
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata",
            "iiurlwidth": 1920,
            "format": "json",
        },
    )
    out = []
    for page in (data.get("query", {}).get("pages", {}) or {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {}) or {}
        url = info.get("thumburl") or info.get("url")
        if not url or not str(url).lower().endswith(_IMAGE_EXTS):
            continue
        out.append(
            {
                "source": "wikimedia",
                "asset_id": str(page.get("pageid", "")),
                "title": (page.get("title") or "").removeprefix("File:"),
                "url": url,
                "width": info.get("thumbwidth") or info.get("width"),
                "height": info.get("thumbheight") or info.get("height"),
                "license_name": (meta.get("LicenseShortName", {}) or {}).get("value", ""),
                "license_url": (meta.get("LicenseUrl", {}) or {}).get("value", ""),
                "credit": _strip_html((meta.get("Artist", {}) or {}).get("value", ""))[:120],
            }
        )
    return out


def search_openverse(query: str, limit: int = 5) -> list[dict]:
    data = _get_json(
        "https://api.openverse.org/v1/images/",
        params={"q": query, "page_size": limit, "license_type": "all-cc,commercial"},
    )
    out = []
    for item in data.get("results", []) or []:
        url = item.get("url")
        if not url:
            continue
        lic = str(item.get("license") or "").upper()
        version = item.get("license_version") or ""
        out.append(
            {
                "source": "openverse",
                "asset_id": str(item.get("id", "")),
                "title": (item.get("title") or "")[:200],
                "url": url,
                "width": item.get("width"),
                "height": item.get("height"),
                "license_name": f"CC {lic} {version}".strip(),
                "license_url": item.get("license_url") or "",
                "credit": (item.get("creator") or "")[:120],
            }
        )
    return out


def search_met(query: str, limit: int = 5) -> list[dict]:
    data = _get_json(
        "https://collectionapi.metmuseum.org/public/collection/v1/search",
        params={"hasImage": "true", "q": query},
        timeout=4.0,
    )
    ids = (data.get("objectIDs") or [])[: min(limit, 3)]
    out: list[dict] = []
    for object_id in ids:
        if len(out) >= limit:
            break
        try:
            obj = _get_json(
                f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{object_id}",
                timeout=3.0,
            )
        except Exception:
            continue
        if not obj.get("primaryImageSmall") or not obj.get("isPublicDomain"):
            continue
        out.append(
            {
                "source": "met",
                "asset_id": str(object_id),
                "title": (obj.get("title") or "")[:200],
                "url": obj.get("primaryImage") or obj.get("primaryImageSmall"),
                "width": None,
                "height": None,
                "license_name": "CC0",
                "license_url": "https://www.metmuseum.org/about-the-met/policies-and-documents/open-access",
                "credit": (obj.get("artistDisplayName") or "The Met")[:120],
            }
        )
    return out


def _get_api_key(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if val:
        return val
    for env_path in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent.parent / ".env"):
        if env_path.exists():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith(f"{name}="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
            except Exception:
                pass
    return ""


def search_pixabay(query: str, limit: int = 5) -> list[dict]:
    api_key = _get_api_key("PIXABAY_API_KEY")
    if not api_key:
        return []
    out: list[dict] = []
    # 1. Search photos
    try:
        data = _get_json(
            "https://pixabay.com/api/",
            params={
                "key": api_key,
                "q": query,
                "image_type": "photo",
                "per_page": max(3, min(200, limit)),
                "orientation": "horizontal",
                "safesearch": "true",
            },
        )
        for item in (data.get("hits") or []):
            url = item.get("largeImageURL") or item.get("webformatURL")
            if not url:
                continue
            out.append(
                {
                    "source": "pixabay",
                    "asset_id": str(item.get("id", "")),
                    "title": item.get("tags") or query,
                    "url": url,
                    "width": item.get("imageWidth"),
                    "height": item.get("imageHeight"),
                    "license_name": "Pixabay License",
                    "license_url": "https://pixabay.com/service/license-summary/",
                    "credit": (item.get("user") or "Pixabay")[:120],
                    "media_type": "image",
                }
            )
    except Exception:
        pass

    # 2. Search videos
    try:
        vdata = _get_json(
            "https://pixabay.com/api/videos/",
            params={
                "key": api_key,
                "q": query,
                "per_page": max(3, min(200, limit)),
                "safesearch": "true",
            },
        )
        for item in (vdata.get("hits") or []):
            vfiles = item.get("videos") or {}
            target = vfiles.get("large") or vfiles.get("medium") or vfiles.get("small") or {}
            vurl = target.get("url")
            if not vurl:
                continue
            out.append(
                {
                    "source": "pixabay",
                    "asset_id": str(item.get("id", "")),
                    "title": item.get("tags") or query,
                    "url": vurl,
                    "width": target.get("width"),
                    "height": target.get("height"),
                    "license_name": "Pixabay License",
                    "license_url": "https://pixabay.com/service/license-summary/",
                    "credit": (item.get("user") or "Pixabay")[:120],
                    "media_type": "video",
                }
            )
    except Exception:
        pass
    return out


def search_pexels(query: str, limit: int = 5) -> list[dict]:
    api_key = _get_api_key("PEXELS_API_KEY")
    if not api_key:
        return []
    headers = {"Authorization": api_key, "User-Agent": USER_AGENT}
    out: list[dict] = []
    # 1. Search photos
    try:
        resp = httpx.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "per_page": limit, "orientation": "landscape"},
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code == 200:
            for photo in (resp.json().get("photos") or []):
                src = photo.get("src") or {}
                url = src.get("large2x") or src.get("large") or src.get("original")
                if not url:
                    continue
                out.append(
                    {
                        "source": "pexels",
                        "asset_id": str(photo.get("id", "")),
                        "title": photo.get("alt") or query,
                        "url": url,
                        "width": photo.get("width"),
                        "height": photo.get("height"),
                        "license_name": "Pexels License",
                        "license_url": "https://www.pexels.com/license/",
                        "credit": (photo.get("photographer") or "Pexels")[:120],
                        "media_type": "image",
                    }
                )
    except Exception:
        pass

    # 2. Search videos
    try:
        vresp = httpx.get(
            "https://api.pexels.com/videos/search",
            params={"query": query, "per_page": max(2, limit // 2), "orientation": "landscape"},
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
        if vresp.status_code == 200:
            for video in (vresp.json().get("videos") or []):
                vfiles = video.get("video_files") or []
                candidates = [
                    f for f in vfiles
                    if f.get("link") and (f.get("file_type") == "video/mp4" or ".mp4" in f.get("link", ""))
                ]
                candidates.sort(key=lambda f: (f.get("width") or 0), reverse=True)
                if not candidates:
                    continue
                chosen = candidates[0]
                out.append(
                    {
                        "source": "pexels",
                        "asset_id": str(video.get("id", "")),
                        "title": (video.get("user") or {}).get("name") or query,
                        "url": chosen["link"],
                        "width": chosen.get("width"),
                        "height": chosen.get("height"),
                        "license_name": "Pexels License",
                        "license_url": "https://www.pexels.com/license/",
                        "credit": ((video.get("user") or {}).get("name") or "Pexels")[:120],
                        "media_type": "video",
                    }
                )
    except Exception:
        pass
    return out


def search_nasa(query: str, limit: int = 5) -> list[dict]:
    try:
        data = _get_json(
            "https://images-api.nasa.gov/search",
            params={"q": query, "media_type": "image"},
            timeout=4.0,
        )
    except Exception:
        return []
    items = (data.get("collection", {}) or {}).get("items", []) or []
    out = []
    for item in items[:limit]:
        item_data = (item.get("data") or [{}])[0]
        links = item.get("links") or [{}]
        url = links[0].get("href")
        if not url:
            continue
        out.append(
            {
                "source": "nasa",
                "asset_id": str(item_data.get("nasa_id", "")),
                "title": item_data.get("title") or query,
                "url": url,
                "width": None,
                "height": None,
                "license_name": "NASA Public Domain",
                "license_url": "https://www.nasa.gov/nasa-brand-media-and-usage-guidelines/",
                "credit": (item_data.get("photographer") or item_data.get("center") or "NASA")[:120],
                "media_type": "image",
            }
        )
    return out


SOURCE_REGISTRY: dict[str, Callable[[str, int], list[dict]]] = {
    "pixabay": search_pixabay,
    "pexels": search_pexels,
    "nasa": search_nasa,
    "wikimedia": search_wikimedia,
    "openverse": search_openverse,
    "met": search_met,
}


# ------------------------------------------------------------------ driver
def search_all(
    queries: list[str],
    sources: tuple[str, ...] | list[str] = DEFAULT_SOURCES,
    per_source: int = 4,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[list[dict], list[str]]:
    """Run every (source, query) pair concurrently; dedupe by URL.

    Returns (results, errors) — errors are "<source>:<query>:<Exc>" strings
    for observability only; a dead source never fails the caller.
    """
    tasks: list[tuple[str, str]] = [
        (name, q) for q in queries for name in sources if name in SOURCE_REGISTRY
    ]
    results: list[dict] = []
    errors: list[str] = []

    def _run(name: str, query: str) -> list[dict]:
        return SOURCE_REGISTRY[name](query, per_source)

    max_workers = min(32, max(1, len(tasks)))
    # Scale global deadline gracefully with task volume, bounded between timeout and 180s
    overall_timeout = min(180.0, max(timeout, timeout + (len(tasks) / float(max_workers)) * 3.0))

    with cf.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_run, n, q): (n, q) for n, q in tasks}
        try:
            for future in cf.as_completed(futures, timeout=overall_timeout):
                name, query = futures[future]
                try:
                    for item in future.result(timeout=0):
                        item["_query"] = query
                        results.append(item)
                except Exception as exc:  # noqa: BLE001 - source death degrades, never fails
                    errors.append(f"{name}:{query}:{type(exc).__name__}")
        except TimeoutError:
            for future, (name, query) in futures.items():
                if not future.done():
                    future.cancel()
                    errors.append(f"{name}:{query}:TimeoutError")

    seen: set[str] = set()
    deduped = []
    for item in sorted(results, key=lambda r: (r["source"], r.get("title") or "")):
        key = item["url"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped, errors


# ------------------------------------------------------------------ ingest
def download_image(url: str, dest_dir: str | Path, timeout: float = 45.0) -> tuple[Path, str]:
    """Download image or video bytes -> (path, sha256). Path is `<dest_dir>/<sha><ext>`."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    resp = httpx.get(
        url, timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )
    resp.raise_for_status()
    data = resp.content
    if len(data) > 60 * 1024 * 1024:
        raise ValueError(f"media too large: {len(data)} bytes")
    ctype = resp.headers.get("content-type", "").lower()
    clean_url = url.lower().split("?")[0]
    if "mp4" in ctype or clean_url.endswith(".mp4"):
        ext = ".mp4"
    elif "webm" in ctype or clean_url.endswith(".webm"):
        ext = ".webm"
    elif "png" in ctype or clean_url.endswith(".png"):
        ext = ".png"
    elif "webp" in ctype or clean_url.endswith(".webp"):
        ext = ".webp"
    else:
        ext = ".jpg"
    sha = hashlib.sha256(data).hexdigest()
    path = dest_dir / f"{sha}{ext}"
    if not path.exists():
        path.write_bytes(data)
    return path, sha


def _strip_html(value: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", value or "").strip()
