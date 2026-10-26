# Autonomous YouTube Production Platform — Full Architecture

**Target operator:** One developer, i5-6300U / 8 GB RAM / Intel HD 520 / Windows 11, no discrete GPU, no credit card.  
**Hard constraint:** $0 recurring software cost, no vendor lock-in.  
**Input:** `TOPIC: "How does the human brain create memories?"` → **Output:** `final.mp4` + thumbnail + SRT + metadata + license ledger.

---

## 0. Read this before the architecture

Three facts decide almost every design choice below. Everything else follows from them.

**0.1 — Your laptop is the conductor and the render farm, not the brain.**  
A Skylake-U 2-core with 8 GB RAM can do three things well: run Python glue, run SQLite/Postgres, and run FFmpeg. It can also run small CPU models (Kokoro TTS at ~1–3× realtime, ONNX embeddings, a small NLI reranker). It cannot run a 12B diffusion model or a 30B LLM. So: **LLM work → free hosted APIs. Image generation → burst GPU. Everything deterministic → local.**

**0.2 — No credit card is a bigger constraint than no money.**  
It eliminates Runpod, Modal, Vast.ai, Cloudflare R2, Backblaze B2, Oracle Always Free, GCP/AWS/Azure credits, and OpenRouter's higher free tier (which needs $10 of *lifetime* credit purchased to go from 50 req/day to 1,000 req/day). It leaves: Kaggle, Colab, Hugging Face, Groq, Cerebras, Google AI Studio, Mistral, Lightning AI (phone verify). The architecture below is built only from card-free components.

**0.3 — "Press button, get monetized video" is the one thing you should not build.**  
YouTube renamed its *repetitious content* policy to *inauthentic content* in July 2025, published clarified guidance on 16 July 2026, and enforcement has been real: in January 2026 YouTube terminated 16 channels with a combined 35 million subscribers and 4.7 billion lifetime views under that policy. The removed channels shared a fingerprint: synthetic narration, templated thumbnails, stock-footage loops, and an upload pace no human editorial process could sustain. Separately, AI-generated content now requires disclosure via a toggle at upload, and YouTube has stated disclosure does not limit a video's reach or monetization eligibility on its own.

The architectural consequence is concrete, not moral: **build a mandatory human review gate between `QC_PASSED` and `PUBLISHED`, make the system produce *variety* rather than a template, and never build an auto-upload loop.** I've put the gate in the state machine as a first-class state. A pipeline that produces one well-researched video per day that you personally edit before publishing is a defensible asset. A pipeline that produces ten identical ones is a liability with a 4-week fuse.

---

## 1. System architecture

### 1.1 The single most important design decision

**This is not a multi-agent system in the AutoGen/CrewAI sense. It is a durable, resumable DAG of ~23 steps, where some steps happen to contain an LLM call.**

Autonomous agent loops (agents deciding what to do next, agents talking to each other in natural language) are the wrong tool here for four reasons:

1. **Cost.** Free LLM tiers are request-capped (Groq at 30 requests a minute and 1,000 a day). A chatty agent loop burns your daily budget on one video.
2. **Resumability.** You cannot resume a conversation loop from step 11. You can resume a DAG.
3. **Determinism.** 70% of this pipeline (alignment, encoding, subtitles, muxing, license checks) has exactly one correct answer. Wrapping FFmpeg in an "agent" adds failure modes and removes none.
4. **Debuggability by one person.** A DAG with typed JSON contracts can be inspected. An agent conversation cannot.

So: **the orchestrator is a state machine, not an LLM.** Agents are stateless functions: `(typed JSON input, artifact refs) → (typed JSON output, artifact refs)`. They never call each other. They only read and write the shared store. That's the blackboard pattern, and it's what makes this buildable by one person.

### 1.2 Planes

