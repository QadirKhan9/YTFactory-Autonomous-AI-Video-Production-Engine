"""Attribution ledgers for S18 (FR-V-06 / FR-P-05): CREDITS.md + SOURCES.md."""
from __future__ import annotations

from ytfactory.db import Blackboard

# license ids that require attribution when reused commercially
_ATTRIBUTION_REQUIRED_PREFIXES = ("CC-BY", "CC-BY-SA", "nasa-media")
_NO_ATTRIBUTION = ("CC0-1.0", "PDM", "internal_typographic", "unknown", "")


def requires_attribution(license_id: str) -> bool:
    lic = (license_id or "").strip()
    if lic in _NO_ATTRIBUTION or lic.startswith("CC0"):
        return False
    return lic.startswith(_ATTRIBUTION_REQUIRED_PREFIXES) or lic.startswith("CC-BY")


def _ms_chapter(ms: int) -> str:
    total_s = int(round(ms / 1000))
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def load_assets(bb: Blackboard, project_id: int) -> list[dict]:
    return [
        dict(r)
        for r in bb.conn.execute(
            "SELECT * FROM media_assets WHERE project_id = ? ORDER BY retrieved_at, sha256",
            (project_id,),
        ).fetchall()
    ]


def attribution_line(asset: dict) -> str:
    title = (asset.get("source_title") or "").strip() or "Untitled asset"
    credit = (asset.get("credit") or "").strip()
    lic = asset.get("license_id") or "unknown"
    lic_url = (asset.get("license_url") or "").strip()
    url = asset.get("source_url") or ""
    who = f" by {credit}" if credit else ""
    lic_part = f"{lic} — {lic_url}" if lic_url else lic
    line = f'- "{title}"{who} — {lic_part} — source: {url}'
    return line


def build_credits(bb: Blackboard, project_id: int, topic: str) -> str:
    """CREDITS.md — exact attribution strings for every attribution-required asset."""
    assets = load_assets(bb, project_id)
    required = [a for a in assets if requires_attribution(a.get("license_id") or "")]
    lines = [
        f"# CREDITS — {topic}",
        "",
        "AI-generated content: narration and visuals are AI-assisted; "
        "claims are cited from the sources in `SOURCES.md`.",
        "",
    ]
    if required:
        lines.append("## Licensed assets (attribution required)")
        lines.append("")
        for asset in required:
            lines.append(attribution_line(asset))
        lines.append("")
    else:
        lines.append("_No third-party attribution required for this video's assets._")
        lines.append("")
    optional = [a for a in assets if not requires_attribution(a.get("license_id") or "")]
    if optional:
        lines.append("## Additional assets (public domain / self-made)")
        lines.append("")
        for asset in optional:
            lines.append(attribution_line(asset))
        lines.append("")
    return "\n".join(lines)


def build_sources(bb: Blackboard, project_id: int, topic: str) -> str:
    """SOURCES.md — the research corpus behind the script."""
    rows = [
        dict(r)
        for r in bb.conn.execute(
            "SELECT url, title, domain, quality_tier FROM sources "
            "WHERE project_id = ? ORDER BY quality_tier, id",
            (project_id,),
        ).fetchall()
    ]
    lines = [f"# SOURCES — {topic}", ""]
    if not rows:
        lines.append("_No sources recorded._")
        lines.append("")
        return "\n".join(lines)
    for i, row in enumerate(rows, start=1):
        tier = row.get("quality_tier") or "-"
        title = (row.get("title") or row.get("domain") or "Untitled").strip()
        lines.append(f"{i}. [{title}]({row['url']}) — {row.get('domain') or '?'} (tier {tier})")
    lines.append("")
    return "\n".join(lines)


def credits_summary_line(bb: Blackboard, project_id: int) -> str:
    """One-line attribution summary embedded in the YouTube description."""
    assets = load_assets(bb, project_id)
    required = [a for a in assets if requires_attribution(a.get("license_id") or "")]
    if not required:
        return "Visuals: public-domain / self-made assets (see CREDITS.md)."
    parts = []
    for asset in required[:5]:
        title = (asset.get("source_title") or "asset").strip()
        lic = asset.get("license_id") or ""
        parts.append(f'"{title}" ({lic})')
    more = f" (+{len(required) - 5} more)" if len(required) > 5 else ""
    return "Visuals: " + ", ".join(parts) + more + " — full attribution in CREDITS.md."
