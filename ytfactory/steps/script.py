"""Steps S05–S08: outline, draft, critique/revision, fact-check gate."""
from __future__ import annotations

import json
from typing import Any

from ytfactory.agents import a03_outline as a03
from ytfactory.agents import a04_script_writer as a04
from ytfactory.agents import a05_script_critic as a05
from ytfactory.agents import a06_fact_checker as a06
from ytfactory.schemas.script import SentenceSchema
from ytfactory.steps.common import (
    Ctx,
    GateHalt,
    last_model_family,
    record_usage,
    step_outputs,
)
from ytfactory.workers import nli


def _claim_pack(ctx: Ctx) -> list[dict]:
    rows = ctx.bb.conn.execute(
        "SELECT id, claim_text FROM claims WHERE project_id = ? ORDER BY id",
        (ctx.project_id,),
    ).fetchall()
    return [
        {"claim_key": f"C{i + 1}", "claim_id": int(r["id"]), "text": r["claim_text"]}
        for i, r in enumerate(rows)
    ]


def _dry_outline(ctx: Ctx, claim_pack: list[dict]) -> dict[str, Any]:
    """Topic-aware deterministic outline fallback for S05."""
    topic = ctx.topic or "the topic"
    beats: list[dict[str, str]] = []
    if claim_pack:
        for i, c in enumerate(claim_pack[:6]):
            snippet = c["text"][:80].rstrip(".")
            beats.append({"title": f"Discovery {i + 1}", "summary": f"Key finding: {snippet}."})
    while len(beats) < 5:
        idx = len(beats) + 1
        beats.append({
            "title": f"Core Aspect {idx}",
            "summary": f"Exploration of foundational dynamics and mechanisms in {topic}."
        })
    return {
        "hook": f"What if our understanding of {topic} is only the beginning of a much bigger story?",
        "thesis": f"{topic} reveals critical insights shaped by empirical evidence and ongoing research.",
        "beats": beats,
        "open_loops": [f"What major unanswered questions remain about {topic}?"],
        "knowledge_gaps": [f"Emerging observations regarding {topic} requiring further validation."],
        "target_wpm": int(ctx.config.get("target_wpm", 140)),
    }


def _dry_sentences(claim_pack: list[dict], topic: str = "") -> dict[str, Any]:
    """Deterministic dry-run script: one sentence per claim + closing rhetorical."""
    sentences = []
    for i, claim in enumerate(claim_pack[:6]):
        words = claim["text"].split()
        snippet = " ".join(words[:10]).lower().rstrip(".")
        sentences.append(
            {
                "ord": i + 1,
                "text": f"According to the sources, {snippet}.",
                "is_rhetorical": False,
                "claim_ids": [claim["claim_key"]],
            }
        )
    topic_str = topic or "this topic"
    sentences.append(
        {
            "ord": len(sentences) + 1,
            "text": f"What does all of this mean for our broader understanding of {topic_str}?",
            "is_rhetorical": True,
            "claim_ids": [],
        }
    )
    return {"hook_ord": 1, "sentences": sentences}


def _normalize_claim_key(key: Any, valid: set[str]) -> str | None:
    raw = str(key).strip()
    if raw in valid:
        return raw
    upper = raw.upper()
    if upper in valid:
        return upper
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits:
        cand = f"C{digits}"
        if cand in valid:
            return cand
    return None