```text
┌─────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE          (laptop, always-on-ish, ~200 MB RAM)         │
│  • orchestrator: state machine + scheduler loop                     │
│  • job queue (SQLite → Postgres later)                              │
│  • blackboard DB (projects, steps, claims, scenes, assets, ledger)  │
│  • LLM router (LiteLLM proxy) — the anti-lock-in chokepoint         │
│  • budget/rate-limit governor (token buckets per provider)          │
│  • local web UI (FastAPI + htmx) for the human gate                 │
└─────────────────────────────────────────────────────────────────────┘
        │  leases jobs by capability tag
        ▼
┌──────────────────┐ ┌──────────────────┐ ┌───────────────────────────┐
│ CPU WORKER       │ │ NET WORKER       │ │ GPU BURST WORKER          │
│ (laptop)         │ │ (laptop or       │ │ (Kaggle / Colab notebook) │
│  • FFmpeg render │ │  HF Space)       │ │  PULL-based, ephemeral    │
│  • Kokoro/Piper  │ │  • research APIs │ │  • FLUX.1-schnell / SDXL  │
│  • align local   │ │  • stock search  │ │  • optional Chatterbox    │
│  • embeddings    │ │  • LLM calls     │ │  • D8 whisper (align+WER) │
│  • NLI gate      │ │  • downloads     │ │  • batch-only, never 1×   │
│  • thumbnail     │ │                  │ │                           │
└──────────────────┘ └──────────────────┘ └───────────────────────────┘
        │                    │                        │
        ▼                    ▼                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│ ARTIFACT PLANE — content-addressed blob store                       │
│  • local:  ./store/blobs/<sha256[0:2]>/<sha256>                     │
│  • bus:    private Hugging Face dataset repo (card-free, git-lfs)   │
│  • DB stores only: sha256, mime, bytes, role, provenance            │
└─────────────────────────────────────────────────────────────────────┘
```

**Remote-eligible deterministic workers (Kaggle/Colab, same HF-bus pattern):** D7 TTS (primary, Kaggle), **D8 aligner** (Kaggle primary: alignment + WER), **D3 embeddings** and **D4 NLI** (optional, to stay under the 7.0 GB RAM ceiling). D1/D2/D5/D6 *may* run remotely but default local. **D9/D10/D11 are local-only** (SQLite blackboard, Intel QSV, final artifacts).

### 1.3 The GPU burst worker — the one genuinely tricky piece

Kaggle and Colab notebooks **cannot accept inbound connections**. You cannot "call" them. So the GPU worker must *pull*.

Kaggle publishes a visible weekly quota of ~30 GPU-hours on a P100 or two T4s, with no credit card required. Kaggle ships with torch, diffusers and ComfyUI preloaded, removing 10 minutes of pip install per session.

**Protocol:**
1. Laptop enqueues gpu jobs → `status=queued`, `capability='gpu'`
2. Laptop writes a manifest to the HF bus repo: `/inbox/<batch_id>.json`
3. Kaggle notebook loops `hf_hub_download`, claims `GET {bus}/inbox` (15 min lease, 60s heartbeats).
4. Render → upload blob to HF repo `/outbox/<sha256>`
5. Write `/outbox/<job_id>.result.json`
6. Laptop reaper polls `/outbox`, ingests blobs, completes steps.

**Rules:** Never boot GPU for 1 image (batch 3-10 videos). Assume loss - degrade to stock+Manim+typographic card via config flag if Kaggle terminates your session.

### 1.4 The LLM router — your anti-lock-in insurance

All LLM calls funnel through the LiteLLM SDK on the laptop. Never use a vendor SDK directly.

```yaml
tiers:
  cheap:    [groq/llama-3.3-70b, cerebras/qwen3-32b, gemini/flash-lite, local/qwen3-4b-gguf]
  writer:   [gemini/flash, groq/gpt-oss-120b, mistral/free]
  verifier: [gemini/flash, groq/gpt-oss-120b]   # MUST differ from writer
```

Provider death is a 4-line YAML edit. Also, verifier decorrelation is enforced here (tier-level `forbidden_models` in `registry/providers.yaml`).

---

## 2. Agent architecture — what to actually build

### 2.1 Final agent roster (9 LLM agents, 11 deterministic workers)

