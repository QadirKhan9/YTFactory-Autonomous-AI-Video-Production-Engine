"""D2 Source Scorer — quality tiers, retraction check, >=5 Tier-A HALT gate (S03)."""
from __future__ import annotations

from typing import Any

# Domain authority list (explicit beats clever; extend via registry later).
TIER_A_DOMAINS = {
    "pubmed.ncbi.nlm.nih.gov",
    "nih.gov",
    "ncbi.nlm.nih.gov",
    "nature.com",
    "science.org",
    "cell.com",
    "plos.org",
    "thelancet.com",
    "nejm.org",
    "bmj.com",
    "jamanetwork.com",
    "who.int",
    "openalex.org",
    "openreview.net",
    "arxiv.org",
    "medrxiv.org",
    "biorxiv.org",
    "doi.org",
    "jstor.org",
    "cambridge.org",
    "oup.com",
    "springer.com",
    "wiley.com",
    "sciencedirect.com",
    "tandfonline.com",
    "frontiersin.org",
}

TIER_B_DOMAINS = {
    "wikipedia.org",
    "reuters.com",
    "bbc.com",
    "bbc.co.uk",
    "scientificamerican.com",
    "the-scientist.com",
    "eurekalert.org",
    "phys.org",
}

MIN_TIER_A = 5  # FR-R-05: fewer than 5 Tier-A sources -> BLOCKED_SOURCES


def domain_tier(domain: str) -> str:
    d = domain.lower().removeprefix("www.")
    if d in TIER_A_DOMAINS or d.endswith(".gov") or d.endswith(".edu") or any(d.endswith("." + td) for td in TIER_A_DOMAINS):
        return "A"
    if d in TIER_B_DOMAINS or any(d.endswith("." + tb) for tb in TIER_B_DOMAINS):
        return "B"
    return "C"


def domain_score(domain: str) -> float:
    return {"A": 1.0, "B": 0.6, "C": 0.25}[domain_tier(domain)]


def score_source(record: dict[str, Any]) -> dict[str, Any]:
    """Attach quality_tier/domain_score/is_retracted to a harvest record."""
    tier = domain_tier(record.get("domain", ""))
    score = domain_score(record.get("domain", ""))
    if record.get("year"):
        age_penalty = min(0.3, max(0, (2026 - int(record["year"]) - 10) * 0.02))
        score = max(0.0, score - age_penalty)
    return {
        **record,
        "quality_tier": tier,
        "domain_score": round(score, 3),
        "is_retracted": bool(record.get("is_retracted", False)),
    }


def check_crossref_retraction(doi: str | None) -> bool:
    """True if Crossref flags an update of type retraction (FR-R-03). Lazy httpx."""
    if not doi:
        return False
    import httpx

    doi = doi.removeprefix("https://doi.org/")
    try:
        resp = httpx.get(
            f"https://api.crossref.org/works/{doi}",
            timeout=15.0,
            headers={"User-Agent": "YTFactory/0.1"},
        )
        if resp.status_code != 200:
            return False
        updates = resp.json().get("message", {}).get("update-to", []) or []
        return any(u.get("type") == "retraction" for u in updates)
    except Exception:
        return False


def tier_a_gate(sources: list[dict[str, Any]]) -> tuple[bool, int]:
    """(passed, tier_a_count) — pass requires >= MIN_TIER_A usable Tier-A sources."""
    count = sum(
        1
        for s in sources
        if s.get("quality_tier") == "A" and not s.get("is_retracted") and s.get("clean_text")
    )
    return count >= MIN_TIER_A, count
