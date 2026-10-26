"""D1 Source Fetcher — HTTP GET + extraction, deduplicated by url_sha (S02).

Heavy deps (trafilatura) are lazy-imported; search APIs need no API key.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

import httpx

TIMEOUT = 20.0
UA = {"User-Agent": "YTFactory/1.0 (https://github.com/ytfactory; ytfactory@example.com)"}


def _clean_query(query: str) -> str:
    """A1 emits scholarly syntax (quotes, boolean AND/OR, wildcards);
    search APIs need plain search terms to avoid 400/500 errors."""
    terms = re.sub(r'[*"+\(\)\[\]]', " ", query)
    terms = re.sub(r"\b(AND|OR|NOT)\b", " ", terms, flags=re.I)
    return " ".join(terms.split())


def url_sha(url: str) -> str:
    return hashlib.sha256(url.strip().lower().encode("utf-8")).hexdigest()


def search_openalex(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """OpenAlex works search (CC0, no key). Returns venue URL/title/year."""
    clean = _clean_query(query)
    resp = httpx.get(
        "https://api.openalex.org/works",
        params={"search": clean, "per-page": limit, "filter": "is_retracted:false"},
        headers=UA,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    out = []
    for w in resp.json().get("results", []):
        primary = w.get("primary_location") or {}
        src = primary.get("source") or {}
        landing = primary.get("landing_page_url") or w.get("doi") or ""
        if not landing:
            continue
        out.append(
            {
                "url": landing,
                "title": w.get("display_name") or "",
                "domain": _domain(landing),
                "year": w.get("publication_year"),
                "doi": w.get("doi"),
                "is_oa": bool(w.get("open_access", {}).get("is_oa")),
                "venue": src.get("display_name") or "",
            }
        )
    return out


def search_arxiv(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """arXiv API search (Atom, keyless). arxiv.org is Tier-A; publishers behind
    DOI links often 403, so this is the reliable scholarly backend for STEM."""
    import xml.etree.ElementTree as ET

    terms = _clean_query(query)
    resp = httpx.get(
        "https://export.arxiv.org/api/query",
        params={
            "search_query": f"all:{terms}",
            "max_results": limit,
            "sortBy": "relevance",
        },
        headers=UA,
        timeout=TIMEOUT,
        follow_redirects=True,
    )
    resp.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return out
    for entry in root.findall("a:entry", ns):
        url = (entry.findtext("a:id", "", ns) or "").strip()
        if not url:
            continue
        published = (entry.findtext("a:published", "", ns) or "")[:4]
        doi = ""
        for link in entry.findall("a:link", ns):
            if link.get("title") == "doi":
                doi = link.get("href", "")
        out.append(
            {
                "url": url,
                "title": re.sub(r"\s+", " ", entry.findtext("a:title", "", ns) or "").strip(),
                "domain": _domain(url),
                "year": int(published) if published.isdigit() else None,
                "doi": doi or None,
                "is_oa": True,
                "venue": "arXiv",
            }
        )
    return out


def search_wikipedia(query: str, limit: int = 3) -> list[dict[str, Any]]:
    clean = _clean_query(query)
    resp = httpx.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "list": "search",
            "srsearch": clean,
            "srlimit": limit,
            "format": "json",
        },
        headers=UA,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    out = []
    for hit in resp.json().get("query", {}).get("search", []):
        title = hit["title"].replace(" ", "_")
        out.append(
            {
                "url": f"https://en.wikipedia.org/wiki/{title}",
                "title": hit["title"],
                "domain": "wikipedia.org",
                "year": None,
                "doi": None,
                "is_oa": True,
                "venue": "Wikipedia",
            }
        )
    return out


def fetch_text(url: str) -> dict[str, Any]:
    """GET a page and extract clean text (trafilatura, with a regex fallback)."""
    resp = httpx.get(url, headers=UA, timeout=TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    html = resp.text
    title = ""
    try:
        # pyrefly: ignore[missing-import]
        import trafilatura

        title = trafilatura.extract_metadata(html).title or "" if trafilatura.extract_metadata(html) else ""
        text = trafilatura.extract(html) or ""
    except Exception:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.I)
        title = title_match.group(1).strip() if title_match else ""
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.DOTALL | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
    return {"title": title, "text": text}


def _domain(url: str) -> str:
    m = re.search(r"https?://([^/]+)", url)
    host = m.group(1) if m else url
    return host.lower().removeprefix("www.")