**LLM agents** (Prompt + JSON schema + model tier):
| # | Agent | In | Out | Tier | Runs |
|---|---|---|---|---|---|
| A1 | **Research Planner** | topic | `{sub_questions[], search_queries[], source_types[]}` | cheap | net |
| A2 | **Evidence Extractor** | doc chunks | `{facts[]: {text, source_id, span, conf}}` | cheap | net |
| A3 | **Outline & Narrative** | pack + audience | `{hook, thesis, beats[], open_loops[], wpm}` | writer | net |
| A4 | **Script Writer** | outline + pack | `{sentences[]: {text, claim_ids[], emphasis}}` | writer | net |
| A5 | **Script Critic** | script | `{retention_risks[], rewrite_notes[], score}` | writer (≠ A4) | net |
| A6 | **Fact Checker** | claim + passages | `{verdict: supported/partial/unsupported, span, note}`| verifier (≠ A4) | net |
| A7 | **Scene Director** | script sentences | `{scenes[]: {sentence_ids[], intent, stock_queries[], gen_prompt, motion}}` | writer | net |
| A8 | **SEO & Thumbnail** | script + scenes | `{title_variants[], description, chapters[], tags[], thumb:{...}}` | cheap | net |
| A9 | **QC Reviewer** | auto-QC + diff + frames | `{blockers[], warnings[]}` | writer | net |

**Deterministic workers** (No LLMs, fully testable):
| # | Worker | Job |
|---|---|---|
| D1 | Source Fetcher | HTTP GET + trafilatura extraction, deduplicated |
| D2 | Source Scorer | Domain tier, retraction check (Crossref), scoring. HALT gate |
| D3 | Claim Indexer | Chunk + embed evidence (bge-small ONNX) |
| D4 | NLI Gate | Cross-encoder entailment (deberta v3) — cheap pre-filter |
| D5 | Stock Searcher | Parallel queries to stock APIs, normalizes schema + licenses |
| D6 | License Validator| Hard gate. Unknown license = blocked |
| D7 | TTS Renderer | Kokoro/Piper, **one WAV per sentence**, exact duration |
| D8 | Aligner | Word timings + WER pass — Kaggle batch primary, local fallback |
| D9 | EDL Compiler | scenes + measured durations → validated EDL JSON |
| D10 | Video Renderer | EDL → segments → concat → mux → loudnorm → final |
| D11 | Auto-QC | 20 deterministic assertions |

---

## 3. Exact workflow: topic → final.mp4

`▸` marks hard gates that halt the DAG until human intervention.

```text
S00  INTAKE            topic → project row, slug, config snapshot
S01  RESEARCH_PLAN     A1 → sub_questions[], search_queries[]
S02  SOURCE_HARVEST    D1 → fetch docs, cache by url_sha
S03  SOURCE_SCORE    ▸ D2 → filter. HALT if <5 tier-A sources
S04  EVIDENCE_INDEX    D3+A2 → extract atomic facts with char spans
S05  OUTLINE           A3 → hook + beats + target duration
S06  DRAFT             A4 → sentences[]. EVERY sentence carries claim_ids or is_rhetorical=true
S07  CRITIQUE          A5 → retention pass; A4 revises once. Hard stop.
S08  FACT_CHECK      ▸ D4+A6 → per-claim verdict. HALT if any unsupported claim survives
S09  SCENE_PLAN        A7 → scenes, intent + queries + gen_prompt
S10  TTS               D7 → N sentence WAVs + measured durations  ◀── TIMING TRUTH
S17  CAPTIONS          D8 → SRT + VTT (arithmetic from the S10 master clock)
S11  VISUAL_SOURCE     D5 → stock-first search
S12  LICENSE_GATE    ▸ D6 → every asset must resolve to a known permissive license
S13  IMAGE_GEN         GPU → for scenes with no stock asset (skippable)
S14  AUDIO_BED         D5 → music bed + ducking
S15  EDL_COMPILE       D9 → durations from S10 drive every scene length
S16  RENDER            D10 → segments → concat → mux → loudnorm → final.mp4
S18  PACKAGING         A8 → title/desc/chapters; HTML thumbnail render
S19  AUTO_QC         ▸ D11 → 20 assertions
S20  AI_QC             A9 → subjective pacing/tone review
S21  HUMAN_GATE      ▸ APPROVE / REQUEST CHANGES / REJECT  ◀── MANDATORY
S22  DELIVER          → outputs/<slug>/ final.mp4 + assets + CREDITS.md + disclosure
```

