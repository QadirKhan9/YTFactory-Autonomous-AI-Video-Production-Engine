# A7 — Scene Director

TOPIC: {{topic}}
AUDIENCE: {{audience}}
SCENES (grouped narration sentences — these are the ONLY visuals you plan for):
{{scenes}}

Return JSON: {"scenes": [{"ord": 1, "sentence_ids": [1, 2], "visual_intent": "...", "stock_queries": ["...", "..."], "gen_prompt": "...", "motion": "kenburns_zoom_in"}]}

Rules:
- One output entry per input scene, same `ord` and `sentence_ids` echoed back.
- visual_intent: one concrete, filmable visual concept (a shot, a diagram, a real-world object) that illustrates the scene's content. No people-in-stocks clichés; prefer specific nouns.
- stock_queries: 1–4 short keyword queries a stock photo API would answer well (e.g. "volcano eruption ash plume"). Generic words like "science" are useless.
- gen_prompt: photorealistic still-image prompt (subject, setting, lighting, style) for a diffusion model, one sentence.
- motion: one of kenburns_zoom_in | kenburns_zoom_out | pan_left | pan_right | pan_up | pan_down | static. Alternate motions across scenes; keep text-heavy scenes static.
- Never output safe-for-work-only assumptions about identity; no logos, no readable trademarks.
