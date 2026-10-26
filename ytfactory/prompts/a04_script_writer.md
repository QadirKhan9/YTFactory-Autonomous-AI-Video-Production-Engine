# A4 — Script Writer

OUTLINE:
{{outline}}

NUMBERED CLAIM/EVIDENCE PACK (the ONLY source of facts; ids are strings like "C1"):
{{claim_pack}}

EDITORIAL CRITIQUE & REVISION NOTES (if provided, apply these improvements):
{{critique_notes}}

Return JSON: {"sentences": [{"ord": 1, "text": "...", "is_rhetorical": false, "claim_ids": ["C1"], "emphasis": "strong|soft|fast|slow|null"}]}

Rules:
- Write the full narration as sequential sentences (ord 1..N, ascending).
- EVERY factual sentence MUST list ≥1 claim_id from the pack (e.g. ["C1"]); otherwise mark "is_rhetorical": true (transitions, questions, callbacks).
- Never copy source prose; synthesize the wording. No new facts.
- Target pacing: {{target_wpm}} wpm. Open with the hook sentence from the outline.
- Sentence brevity & pacing: Keep sentences short and punchy, between 8 and 18 words (maximum 20 words per sentence). NEVER write long compound sentences with multiple clauses, as single scenes must not exceed 12 seconds.
- Provide sufficient sentences (15 to 25 sentences) to cover all beats with lively transitions.
- If revision notes are provided, address retention risks and improve flow accordingly.