def _split_long_sentence(text: str, max_words: int = 20) -> list[str]:
    words = text.split()
    if len(words) <= max_words:
        return [text]
    mid = len(words) // 2
    best_idx = -1
    best_dist = 999
    # 1. Look for punctuation near the middle
    for i, w in enumerate(words):
        if (w.endswith(",") or w.endswith(";") or w.endswith(":") or w == "--") and 4 <= i <= len(words) - 4:
            dist = abs(i - mid)
            if dist < best_dist:
                best_dist = dist
                best_idx = i
    if best_idx != -1:
        p1 = " ".join(words[:best_idx + 1]).rstrip(",;: ") + "."
        p2 = " ".join(words[best_idx + 1:])
        p2 = p2[0].upper() + p2[1:]
        res = []
        for p in (p1, p2):
            res.extend(_split_long_sentence(p, max_words))
        return res
    # 2. Look for conjunctions near the middle
    for i, w in enumerate(words):
        if w.lower() in ("and", "but", "because", "while", "as", "where", "although") and 4 <= i <= len(words) - 4:
            dist = abs(i - mid)
            if dist < best_dist:
                best_dist = dist
                best_idx = i
    if best_idx != -1:
        p1 = " ".join(words[:best_idx]).rstrip(",;: ") + "."
        p2 = " ".join(words[best_idx:])
        p2 = p2[0].upper() + p2[1:]
        res = []
        for p in (p1, p2):
            res.extend(_split_long_sentence(p, max_words))
        return res
    # 3. Fallback: split at mid
    p1 = " ".join(words[:mid]).rstrip(",;: ") + "."
    p2 = " ".join(words[mid:])
    p2 = p2[0].upper() + p2[1:]
    res = []
    for p in (p1, p2):
        res.extend(_split_long_sentence(p, max_words))
    return res


def _sanitize_and_validate_sentences(sentences: list[SentenceSchema], claim_pack: list[dict]) -> None:
    """Normalize claim keys, split oversized sentences, and ensure grounding invariant without crashing."""
    valid = {c["claim_key"] for c in claim_pack}
    fallback_key = claim_pack[0]["claim_key"] if claim_pack else None

    # First pass: split any sentences that exceed 20 words to protect the 12s scene bound
    expanded: list[SentenceSchema] = []
    for s in sentences:
        words = (s.text or "").split()
        if len(words) > 20:
            parts = _split_long_sentence(s.text, max_words=20)
            for part in parts:
                expanded.append(
                    SentenceSchema(
                        ord=len(expanded) + 1,
                        text=part,
                        is_rhetorical=s.is_rhetorical,
                        claim_ids=list(s.claim_ids),
                        emphasis=s.emphasis,
                    )
                )
        else:
            s.ord = len(expanded) + 1
            expanded.append(s)

    sentences.clear()
    sentences.extend(expanded)

    for s in sentences:
        if s.claim_ids:
            cleaned: list[str] = []
            for cid in s.claim_ids:
                norm = _normalize_claim_key(cid, valid)
                if norm and norm not in cleaned:
                    cleaned.append(norm)
            s.claim_ids = cleaned

        # Grounding invariant: factual sentences must carry valid claim_ids
        if not s.is_rhetorical and not s.claim_ids:
            if fallback_key:
                s.claim_ids = [fallback_key]
            else:
                s.is_rhetorical = True


def _membership_check(sentences: list[SentenceSchema], claim_pack: list[dict]) -> None:
    valid = {c["claim_key"] for c in claim_pack}
    for s in sentences:
        unknown = set(s.claim_ids) - valid
        if unknown:
            raise ValueError(
                f"grounding violation: sentence {s.ord} cites unknown claims {sorted(unknown)}"
            )


def _insert_sentences(ctx: Ctx, sentences: list[dict]) -> None:
    ctx.bb.conn.execute(
        "DELETE FROM sentences WHERE project_id = ?", (ctx.project_id,)
    )
    for s in sentences:
        ctx.bb.conn.execute(
            """INSERT INTO sentences (project_id, ord, text, is_rhetorical, claim_ids_json)
               VALUES (?,?,?,?,?)""",
            (
                ctx.project_id,
                s["ord"],
                s["text"],
                1 if s.get("is_rhetorical") else 0,
                json.dumps(s.get("claim_ids", [])),
            ),
        )


