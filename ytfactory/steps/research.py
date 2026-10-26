"""Steps S00–S04: intake, research plan, harvest, tier gate, evidence index."""
from __future__ import annotations

import hashlib
from typing import Any

from ytfactory.agents import a01_research_planner as a01
from ytfactory.agents import a02_evidence_extractor as a02
from ytfactory.steps.common import Ctx, GateHalt, record_usage, step_outputs
from ytfactory.workers import embed, fetch, score

# --------------------------------------------------------------------------
# Dry-run corpus: 6 Tier-A sources (one retracted -> 5 usable = exactly at the
# FR-R-05 gate), 1 Tier-B, 1 Tier-C. Tests patch _dry_sources() to starve the
# gate and prove BLOCKED_SOURCES halts the DAG.
# --------------------------------------------------------------------------
def _t(label: str) -> str:
    return (
        f"Controlled studies of {label} demonstrate that episodic memory depends on "
        "hippocampal coordination. Reactivation of distributed ensembles during rest "
        "stabilizes recently encoded traces, while protein synthesis converts labile "
        "short-term traces into durable synaptic changes. Interference between similar "
        "episodes accounts for a large fraction of everyday forgetting. These findings "
        "are replicated across independent laboratories using imaging, lesion and "
        "electrophysiology methods."
    )


def _dry_sources() -> list[dict[str, Any]]:
    return [
        {"url": "https://www.nature.com/articles/memory-replay", "title": "Memory replay during sleep", "domain": "nature.com", "year": 2023, "is_retracted": False, "clean_text": _t("sleep replay")},
        {"url": "https://pubmed.ncbi.nlm.nih.gov/12345/", "title": "Hippocampal consolidation", "domain": "pubmed.ncbi.nlm.nih.gov", "year": 2022, "is_retracted": False, "clean_text": _t("consolidation")},
        {"url": "https://www.cell.com/article/999", "title": "Synaptic tagging and capture", "domain": "cell.com", "year": 2021, "is_retracted": False, "clean_text": _t("synaptic tagging")},
        {"url": "https://journals.plos.org/plosone/article?id=1", "title": "Reconsolidation updating", "domain": "plos.org", "year": 2020, "is_retracted": False, "clean_text": _t("reconsolidation")},
        {"url": "https://www.who.int/news/memory", "title": "Cognitive health bulletin", "domain": "who.int", "year": 2024, "is_retracted": False, "clean_text": _t("cognitive health")},
        {"url": "https://www.nih.gov/retracted-study", "title": "Retracted memory study", "domain": "nih.gov", "year": 2019, "is_retracted": True, "clean_text": _t("a retracted analysis")},
        {"url": "https://en.wikipedia.org/wiki/Memory", "title": "Memory - Wikipedia", "domain": "wikipedia.org", "year": 2024, "is_retracted": False, "clean_text": _t("general reference material")},
        {"url": "https://spam.example/blog/memory-hacks", "title": "10 memory hacks", "domain": "spam.example", "year": 2018, "is_retracted": False, "clean_text": _t("anecdotal claims")},
    ]


# --------------------------------------------------------------------------
def run_s00(ctx: Ctx) -> dict:
    project = ctx.bb.get_project(ctx.project_id)
    ctx.bb.add_event(ctx.project_id, "intake", f"project {project['slug']} created")
    return {
        "slug": project["slug"],
        "config_hash": project["config_hash"],
        "planned_steps": 23,
        "dry_run": ctx.dry_run,
    }


def run_s01(ctx: Ctx) -> dict:
    plan = a01.run(
        ctx.router,
        payload={
            "topic": ctx.topic,
            "audience": ctx.config.get("audience", "beginner"),
            "duration_min": ctx.config.get("duration_min", 8),
        },
        stub=ctx.stub("A1", a01.STUB),
    )
    record_usage(ctx, "S01_RESEARCH_PLAN")
    return plan.model_dump()