**S10 sits before S15 on purpose.** Audio is generated before the timeline is compiled because the audio *is* the timeline. This decision eliminates the entire class of A/V sync bugs.

**S17 sits immediately after S10 for the same reason.** Captions are pure arithmetic over `sentences.duration_ms` and the D8 alignment/WER batch compares against `master_audio.wav` — neither needs rendered video. Generating captions here keeps the audio-first invariant intact and makes them available to S16 if burned-in subtitles are ever enabled.

---

## 4. Tool recommendations with dependency classification

A = Open Source/Self-hostable, B = Free stable API, C = Temporary free tier, D = Paid, W = Weights with restriction

- **Research:** OpenAlex (B), Europe PMC (B), PubMed (B), Crossref (B). Lowest lock-in risk.
- **LLM Inferencing:** Groq (C), Cerebras (C), Gemini Flash (C). Llama.cpp Qwen3-4B (A) as local floor.
- **TTS:** Kokoro-82M (Apache 2.0) via **swappable backend interface**. Primary: Kaggle batch CPU notebook (~1–3× realtime on P100/T4). Fallbacks: local `kokoro-onnx` (Apache 2.0) on CPU → Piper (MIT) as final floor. Backend interface: `synthesize(scenes: list[dict], out_dir: str) -> list[str]`. Hash caching uses `md5(text + voice)[:10]` for local filename decisions. *Chatterbox (MIT) needs GPU.*
- **Image Gen:** FLUX.1-schnell (A - Apache 2.0) on Kaggle. *[dev] is Non-Commercial - avoid!*
- **Stock Media:** Wikimedia Commons (B), Openverse (B), Smithsonian (B) - Parse CC-BY/CC0 per file. Manim (A) for diagrams.
- **Audio Bed:** Musopen (B) public domain. Freesound (B) filtered for non-NC.
- **Assembly:** FFmpeg (A). Playwright (A).

---

## 5. Local vs. remote — the actual split

100% of orchestration, db, queue, fetching, scoring, embeddings (bge-small), NLI (deberta), stock search, Manim rendering, FFmpeg encoding, and Auto-QC happen **locally** on your i5-6300U. TTS runs Kaggle-primary with local Kokoro/Piper fallback; D8 (forced alignment + WER) runs as a Kaggle batch with local faster-whisper fallback.

Only LLM logic (LiteLLM → APIs) and AI image generation (Kaggle) happen remotely.

Encoding bottleneck trick: Use Intel QSV `h264_qsv` encoding on your HD 520 (3–5x faster than x264 software encoding). Ken Burns effects should use `crop` linear expressions, not the expensive `zoompan` filter.

---

## 6. Database schema Overview

8 logical groups (SQLite WAL → Postgres later).

1. `projects`: One row per video. Frozen config.
2. `steps`: `UNIQUE(project_id, idem_key)`. **This constraint IS the resumability mechanism.**
3. `jobs`: SQLite queue (`BEGIN IMMEDIATE`), routed by `capability`.
4. `artifacts`: CAS blob store (`sha256` keys to `store/blobs/`).
5. `sources` & `evidence`: Research indexing.
6. `claims` & `claim_evidence`: Factual assertions and verdicts.
7. `sentences` & `scenes`: Script and timeline. `sentences.duration_ms` is the master clock.
8. `media_assets`: License ledger. `gate_status` defaults to `pending`, walling off FFmpeg until `approved`.
9. `usage_ledger`, `events`, `cache`.

---

## 7. Queue & failure architecture

**Error Taxonomy:**
- `TRANSIENT` (5xx, timeouts) -> Retry 3x with jitter.
- `RATE_LIMIT` (429) -> Failover in tier chain. Do not retry same provider. 
- `SCHEMA` (validation failure) -> 1 LLM repair attempt with error feedback, then failover.
- `CONTENT` (fact check failed, blocked license) -> Route to `BLOCKED_*` state (human queue).
- `RESOURCE` (OOM, GPU died) -> Halve batch size, retry once, requeue.
- `FATAL` -> Fail immediately.