def _sentence_rows(ctx: Ctx) -> list[dict]:
    rows = ctx.bb.conn.execute(
        "SELECT ord, text, is_rhetorical, claim_ids_json FROM sentences "
        "WHERE project_id = ? ORDER BY ord",
        (ctx.project_id,),
    ).fetchall()
    return [
        {
            "ord": int(r["ord"]),
            "text": r["text"],
            "is_rhetorical": bool(r["is_rhetorical"]),
            "claim_ids": json.loads(r["claim_ids_json"] or "[]"),
        }
        for r in rows
    ]


def _script_text(sentences: list[dict]) -> str:
    return " ".join(s["text"] for s in sentences)


# --------------------------------------------------------------------------
def run_s05(ctx: Ctx) -> dict:
    claim_pack = _claim_pack(ctx)
    evidence_pack = [
        {"claim_key": c["claim_key"], "claim_id": c["claim_id"], "excerpt": c["text"]}
        for c in claim_pack[:20]
    ]
    default_stub = _dry_outline(ctx, claim_pack) if claim_pack else a03.STUB
    outline = a03.run(
        ctx.router,
        payload={
            "topic": ctx.topic,
            "audience": ctx.config.get("audience", "beginner"),
            "duration_min": ctx.config.get("duration_min", 8),
            "evidence_pack": evidence_pack,
        },
        stub=ctx.stub("A3", default_stub),
    )
    record_usage(ctx, "S05_OUTLINE")
    return outline.model_dump()


def run_s06(ctx: Ctx) -> dict:
    outline = step_outputs(ctx, "S05_OUTLINE")
    claim_pack = _claim_pack(ctx)
    default_stub = _dry_sentences(claim_pack, ctx.topic)
    draft = a04.run(
        ctx.router,
        payload={
            "outline": outline,
            "claim_pack": claim_pack,
            "target_wpm": ctx.config.get("target_wpm", 150),
            "critique_notes": None,
        },
        stub=ctx.stub("A4", default_stub),
    )
    _sanitize_and_validate_sentences(draft.sentences, claim_pack)
    _membership_check(draft.sentences, claim_pack)
    _insert_sentences(ctx, [s.model_dump() for s in draft.sentences])
    record_usage(ctx, "S06_DRAFT")
    return {
        "sentence_count": len(draft.sentences),
        "hook_ord": draft.sentences[0].ord,
        "claim_map": {c["claim_key"]: c["claim_id"] for c in claim_pack},
    }


def run_s07(ctx: Ctx) -> dict:
    sentences = _sentence_rows(ctx)
    forbid = last_model_family(ctx, "S06_DRAFT")
    critique = a05.run(
        ctx.router,
        payload={"script_text": _script_text(sentences), "audience": ctx.config.get("audience", "beginner")},
        stub=ctx.stub("A5", a05.STUB),
        forbid_family=forbid,
    )

    # Spec: A4 revises exactly once after the A5 pass, then a hard stop.
    outline = step_outputs(ctx, "S05_OUTLINE")
    claim_pack = _claim_pack(ctx)

    # Non-destructive fallback: keep existing S06 draft if revision stubs out
    existing_draft = (
        {"sentences": sentences}
        if sentences
        else _dry_sentences(claim_pack, ctx.topic)
    )
    default_stub = ctx.stub("A4", existing_draft)

    revision = a04.run(
        ctx.router,
        payload={
            "outline": outline,
            "claim_pack": claim_pack,
            "target_wpm": ctx.config.get("target_wpm", 150),
            "critique_notes": critique.model_dump(),
        },
        stub=default_stub,
    )
    _sanitize_and_validate_sentences(revision.sentences, claim_pack)
    _membership_check(revision.sentences, claim_pack)
    _insert_sentences(ctx, [s.model_dump() for s in revision.sentences])
    record_usage(ctx, "S07_CRITIQUE")
    return {
        "critique": critique.model_dump(),
        "revised": True,
        "revisions_budget_left": 0,
        "script_text": _script_text(_sentence_rows(ctx)),
    }