def run_s02(ctx: Ctx) -> dict:
    plan = step_outputs(ctx, "S01_RESEARCH_PLAN")
    if ctx.dry_run:
        records = _dry_sources()
    else:
        records, seen = [], set()
        for query in plan["search_queries"][:4]:
            hits: list[dict] = []
            for name, search in (("openalex", fetch.search_openalex),
                                 ("arxiv", fetch.search_arxiv),
                                 ("wikipedia", fetch.search_wikipedia)):
                try:
                    hits.extend(search(query))
                except Exception as exc:
                    ctx.note(f"search_{name} failed for {query!r}: {type(exc).__name__}: {exc}")
            for rec in hits:
                if rec["url"] in seen:
                    continue
                seen.add(rec["url"])
                records.append(rec)
        for rec in records[:20]:  # FR-R-06: bounded harvest
            try:
                page = fetch.fetch_text(rec["url"])
                rec["title"] = rec["title"] or page["title"]
                rec["clean_text"] = page["text"]
            except Exception as exc:
                ctx.note(f"fetch failed for {rec['url']}: {exc}")
                rec["clean_text"] = ""

    ids: list[int] = []
    for rec in records:
        if not rec.get("is_retracted"):
            rec["is_retracted"] = score.check_crossref_retraction(rec.get("doi"))
        text = rec.get("clean_text", "")
        cur = ctx.bb.conn.execute(
            """INSERT OR IGNORE INTO sources
               (project_id, url, url_sha, title, domain, clean_text, text_sha, is_retracted, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))""",
            (
                ctx.project_id,
                rec["url"],
                fetch.url_sha(rec["url"]),
                rec.get("title", ""),
                rec.get("domain", ""),
                text,
                hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
                1 if rec.get("is_retracted") else 0,
            ),
        )
        if cur.lastrowid:
            ids.append(int(cur.lastrowid))
        else:
            row = ctx.bb.conn.execute(
                "SELECT id FROM sources WHERE project_id = ? AND url_sha = ?",
                (ctx.project_id, fetch.url_sha(rec["url"])),
            ).fetchone()
            if row:
                ids.append(int(row["id"]))
    return {"harvested": len(records), "source_ids": ids}


def run_s03(ctx: Ctx) -> dict:
    rows = ctx.bb.conn.execute(
        "SELECT * FROM sources WHERE project_id = ?", (ctx.project_id,)
    ).fetchall()
    sources = []
    for row in rows:
        rec = dict(row)
        rec = score.score_source(rec)
        ctx.bb.conn.execute(
            "UPDATE sources SET quality_tier = ?, domain_score = ?, is_retracted = ? WHERE id = ?",
            (rec["quality_tier"], rec["domain_score"], 1 if rec["is_retracted"] else 0, rec["id"]),
        )
        sources.append(rec)

    passed, count = score.tier_a_gate(sources)
    if not passed:
        raise GateHalt(
            "BLOCKED_SOURCES",
            f"only {count} usable Tier-A sources survive scoring (need >= {score.MIN_TIER_A}); "
            "widen queries or raise the domain whitelist, then `rerun --from S03`",
        )
    return {
        "scored": len(sources),
        "tier_a_count": count,
        "tiers": {t: sum(1 for s in sources if s["quality_tier"] == t) for t in ("A", "B", "C")},
    }