**Degradation Ladder Example (Images):**
`Stock (Wikimedia) -> Stock (Pexels) -> Manim diagram -> FLUX.1 (GPU) -> Gemini image (Google AI Studio) -> Neighbor asset reuse -> Typographic Card (cannot fail)`

---

## 8. Preventing Hallucinated Facts

1. **Evidence-first:** Writer never sees raw topic, only numbered chunk pack (Summarization instead of generation).
2. **Schema-enforced Grounding:** Pydantic sentence schema rejects any factual statement lacking `claim_ids`.
3. **Cheap NLI Gate:** Local DeBERTa scores >0.9 auto-approved, <0.1 auto-failed. Only the middle goes to LLM checker (saves 4-5x requests).
4. **Decorrelated Verification:** Fact-checker MUST use a different LLM model family than the writer.
5. **Exact Match:** Dates/numbers/entities extracted and string-matched against source. Unmatched superlatives flagged.
6. **Hedging permitted:** "Knowledge gaps" are allowed output.

---

## 9. Narration / Video Synchronization

```text
S10: wav = tts(sentence) -> duration_ms = exact_samples(wav) / sample_rate * 1000
S15: start_frame(n) = round(start_ms(n) * fps / 1000); frames(n) = start_frame(n+1) - start_frame(n); sum(frames) == audio_frames
S16: sequence scenes
```
Video conforms to audio. Never the reverse.
FFmpeg Rules: Constant Frame Rate (`-r 30 -vsync cfr`), identical encode parameters, `concat` stream copy, single mux pass at the very end.

Subtitles are **arithmetic**, generated directly from `sentences.duration_ms`. Only word-level karaoke and the WER QC pass require faster-whisper — both run as a single Kaggle batch (D8) with local faster-whisper fallback.

---

## 10. Licensing & Compliance (The Non-Negotiables)

1. **Unknown License = Blocked.** The renderer joins against `gate_status='approved'`.
2. **Snapshot the terms.** Save license HTML as an artifact when downloaded.
3. **Attribution is generated.** `CREDITS.md` builds from the DB for all CC-BY.
4. **Blocklists are code.** `FLUX.1 [dev], XTTS v2, F5-TTS, MuseTalk (Commercial weights)` are hard-coded in the `commercial_ok = False` model blocklist.

---

## 11. MVP Build Order (Phase 0)

1. SQLite Schema (`projects`, `steps`, `jobs`).
2. Orchestrator Loop + Local Worker.
3. LiteLLM Proxy setup.
4. **`--dry-run` stub LLM execution mode** (Test S00-S22 in under 30s with zero API calls).
5. CLI (`run`, `resume`, `rerun --from`).

*The key to finishing the remaining phases quickly is that `--dry-run` makes debugging instantaneous.*

---

## Appendix A: EDL Format

```json
{
  "version": 1,
  "output": {"w":1920,"h":1080,"fps":30,"pix_fmt":"yuv420p"},
  "audio":  {"master_sha":"sha256:…","lufs":-14.0,"duration_ms":487340},
  "scenes": [
    {
      "ord": 23,
      "start_ms": 142100, "end_ms": 148900, "frames": 204,
      "asset": {"sha":"sha256:…","kind":"image","license_id":"CC0-1.0"},
      "motion": {"type":"kenburns","from":[0,0,1.00],"to":[40,25,1.12],"ease":"linear"},
      "overlay": {"type":"lower_third","text":"Hippocampus","in_ms":300,"out_ms":2200},
      "transition_in": {"type":"fade","ms":250}
    }
  ],
  "captions": {"srt_sha":"sha256:…","burn_in": false}
}
```

## Appendix B: Repository Layout

```text
ytfactory/
  orchestrator/   # state_machine.py, scheduler.py, reaper.py, idem.py
  agents/         # a01_... to a09_...
  workers/        # fetch.py, tts.py, render.py, edl.py (deterministic)
  prompts/        # versioned markdown texts
  schemas/        # Pydantic (where policy lives)
  registry/       # YAMLs for providers, models, licenses
  gpu/            # kaggle notebook
  store/          # blobs/ab/sha256
  outputs/
  ui/
```