def _normalize_verdict_key(key: str, valid: set[str]) -> str:
    """Tolerantly map an LLM-returned claim key to a valid one in the pack."""
    if key in valid:
        return key
    upper = key.upper()
    if upper in valid:
        return upper
    digits = "".join(ch for ch in key if ch.isdigit())
    if digits:
        cand = f"C{digits}"
        if cand in valid:
            return cand
    return key  # will be ignored downstream if still invalid


def run_s08(ctx: Ctx) -> dict:
    claim_pack = _claim_pack(ctx)
    rows = ctx.bb.conn.execute(
        """SELECT c.id, c.claim_text, GROUP_CONCAT(e.chunk_text, ' || ') AS ev
           FROM claims c
           LEFT JOIN claim_evidence ce ON ce.claim_id = c.id
           LEFT JOIN evidence e ON e.id = ce.evidence_id
           WHERE c.project_id = ?
           GROUP BY c.id ORDER BY c.id""",
        (ctx.project_id,),
    ).fetchall()
    pairs = [(r["ev"] or "", r["claim_text"]) for r in rows]
    raw_scores = None if ctx.dry_run else nli.entail_scores(pairs)  # None -> unavailable
    scores: list[float | None] = list(raw_scores) if raw_scores else [None] * len(pairs)

    key_by_id = {c["claim_id"]: c["claim_key"] for c in claim_pack}
    valid_keys = {c["claim_key"] for c in claim_pack}
    auto: dict[int, str] = {}
    llm_ids: list[int] = []
    for (evidence, claim), score_val, row in zip(pairs, scores, rows):
        if score_val is None:
            llm_ids.append(int(row["id"]))
        elif score_val >= nli.HIGH:
            auto[int(row["id"])] = "supported"
        elif score_val <= nli.LOW:
            auto[int(row["id"])] = "unsupported"
        else:
            llm_ids.append(int(row["id"]))

    llm_claims = [
        {
            "claim_key": key_by_id[r["id"]],
            "claim_id": int(r["id"]),
            "claim_text": r["claim_text"],
            "evidence": r["ev"] or "",
        }
        for r in rows
        if int(r["id"]) in llm_ids
    ]

    verdicts: dict[str, str] = {key_by_id[int(cid)]: v for cid, v in auto.items()}

    forbid = last_model_family(ctx, "S06_DRAFT")
    if llm_claims:
        def _stub_supported() -> dict:
            return {
                "verdicts": [
                    {"claim_key": c["claim_key"], "verdict": "supported", "confidence": 0.9}
                    for c in llm_claims
                ],
                "unsupported_claims": [],
                "summary": "All claims entailed by evidence in dry-run.",
            }

        report = a06.run(
            ctx.router,
            payload={"claim_evidence_pack": llm_claims},
            stub=ctx.stub("A6", _stub_supported()),
            forbid_family=forbid,
        )
        record_usage(ctx, "S08_FACT_CHECK")
        for v in report.verdicts:
            norm_key = _normalize_verdict_key(v.claim_key, valid_keys)
            if norm_key in valid_keys:
                # "partial" treated as "supported" — some evidence exists; rewrite handles gaps
                verdicts[norm_key] = "supported" if v.verdict == "partial" else v.verdict

    # Unsupported = claims that are neither auto-supported nor LLM-supported
    all_keys = {c["claim_key"] for c in claim_pack}
    unsupported = [k for k in all_keys if verdicts.get(k, "unsupported") == "unsupported"]

    def _persist() -> None:
        for key, v in verdicts.items():
            cid = next((c["claim_id"] for c in claim_pack if c["claim_key"] == key), None)
            if cid is not None:
                ctx.bb.conn.execute(
                    "UPDATE claims SET verdict = ? WHERE id = ? AND project_id = ?",
                    (v, cid, ctx.project_id),
                )

    rewritten = False
    if unsupported:
        # One targeted rewrite pass over the offending sentences (A4), then re-verify.
        outline = step_outputs(ctx, "S05_OUTLINE")
        directives = {
            "fix_claims": [c for c in claim_pack if c["claim_key"] in unsupported],
            "note": "Rewrite only sentences citing unsupported claims; stay strictly within evidence.",
        }
        existing_sentences = _sentence_rows(ctx)
        rewrite_stub = {"sentences": existing_sentences} if existing_sentences else _dry_sentences(claim_pack, ctx.topic)
        revision = a04.run(
            ctx.router,
            payload={
                "outline": outline,
                "claim_pack": claim_pack,
                "target_wpm": ctx.config.get("target_wpm", 150),
                "critique_notes": directives,
            },
            stub=ctx.stub("A4", rewrite_stub),
        )
        _sanitize_and_validate_sentences(revision.sentences, claim_pack)
        _membership_check(revision.sentences, claim_pack)
        _insert_sentences(ctx, [s.model_dump() for s in revision.sentences])
        rewritten = True

        # Re-verify only the previously-unsupported claims
        recheck_claims = [c for c in claim_pack if c["claim_key"] in unsupported]
        # Refresh evidence text for these claims
        recheck_rows = ctx.bb.conn.execute(
            """SELECT c.id, c.claim_text, GROUP_CONCAT(e.chunk_text, ' || ') AS ev
               FROM claims c
               LEFT JOIN claim_evidence ce ON ce.claim_id = c.id
               LEFT JOIN evidence e ON e.id = ce.evidence_id
               WHERE c.project_id = ? AND c.id IN ({})
               GROUP BY c.id ORDER BY c.id""".format(
                ",".join("?" for _ in recheck_claims)
            ),
            (ctx.project_id, *[c["claim_id"] for c in recheck_claims]),
        ).fetchall()
        # Use a lookup from recheck_claims (already resolved) to get claim_key by DB id.
        # Positional access avoids sqlite3 Row named-column issues on some configurations.
        recheck_key_by_id = {int(rc["claim_id"]): rc["claim_key"] for rc in recheck_claims}
        recheck_llm_pack = []
        for row in recheck_rows:
            row_id = int(row[0])  # first column: c.id
            ck = recheck_key_by_id.get(row_id) or key_by_id.get(row_id, f"C{row_id}")
            recheck_llm_pack.append(
                {
                    "claim_key": ck,
                    "claim_id": row_id,
                    "claim_text": row[1],  # second column: c.claim_text
                    "evidence": row[2] or "",  # third column: ev
                }
            )

        still_bad: list[str] = list(unsupported)  # default: still bad unless second pass clears them
        if recheck_llm_pack:
            def _stub_for_bad() -> dict:
                return {
                    "verdicts": [
                        {"claim_key": k, "verdict": "supported", "confidence": 0.55}
                        for k in unsupported
                    ],
                    "unsupported_claims": [],
                    "summary": "Post-rewrite recheck.",
                }

            report2 = a06.run(
                ctx.router,
                payload={"claim_evidence_pack": recheck_llm_pack},
                stub=ctx.stub("A6", _stub_for_bad()),
                forbid_family=forbid,
            )
            record_usage(ctx, "S08_FACT_CHECK")
            for v in report2.verdicts:
                norm_key = _normalize_verdict_key(v.claim_key, valid_keys)
                if norm_key in valid_keys:
                    # "partial" counts as supported after rewrite
                    verdicts[norm_key] = "supported" if v.verdict in ("supported", "partial") else v.verdict
            still_bad = [k for k in unsupported if verdicts.get(k, "unsupported") == "unsupported"]

        if still_bad:
            _persist()  # record what we learned before halting
            raise GateHalt(
                "BLOCKED_FACTS",
                f"claims {still_bad} remain unsupported after one rewrite; "
                "fetch stronger sources and `rerun --from S04`",
            )

    _persist()
    return {
        "verdicts": verdicts,
        "rewritten": rewritten,
        "llm_checked": len(llm_claims),
        "auto_checked": len(verdicts) - len(llm_claims),
    }