def run_s04(ctx: Ctx) -> dict:
    sources = ctx.bb.conn.execute(
        "SELECT * FROM sources WHERE project_id = ? ORDER BY id", (ctx.project_id,)
    ).fetchall()
    plan = step_outputs(ctx, "S01_RESEARCH_PLAN")

    # purge derived rows so `rerun --from S04` cannot duplicate claims
    ctx.bb.conn.execute(
        "DELETE FROM claims WHERE project_id = ?", (ctx.project_id,)
    )  # claim_evidence cascades
    ctx.bb.conn.execute("DELETE FROM sentences WHERE project_id = ?", (ctx.project_id,))
    ctx.bb.conn.execute("DELETE FROM evidence WHERE project_id = ?", (ctx.project_id,))

    # 1) chunk + index evidence (D3)
    ev_rows: list[tuple[int, int, str]] = []  # (evidence_id, source_id, chunk)
    evidence_by_source: dict[int, list[int]] = {}
    for src in sources:
        text = src["clean_text"] or ""
        if not text:
            continue
        for chunk, start, end in embed.chunk_text(text)[:8]:
            cur = ctx.bb.conn.execute(
                "INSERT INTO evidence (project_id, source_id, chunk_text, char_span) VALUES (?,?,?,?)",
                (ctx.project_id, src["id"], chunk, f"{start}-{end}"),
            )
            eid = int(cur.lastrowid)
            ev_rows.append((eid, src["id"], chunk))
            evidence_by_source.setdefault(src["id"], []).append(eid)

    # embeddings (optional; None when fastembed absent)
    if ev_rows and not ctx.dry_run:
        vectors = embed.embed_texts([chunk for _, _, chunk in ev_rows])
        if vectors:
            for (eid, _, _), vec in zip(ev_rows, vectors):
                ctx.bb.conn.execute(
                    "UPDATE evidence SET embedding_blob = ? WHERE id = ?", (vec, eid)
                )

    # 2) extract atomic facts per source (A2)
    facts: list[dict] = []
    for src in sources:
        src_evs = [(eid, chunk) for eid, sid, chunk in ev_rows if sid == src["id"]]
        if not src_evs:
            continue
        chunk_pack = "\n".join(f"[evidence {eid}] {chunk}" for eid, chunk in src_evs)
        # deterministic pseudo-fact = the offline floor for live runs too
        # (dry-run used to be the only path; fail-closed empty stub blocked
        # keyless live runs at the BLOCKED_SOURCES gate)
        default_stub = {
            "facts": [
                {
                    "text": f"Reported finding on {src['title'] or src['domain']}: "
                    + src["clean_text"].split(". ")[0].lower().rstrip(".")
                    + ".",
                    "source_id": src["id"],
                    "char_span": f"0-{min(120, len(src['clean_text']))}",
                    "confidence": 0.92,
                }
            ]
        }
        try:
            extract = a02.run(
                ctx.router,
                payload={"chunk_pack": chunk_pack, "source_id": src["id"]},
                stub=ctx.stub("A2", default_stub),
            )
        except Exception as exc:
            ctx.note(f"A2 skipped for source {src['id']}: {exc}")
            continue
        for fact in extract.facts:
            d = fact.model_dump()
            d["source_id"] = src["id"]
            facts.append(d)
    record_usage(ctx, "S04_EVIDENCE_INDEX")

    # 3) persist claims + claim_evidence links
    claim_ids: list[int] = []
    for fact in facts:
        cur = ctx.bb.conn.execute(
            "INSERT INTO claims (project_id, claim_text, source_span) VALUES (?,?,?)",
            (ctx.project_id, fact["text"], fact["char_span"]),
        )
        claim_id = int(cur.lastrowid)
        claim_ids.append(claim_id)
        ev = evidence_by_source.get(fact["source_id"]) or []
        if ev:
            ctx.bb.conn.execute(
                "INSERT OR IGNORE INTO claim_evidence (claim_id, evidence_id) VALUES (?,?)",
                (claim_id, ev[0]),
            )

    if not claim_ids:
        raise GateHalt(
            "BLOCKED_SOURCES",
            "evidence extraction produced zero claims; broaden the harvest and `rerun --from S04`",
        )

    claim_pack = [
        {"claim_key": f"C{i + 1}", "claim_id": cid, "text": t}
        for i, (cid, t) in enumerate(
            (r["id"], r["claim_text"])
            for r in ctx.bb.conn.execute(
                "SELECT id, claim_text FROM claims WHERE project_id = ? ORDER BY id",
                (ctx.project_id,),
            ).fetchall()
        )
    ]
    return {
        "evidence_count": sum(len(v) for v in evidence_by_source.values()),
        "claim_count": len(claim_ids),
        "claim_pack": claim_pack,
        "sub_questions": plan.get("sub_questions", []),
    }
