# YTFactory — Implementation Phases & Milestone Roadmap (`phases.md`)

**Version:** 1.3  
**Status:** Active Execution Plan — Phases 0–6 complete; Phase 7 pending  
**Target Hardware:** Intel Core i5-6300U / 8 GB RAM / Intel HD 520 / Windows 11 ($0.00 Cost, No Credit Card)

---

## Executive Overview

The YTFactory build is structured into 8 sequential phases (Phase 0 through Phase 7). Each phase delivers a self-contained, verifiable capability. The core philosophy is **dry-run engine first** so that subsequent pipeline steps can be tested deterministically in seconds.

**Progress (2026-10-09):** Phase 0, Phase 1 (S00–S08), Phase 2 (S09, S10, S17), Phase 3 (A7/S09 upgrade, S11–S16), Phase 4 (S18, S19, S21, S22), Phase 5 (S20 + A9) and Phase 6 (S13 GPU bus, visual library, S14 music bed) are implemented and verified — **218 unit tests** pass (incl. a real ffmpeg render smoke); zero-API `--dry-run` S00–S22 completes in ~3 s; a live end-to-end delivered a complete package in `outputs/<slug>/`. Phase 6 live loop: `library --seed 200` (FR-V-09) → local GPU bus simulator (`python -m ytfactory.workers.gpubus --watch`) → `rerun --from S11 --live` → S13 **gpu_generated 7/7** → S14 **duck 9.4 dB**, master_mix −14.0 LUFS → S16 mix mux (verified by sample diff) → `AWAITING_HUMAN` → `approve` → `resume --live` → **`DELIVERED`**. 2026-10-09 additions: **YouTube Audio Library music source** (keyless catalog → Drive download, live-verified through S14 to `DELIVERED`), **offline `visual_v1` embeddings** (CLIP-model download blocked → deterministic 136-d visual vectors + centroid re-rank; library now 200/200 embedded), CLI `--set KEY=VALUE` config overrides, and the step-by-step real-GPU guide **`docs/kaggle-flux.md`**. **Real Kaggle + FLUX verified live (2026-10-09, later that day):** config-dataset bus (`zain234/ytfactory-gpu-bus-config` → `Huzaif2/ytfactory-gpu-bus-v2`), GGUF `flux1-schnell-Q5_K_S` on 2×T4 (`gguf≥0.10` upgrade + `sys.modules` purge, RAM guard skips the 34 GB full load), per-attempt job-id nonce vs stale outbox, `reap` retry vs transient HF outages — slug `elon-musk-...-93ac4b7a` **7/7 `gpu_generated`, `library_reused 0`, `apache-2.0`**, S13 ok → `approve` → `resume --live` → **`DELIVERED`** (kaggle-flux.md has the full runbook + failure table).

---

## Phase 0: Architecture Skeleton & Dry-Run Engine

**Goal:** Establish database schema, state machine DAG engine, idempotency generator, and zero-API `--dry-run` mode.

- [x] **0.1 SQLite WAL Schema Initialization:**
  - Create `ytfactory.db` schema: `projects`, `steps`, `jobs`, `artifacts`, `sources`, `evidence`, `claims`, `sentences`, `scenes`, `media_assets`, `usage_ledger`, `events`, `cache`.
  - Enable `PRAGMA journal_mode=WAL;` and `PRAGMA foreign_keys=ON;`.
  - *Done:* `ytfactory/schemas/schema.sql` + `ytfactory/db.py` (blackboard CRUD, BEGIN IMMEDIATE lease queue; also adds `project_artifacts` and `claim_evidence` link tables).
- [x] **0.2 State Machine Orchestrator & Idempotency Generator:**
  - Implement `orchestrator/state_machine.py` to drive steps S00 through S22.
  - Implement `idem.py` calculating `idem_key = sha256(step_name | canonical_json(inputs) | prompt_version | model_tier | code_version)`.
  - Enforce `UNIQUE(project_id, idem_key)` in the `steps` table for crash-safe resumability.
  - *Done:* ordered S00–S22 registry with cached/stale detection, gate handling, and `rerun --from` invalidation; idem keys hash the configured tier (failover-safe).
- [x] **0.3 LiteLLM Proxy Router Setup:**
  - Configure `registry/providers.yaml` mapping cheap, writer, and verifier model tiers over Groq, Cerebras, Google AI Studio, and Mistral free APIs.
  - Enforce model family decorrelation rules (tier-level `forbidden_models` in `registry/providers.yaml`).
  - *Done:* `registry/providers.yaml` (tiers, decorrelation, 60-min demotion after 3 errors/10 min, per-project call budget) + `orchestrator/router.py` (single LLM chokepoint, one JSON-repair attempt). Decorrelation is realized as `decorrelation.verifier_excludes_writer_family` in the YAML plus runtime `forbid_family` checks driven by the model A4 actually used (recorded in `usage_ledger`) — a stronger guarantee than a static family list, because it holds across provider failover. LiteLLM is imported lazily — `pip install litellm` is required only for `--live` runs.
- [x] **0.4 `--dry-run` CLI Engine:**
  - Implement `cli.py` with `run`, `resume`, and `rerun --from <step>` commands.
  - `--dry-run` mode executes S00–S22 with stubbed data in $<30$ seconds without external network/API calls.
  - *Done:* plus `approve` and `list` commands; dry-run validates every agent stub against its Pydantic schema (policy firewall exercised with zero API calls). Verified at ~2.4 s per fresh run.

---

## Phase 1: Research, Ingestion & Script Drafting (S00–S08)

**Goal:** Fetch academic/web sources, build evidence vector index, draft grounded scripts with claim linkages.

- [x] **1.1 Research Ingestion & Source Scoring (S01–S03):**
  - Implement D1 Source Fetcher (`httpx` + `trafilatura` article extraction, URL SHA-256 caching).
  - Implement D2 Source Scorer (query OpenAlex, Europe PMC, PubMed, Wikipedia REST APIs; Crossref retraction checks).
  - Implement `BLOCKED_SOURCES` gate (halts pipeline if $<5$ Tier-1/Tier-A sources survive).
  - *Done:* `workers/fetch.py` (OpenAlex + Wikipedia search, trafilatura extraction, Crossref retraction lookup) and `workers/score.py` (domain tiers, `MIN_TIER_A = 5`). *Gap:* Europe PMC / PubMed REST query helpers not yet added.
- [x] **1.2 Evidence Indexing & Vector Search (S04):**
  - Implement D3 Embeddings Indexer (`fastembed` `bge-small-en-v1.5` ONNX int8, ~400 MB RAM).
  - Extract atomic fact chunks with character offset spans into `evidence` table.
  - Optional: run embedding batches on Kaggle/Colab CPU via the HF bus to keep steady-state RAM under 7.0 GB.
  - *Done:* `workers/embed.py` — sentence-boundary chunking with char spans; `fastembed` is lazy and the indexer degrades to lexical-only (NULL `embedding_blob`) when it is absent. *Optional Kaggle batch: not started.*
- [x] **1.3 Script Generation & Grounding (S05–S07):**
  - Implement A1 Research Planner, A2 Evidence Extractor, A3 Outline Generator, A4 Script Writer.
  - Enforce Pydantic schema: Every non-rhetorical sentence in `sentences` **must** link to $\ge 1$ `claim_id`.
  - Implement A5 Script Critic for adversarial retention pass (using model family different from A4).
  - *Done:* `agents/a01–a05` + prompts; grounding enforced by `@model_validator(mode="after")` in `schemas/script.py` plus a claim-map membership check against real `claims` rows; S07 runs A5 then exactly one A4 revision (hard stop). A5 forbids the family A4 used.
- [x] **1.4 Fact Verification Gate (S08):**
  - Implement D4 NLI Gate (`deberta-v3-small-mnli` ONNX int8, ~300 MB RAM): auto-approve entailment >0.9, auto-reject <0.1, route the 0.1–0.9 middle band to the LLM checker.
  - Implement A6 Fact Checker (model family decorrelated from A4) with one targeted rewrite for `partial`/`unsupported` claims; halt with `BLOCKED_FACTS` if any claim remains `unsupported`.
  - Optional: run the NLI batch remotely (Kaggle/Colab) via the HF bus under local RAM pressure.
  - *Done:* `workers/nli.py` (lazy CrossEncoder; unavailable → all claims route to A6) + `agents/a06` (verifier tier); one targeted rewrite, then `BLOCKED_FACTS` with verdicts persisted before halting. Both gates are drilled in `tests/test_pipeline_dry.py`. *Optional remote NLI: not started.*

---

## Phase 2: Audio Synthesis & Master Clock Timeline (S10, S17)

**Goal:** Implement swappable TTS backends, establish master clock, and build arithmetic subtitles.

*Scope note (2026-10-05): S17 was moved to immediately follow S10 in the DAG (audio-first — captions are arithmetic over the master clock and never need rendered video; diagrams patched in architecture/design/prd). A minimal deterministic S09 scene grouper ships in this phase so the live pipeline can reach S10; Phase 3 upgrades S09 with A7 visual intent.*

- [x] **2.0 Minimal Scene Grouping (S09):**
  - Deterministic sentence→scene grouper (config `sentences_per_scene`) writing `scenes` rows; `visual_intent`/`gen_prompt` stay NULL until A7 (Phase 3) fills them.
  - *Done:* `steps/audio.py: run_s09` — pure DB work, also runs in dry-run; covered by `tests/test_phase2.py` (coverage = every sentence in exactly one scene).
- [x] **2.1 Swappable TTS Backend Architecture (S10):**
  - Define unified backend interface: `synthesize(scenes: list[dict], out_dir: str) -> list[str]`.
  - Build primary Kaggle batch backend (`backends/tts_kaggle_batch.py`): Kokoro-82M, `scene_<id>_<md5(text+voice)[:10]>.wav` local hash caching, single-job batching, dataset versioning, polling with timeout & error log extraction.
  - Build local Kokoro backend (`backends/tts_local_kokoro.py`, `kokoro-onnx` CPU): same interface + same hash cache, invoked when Kaggle is unavailable.
  - Build final-floor backend (`backends/tts_local.py`): Piper TTS CPU engine.
  - *Done:* `backends/{base,tts_kaggle_batch,tts_local_kokoro,tts_local_piper}.py`; chain order kaggle_batch→local_kokoro→local_piper, `model_slot` semaphore (NFR-M-02), sentence cache `sent_<ord>_<md5(text+voice)[:10]>.wav`. The primary rung integrates the existing ready-to-go Kaggle project (kernel `zain234/kokoro-tts-job` + dataset `zain234/kagglw`, code in `kaggle/kernel/`): stage `scenes.json` → `datasets version` + round-trip verify → `kernels push` (triggers run) → poll status → `kernels output` → cache copy; on any error it raises `BackendError` and S10 falls through to local. Local rungs both verified (Kokoro 326 MB / Piper voice auto-download); failover covered by `BrokenBackend` test. Pin with `--tts-backend` (CLI) or `tts_backend` (config).
- [x] **2.2 Master Clock Audio Assembly (S10):**
  - Measure sentence WAV durations: `duration_ms = exact_samples / sample_rate * 1000`.
  - Concatenate sentence WAVs with silence padding at sample level via `soundfile` + `numpy`.
  - Apply EBU R128 loudness normalization via `pyloudnorm`: target **−14 LUFS integrated, −1.5 dBTP true peak**.
  - *Done:* `workers/audio.py` (`sentence_timeline`, `normalize_ebu` with look-ahead true-peak limiter — plain linear gain collapsed loudness to −22.5 on real 21 dB-crest speech, fixed + regression test); live run measured **−14.22 LUFS / −1.55 dBTP** over a 53.4 s master; `sentences.start_ms/duration_ms/audio_sha256` + `scenes.start/end` persisted.
- [x] **2.3 Arithmetic Subtitle Generator (S17):**
  - Generate `.srt` and `.vtt` caption files arithmetically from `sentences.duration_ms` (zero ASR timing drift).
  - Split sentences exceeding 84 chars into ≤42-char × 2-line cues with word-boundary, character-proportional timing; assert Σ cue durations == master audio duration ±80 ms.
  - Submit one D8 Kaggle batch after narration assembly: faster-whisper alignment (if karaoke enabled) + WER diff against `master_audio.wav`; persist `wer_score` for QC assertion 14. Local `faster-whisper` is fallback only.
  - *Done:* `workers/captions.py` (cues chain across inter-sentence gaps so Σ == master by construction; live run: 14 cues, drift 0 ms, no line-length breach) + `workers/align.py` (D8 stub wired; `karaoke` off by default → `skipped`, no network) + `workers/hfbus.py` transport.

---

## Phase 3: Visual Sourcing, Typographic Fallback & Rendering (S09, S11–S16)

**Goal:** Direct visual scenes, search stock APIs, enforce asset licensing, compile EDL, and render CFR video segments.

- [x] **3.1 Visual Planning & Stock Sourcing (S09, S11):**
  - Implement A7 Scene Director (visual intent, generation prompts, camera motion parameters).
  - Implement D5 Stock Searcher (parallel queries to Wikimedia Commons, Openverse, Smithsonian, NASA, Met Museum, Pexels, Pixabay).
  - *Done:* A7 ships as `agents/a07_scene_director.py` + `prompts/a07_scene_director.md` + `schemas/scene_plan.py`; S09 moved to `steps/visual.py` — deterministic planner whenever no LLM key is present (live run used `planner: deterministic`), LLM path when a key exists. D5 is a pluggable `SOURCE_REGISTRY` with the keyless trio (Wikimedia Commons, Openverse, Met Museum) live; Pexels/Pixabay adapters await free API keys. S11 downloaded and linked a CC-BY-2.0 image for 3/3 scenes in the live run.
- [x] **3.2 Asset License Wall (S12):**
  - Implement D6 License Validator: verify `commercial_ok = true`, snapshot license terms HTML into CAS storage.
  - Default `gate_status = pending`; only `approved` assets pass to renderer.
  - *Done:* `workers/license_wall.py` (ordered license rules, unknown/NC/ND → blocked; CAS snapshot `store/cas/<sha[:2]>/<sha>.html`, unreachable → evidence missing but never a gate failure); S12 approves/blocks assets + scenes and never halts the pipeline — the typographic ladder (S13) covers any scene left without an approved asset (R-03).
- [x] **3.3 Rung-7 Typographic Card Fallback:**
  - Implement Pillow (PIL) typographic card generator (brand layout, headline, contrast colors) — guaranteed non-failing visual fallback.
  - *Done:* `workers/typographic.py` renders deterministic-theme 1920×1080 PNG cards (`outputs/<slug>/cards/scene_<ord>.png`, registered as `internal_typographic`/approved); S13 renders cards for uncovered scenes only (live run needed 0 — stock covered everything).
- [x] **3.4 EDL Compiler & FFmpeg Video Rendering (S15–S16):**
  - Implement D9 EDL Compiler: compute scene boundaries by cumulative rounding (`start_frame = round(start_ms * 30 / 1000)`, `frames = next_start − start`), assert $\sum \text{scene\_frames} = \text{audio\_frames}$ exactly.
  - Implement D10 Video Renderer: render per-scene MP4 segments with Ken Burns motion; cache segments by asset SHA + motion params.
  - Perform single stream-copy concat and audio mux (`-r 30 -vsync cfr`, `-c:v libx264` / `h264_qsv`).
  - *Done:* `steps/visual.py` S15 writes schema-validated `edl.json` (live: frames [737, 768, 98] sum **1603 == audio_frames**); S16 pipes PIL frames (1.25× canvas, zoom/pan crop boxes) into ffmpeg per segment (encoder auto-probe: `h264_qsv` selected), caches segments by `sha256(asset|motion|frames|encoder|geometry)`, stream-copy concat + AAC 192k mux + faststart, ffprobe-verifies geometry ±1 s duration, registers `final.mp4`. Live: 1920×1080@30 h264+aac, 53.445 s == master exactly.

---

## Phase 4: Packaging, Auto-QC & Human Review Gate (S18–S22)

**Goal:** Generate metadata and thumbnails, execute 20 auto-QC assertions, and build the local FastAPI+htmx review UI.

- [x] **4.1 SEO & Thumbnail Generation (S18):**
  - Implement A8 SEO Agent (3 CTR title variants, 5000-char description with chapters, tags, `ai_generated_content: true`).
  - Render 1280×720 px PNG thumbnail via Playwright Chromium from HTML/CSS template.
  - Generate `CREDITS.md` with explicit `CC-BY` attribution strings and `SOURCES.md`.
  - *Done:* `steps/packaging.py run_s18` + `agents/a08_seo_agent.py` (schema firewall) + `workers/thumbnail.py` (Playwright Chromium primary, Pillow fallback — both 1280×720 ≤ 2 MB; user decision) + `workers/credits.py` (CREDITS.md attribution lines with license URLs, SOURCES.md corpus, description summary line). No LLM key → deterministic SEO (user decision): 3 distinct ≤100-char titles, ≤5000-char description with chapters + AI disclosure, ≤15 unique tags, sha256-stable palette.
- [x] **4.2 Deterministic Auto-QC Engine (S19):**
  - Implement D11 Auto-QC executing 20 deterministic assertions (A/V sync $<80\text{ ms}$, loudness −14 LUFS, WER $<5\%$, zero unsupported claims, license compliance, container flags, black frame check, etc.).
  - WER (assertion 14) reads the score stored by the S17 D8 batch — no whisper runs locally inside the QC window.
  - *Done:* `workers/qc.py` — 20 assertions `pass|fail|skip`; `run_s19` writes `qc_report.json` then raises `GateHalt("BLOCKED_QC")` on any fail. Live proof: halted on the old 24.6/25.6 s scenes (assertion 2), passed 19/0/1 after regroup; assertion 14 skips when no WER stored (user decision); blackdetect skips (not false-pass) on unreadable files.
- [x] **4.3 Local FastAPI + htmx Review Dashboard (S21):**
  - Implement web UI at `http://localhost:8000`.
  - Mandatory Human Review Gate (`AWAITING_HUMAN` state): HTML5 video player, storyboard grid, fact linkage inspector, QC checklist.
  - Action endpoints: `POST /projects/{id}/approve`, `POST /projects/{id}/reject`, `POST /projects/{id}/request_changes`.
  - *Done:* `ui/app.py` + `ui/templates/` (`python -m ui.app`) — dashboard, project detail, review gate (video player, storyboard with license badges, QC checklist, claim verdicts), QC, delivery screens; `/outputs` static mount; approve → `state_machine.approve`, request-changes → `invalidate_from` + PAUSED, reject → REJECTED; unknown refs 404 (not 500). Verified live: review page 200, `POST .../approve` → 303 → delivery.
- [x] **4.4 First Complete End-to-End Test Video:**
  - Execute topic end-to-end to deliver final package in `outputs/<slug>/`.
  - Verify the S08 gate halts on a deliberately planted false claim before delivery.
  - *Done:* `elon-musk-the-man-behind-the-billion-dollar-empire-93ac4b7a` ran the full Phase 4 loop live: `rerun --from S18` → `BLOCKED_QC` → `rerun --from S09 --until S16` (duration-aware regroup → 7 scenes 3.3–9.8 s, re-render) → `rerun --from S18` → QC 19/0/1 → `AWAITING_HUMAN` → UI approve → `resume` → `DELIVERED` (exit 0) with all 8 package files + `manifest.json`. S08 false-claim halt already proven by the `BLOCKED_FACTS` drill (2026-10-05).

---

## Phase 5: Subjective Editorial Quality Layer (S20)

**Goal:** Integrate subjective AI QC review (pacing, tone, hook delivery, visual harmony).

- [x] **5.1 AI Subjective QC Review (S20):**
  - Implement A9 QC Reviewer evaluating pacing, tone, hook delivery, and visual harmony.
  - *Done:* `agents/a09_qc_reviewer.py` + `prompts/a09_qc_reviewer.md` + `schemas/qc_review.py` (`QcReviewSchema`: `{blockers, warnings, notes}` with dimensions pacing/tone/hook/visual); `workers/editorial.py` deterministic engine (WPM block <80/>250, warn outside 130–150 per FR-S-07; meta/tone regex; hook start >3000 ms blocks; motion-run/card-ratio/dark-frame warnings; Pillow frame sampling via ffmpeg); `steps/ai_qc.py` runs S20 — LLM verdict via A9 when a key exists, deterministic fallback otherwise (zero-cost precedent), writes `ai_qc_report.json` + artifacts + per-finding events; blockers → `GateHalt("BLOCKED_AI_QC")` per user decision, warnings/notes flow to the review UI ("AI editorial review" section); dry S20 = inert payload + schema firewall only; `future.py` trimmed to manifest writer (all 23 steps have real builders); stub sentence wording fixed (`According to the sources, …`) — gate-exposed meta leak.

---

## Phase 6: Production Hardening & GPU Burst Integration (S13, S14)

**Goal:** Connect Kaggle GPU burst worker for FLUX.1 image generation, build CLIP visual index, and add sidechain audio ducking.

- [x] **6.1 Kaggle GPU Pull-Worker Notebook (S13):**
  - Create Kaggle notebook running FLUX.1-schnell (Apache 2.0) GPU batch inference.
  - Implement HF Dataset repo transport bus (`inbox/<batch_id>.json` -> `outbox/<sha256>`).
  - *Done:* `kaggle/gpu_worker/gpu_worker.py` + `kernel-metadata.json` (standalone pull-worker: HF inbox poll, O_EXCL-style claim + 60 s heartbeat threads, FluxPipeline fp16/bf16, per-job sha256 results, 55-min session cap, inline FR-V-08 blocklist, skips already-rendered batches; `_load_env()` reads `YT_GPU_BUS_REPO`/`HF_TOKEN` from **Kaggle Secrets** — script kernels have no setup cell) + `workers/gpubus.py` (shared bus: `LocalBus` dirs with O_EXCL claims/lease steal, `HfBusTransport`, `enqueue`/`reap` with sha verification + 60 s timeout, `gpu_enabled` auto-activates only when a transport is configured, `run_batch` degrades to `None` + note on any `BusError`, `simulate_worker` deterministic local stand-in, `python -m ytfactory.workers.gpubus --watch`) + S13 ladder rewritten **GPU → library abstract → typographic card** with `_gpu_fill` (job ids `sceneNN-<hash>`, `license_id: internal_synthetic`, media_assets approved). Live proof: 7/7 scenes filled through the simulator bus (events: enqueued → 7 verified → filled). **Step-by-step real-run guide: `docs/kaggle-flux.md`** (HF bus repo → Kaggle secrets → kernel push → GPU session → `rerun --from S13 --live --set image_gen=gpu --set gpu_timeout_s=900` → verify; includes failure-ladder + troubleshooting tables).
- [x] **6.2 Reusable Visual Library:**
  - Build local CLIP embedding index over pre-rendered/stock assets for zero-cost visual reuse.
  - *Done:* `schemas/schema.sql` `vislib_assets` + `workers/vislib.py` (dHash + 8×8×3 color histogram always; CLIP vectors via lazy fastembed gated on local model cache — `YT_CLIP=1`/`YT_EMBED=1` opts into a download so restricted networks never stall; deterministic self-made abstract generator, FR-V-09 seed target 200, lexical tag query with deterministic ordering, per-project repetition cap ≤ 3, stale-row eviction) + S11 strict-match rung **before stock** (skips stock queries for covered scenes, links into media_assets `approved`) + S13 abstract rung + `_ingest_project_assets` feeds approved stock back for the next project + `library --seed N` CLI. **2026-10-09 — offline embedding fallback (the "other way" when CLIP won't download):** `feature_vector()` builds a deterministic **`visual_v1`** 136-d vector (block-normalized dHash signs + color histogram + 4×4 spatial RGB grid; L2-normalized, no model download), stored per row with the new `embedding_model` column (`db.init_schema` additive migration); `embed_asset()` picks CLIP when its model is cached, otherwise `visual_v1`; `query()` re-ranks lexical matches by cosine to the **tag-centroid query vector** in feature space (visual outliers demoted among tag-mates, ties stay sha-deterministic); `backfill_embeddings()` vectors pre-existing rows and runs from the `library` CLI. Live proof: `python -m ytfactory.cli library` → **backfilled 200/200** (`200 embedded via visual_v1`).
- [x] **6.3 Audio Bed & Sidechain Ducking (S14):**
  - Query public domain / CC-BY music (Musopen, Freesound).
  - Apply FFmpeg `sidechaincompress` filter ducking music 6–10 dB during narration speech segments.
  - *Done:* `workers/music.py` — source chain `wikimedia → freesound → youtube → generated` (`music_source`/`music_fallback`/`music_query` config; keyless Commons search license-gated by D6, Freesound key-gated, **YouTube Audio Library** (2026-10-09: keyless catalog mirror `api.json` — 5 142 free tracks, filename-token search with deterministic slice fallback, Google-Drive download, D6 rule `youtube-audio-library` → commercial_ok with library credit in CREDITS), self-made ambient A-minor pad as always-available fallback, `music_source=off` disables) + `duck_bed` (loop/trim, `sidechaincompress` with narration as sidechain, dB-vs-linear threshold probe, **asymmetric estimator** (15 ms attack / 250 ms release guard frames) measuring gap-vs-speech RMS of the ducked bed, auto-tunes threshold over ≤8 rounds until 6–10 dB, keeps best) + `build_mix` → EBU `master_mix.wav`; S14 writes provenance into `media_assets` (flows to CREDITS) + S16 `_resolve_audio_input` muxes the mix when present (narration fallback when S14 skipped/absent). Live proof (2026-10-09): chain wikimedia (10054) → freesound (no key) → **youtube: `12_Days_of_Christmas_Instrumental.mp3`** (real 5.3 MB ID3 download), **duck 9.24 dB**, then approve → resume → **`DELIVERED`** with the YouTube track credited (`youtube-audio-library`). Earlier loop: wikimedia → freesound → generated pad, duck 9.4 dB, mix −14.0 LUFS, final.mp4 audio == master_mix.
- [x] **6.4 Automatic Provider Demotion & Governor:**
  - Implement automatic 60-minute demotion for LLM providers returning 3 consecutive 4xx/5xx errors.
  - *Done:* `orchestrator/router.py` (`_demote`, config-driven via `demotion:` in `registry/providers.yaml`) plus `budget.max_llm_calls_per_project`; built early with the router in Phase 0.

---

## Phase 7: Long-Term Scaling (Post-Launch)

**Goal:** Multi-machine execution, PostgreSQL migration path, and analytics feedback.

- [ ] **7.1 Database Driver Abstraction:**
  - Support seamless migration from SQLite WAL to PostgreSQL (Neon) with `SKIP LOCKED` job queueing.
- [ ] **7.2 Multi-Machine Render Workers:**
  - Allow remote worker nodes to lease CPU/Render jobs via capability tags.
- [ ] **7.3 Analytics Feedback Loop:**
  - Ingest YouTube Studio analytics JSON to calibrate retention hook scores and WPM targets for future scripts.

---

## Verification Log

| Date | Check | Result |
|------|-------|--------|
| 2026-10-05 | `python -m unittest discover -s tests` | 31/31 OK (~2 s) |
| 2026-10-05 | `python -m ytfactory.cli run "<topic>" --dry-run` | S00–S22 → `DELIVERED` in ~2.4 s (budget <30 s), zero API calls |
| 2026-10-05 | Idempotent re-run of a delivered project | all 23 steps `cached`, no duplicated sources/claims/sentences |
| 2026-10-05 | `BLOCKED_SOURCES` drill (Tier-A corpus starved to 2) | halts at S03; `resume` re-returns the gate; `rerun --from S02` recovers to `DELIVERED` |
| 2026-10-05 | `BLOCKED_FACTS` drill (injected unsupported verdicts) | one A4 rewrite, verdicts persisted, halts at S08 |
| 2026-10-05 | `rerun --from S08` / `approve` CLI paths | re-executes S08–S22; `approve` refuses projects not at `AWAITING_HUMAN` |
| 2026-10-05 | `python -m unittest discover -s tests` | 64/64 OK (~7 s) incl. Phase 2 captions/audio/backend/DAG-order suites |
| 2026-10-05 | live `run --until S08` then `run --live --until S17` (real Kokoro) | S09/S10/S17 execute live, S00–S08 all `cached`, zero `usage_ledger` rows |
| 2026-10-05 | master audio QC | `master_audio.wav` 53.4 s @ −14.22 LUFS / −1.55 dBTP (targets −14 / ≤−1.5) |
| 2026-10-05 | captions QC | 14 cues, Σ drift 0 ms (tol ±80), no >42-char lines, SRT+VTT written |
| 2026-10-05 | backend rungs probed | local_kokoro synthesize OK (14 s first call); Piper voice download+synth OK (22.05 kHz → resampled) |
| 2026-10-05 | Kaggle batch E2E (`KaggleBatchBackend.synthesize`, 2 sentences) | dataset publish + kernel run + output fetch round-trip OK in 297 s |
| 2026-10-05 | live S10 pinned `--tts-backend kaggle_batch` (7 sentences) | `backend_used: kaggle_batch`, master 55.8 s @ −14.17 LUFS / −1.57 dBTP; S17 captions ok after |
| 2026-10-06 | `python -m unittest discover -s tests` | 85/85 OK (~16 s) incl. Phase 3 typographic/stock/license/EDL/render suites (real ffmpeg) |
| 2026-10-06 | live `run --dry-run --until S08` then `resume --live --until S16` | S09–S17 and S11–S16 all `ok`; project PAUSED after S16 as instructed |
| 2026-10-06 | TTS rung-chain failover (live) | Kaggle kernel QUEUED 30 min → `tts_poll_timeout_s` exceeded → `BackendError` → auto-fell-through to `local_kokoro`; pipeline never stalled |
| 2026-10-06 | Phase 3 live QC | `final.mp4` 1920×1080@30, h264 (qsv) + aac, 53.445 s == master exactly; EDL frames [737,768,98] sum 1603 == `audio_frames`; S11 3/3 scenes sourced, S12 approved 1 / blocked 0, S13 cards 0 (all covered); master −14.72 LUFS / −2.11 dBTP; captions drift 0 ms |
| 2026-10-06 | `python -m unittest discover -s tests` | 135/135 OK (~27 s) incl. Phase 4 thumbnail (Playwright+Pillow+fallbacks), credits/SEO, 20 QC assertions, S18/S19/S21/S22 gates, FastAPI TestClient suites |
| 2026-10-06 | live `rerun --from S18 --live` (Phase 3 video) | S18 ok (deterministic SEO, Playwright thumbnail); S19 **`BLOCKED_QC`** — assertion 2 scene_duration_bounds: scenes 1–2 at 24.6/25.6 s > 12 s; `qc_report.json` written before halt |
| 2026-10-06 | live `rerun --from S09 --live --until S16` | duration-aware regroup → 7 scenes (3.3–9.8 s, all ≤ 12 s), all approved; S10 segment-cache hits; S16 re-render ok → PAUSED |
| 2026-10-06 | live `rerun --from S18 --live` after regroup | S19 **19 pass / 0 fail / 1 skip** (a14 WER absent → skip+warn per user decision); S20 skip note (Phase 5); S21 → **`AWAITING_HUMAN`** (exit 2) |
| 2026-10-06 | FastAPI review UI live (`python -m ui.app`) | dashboard/review/delivery → 200 with AWAITING_HUMAN gate + QC checklist + storyboard; `POST /projects/{slug}/approve` → **303** → project `APPROVED` |
| 2026-10-06 | `resume --live` after UI approval | S22 → **`DELIVERED`** (exit 0); package complete: final.mp4 7.5 MB, thumbnail 1280×720 42 KB, captions SRT/VTT, metadata.json (3 titles/7 chapters/8 tags + disclosure), CREDITS.md, SOURCES.md, qc_report.json, manifest.json |
| 2026-10-07 | `python -m unittest discover -s tests` | 161/161 OK (~31 s) incl. Phase 5 A9 schema, deterministic-review cases (meta/tone, WPM bands, hook, motion/card/frame stats), ffmpeg frame sampling, S20 dry/live/halt/LLM-fallback, wiring (S20 handler, BUILDERS == STEP_NAMES), review-UI editorial section render |
| 2026-10-07 | live `rerun --from S20 --live` (meta-scripted delivered project) | **`BLOCKED_AI_QC`** exit 2 — 4 tone blockers (all 7 sentences self-referential) + 2 warnings (129.1 WPM, hook 7.3 s); `ai_qc_report.json` written before halt; per-finding `step_note` events recorded |
| 2026-10-07 | recovery: `rerun --from S06 --dry-run --until S08` then `resume --live` | re-draft → clean "According to the sources, …" narration; S09–S19 all ok; S20 **0 blockers / 2 warnings** (127.0 WPM, hook 6176 ms — non-blocking by design); S21 → `AWAITING_HUMAN` |
| 2026-10-07 | `approve` + `resume --live` | S22 → **`DELIVERED`** (exit 0); full Phase 5 loop closed |
| 2026-10-08 | `python -m unittest discover -s tests` | 203/203 OK (~58 s) incl. Phase 6 music (pad/duck/mix/fetch chain), vislib (seed/ingest/query/claim cap), gpubus (model policy, O_EXCL claim + lease steal, enqueue→sim→reap, timeout degrade), S11/S13/S14/S16 wiring, kaggle-worker blocklist parity, `library` CLI |
| 2026-10-08 | `python -m ytfactory.cli library --seed 200` | FR-V-09: **200 assets indexed** (200 generated, 0 CLIP-indexed — fastembed installed, model download blocked by network, loader gated on cache presence) |
| 2026-10-08 | live GPU bus roundtrip (`gpubus --watch` + `rerun --from S11 --live`) | S13 **`gpu_generated 7/7`**: batch `9ffb3e9e3ed4` enqueued → 7 outbox results sha-verified → scenes `internal_synthetic/approved`; cards 0, library rung 0 |
| 2026-10-08 | live S14 music bed | source chain wikimedia (`ConnectError 10054`) → freesound (no key) → **generated pad**; **duck 9.4 dB** (FR-A-07 6–10), `master_mix.wav` **−14.0 LUFS**, provenance row `internal_music_bed/approved`, CREDITS.md line present, events record source + duck + LUFS |
| 2026-10-08 | S16 mix mux verification | final.mp4 audio vs `master_mix.wav` RMS diff **0.0034** vs narration-only 0.0113 → mix used; S16 falls back to narration when S14 skipped/absent (unit-tested) |
| 2026-10-08 | full Phase 6 delivery loop | `rerun --from S11 --live` → S11 stock (wikimedia/Met degraded, keyless downloads still linked 7/7) → S12 0/0 (assets already approved) → S13 GPU → S14 → S15/S16 → S18/S19/S20 ok → `AWAITING_HUMAN` → `approve` → `resume --live` → **`DELIVERED`** (exit 0) |
| 2026-10-09 | `python -m unittest discover -s tests` | **213/213 OK** (~97 s) incl. new YouTube music source (catalog parse/search/deterministic fallback/auto-chain), `visual_v1` feature vectors + centroid re-rank + backfill, Kaggle-secret loading parity, CLI `--set KEY=VALUE` overrides |
| 2026-10-09 | live YouTube Audio Library probe (`store/probe_youtube_music.py`) | real catalog fetch (5 142 tracks, 1.3 MB) → Google-Drive download **5.3 MB MP3** (ID3 magic, sha recorded), license `youtube-audio-library` → D6 `commercial_ok=True` |
| 2026-10-09 | `python -m ytfactory.cli library` | schema migration (`embedding_model` column) + **backfilled 200/200** rows → `200 embedded via visual_v1` (offline encoder; CLIP stays cache-gated) |
| 2026-10-09 | live S14 YouTube-music loop | `rerun --from S14 --live`: wikimedia (`10054`) → freesound (no key) → **`youtube: 12_Days_of_Christmas_Instrumental.mp3`**, **duck 9.24 dB** (FR-A-07) → `approve` → `resume --live` → **`DELIVERED`** (exit 0); CREDITS.md carries the `youtube-audio-library` line with source URL |

**Known gaps carried forward:** ~~real FLUX.1-on-Kaggle inference runs only as a documented manual step~~ — **closed 2026-10-09: live-verified end-to-end on real Kaggle 2×T4** (config-dataset bus → GGUF Q5_K_S render → sha verify → `gpu_generated 7/7` → `DELIVERED`); runbook + failure table: `docs/kaggle-flux.md` (license accept, push=run / `logs -f`, max-2-session rule, bus rotation, per-attempt job nonces); `fastembed` is installed but its CLIP/bge model downloads are blocked in this environment — loaders are gated on local cache presence (`YT_CLIP=1`/`YT_EMBED=1` to opt into a download), so the visual library now embeds everything with the offline **`visual_v1`** encoder (CLIP still preferred automatically once its model is on disk; `stats`/`library` CLI report which encoder produced the vectors) and D3 evidence embedding batches stay lexical; the YouTube Audio Library catalog is an **unofficial static mirror** (filename-only metadata — no per-track license/artist fields), so credits always name the library + track and the rare CC-BY subset of the library cannot be attributed per-creator (Standard YTAL license needs no attribution; swap in the official authenticated catalog later if per-track metadata is required); `sentence_transformers`, `faster-whisper` are not installed (NLI/D8 karaoke default off); stock sources Pexels/Pixabay need free API keys (keyless trio ships; live runs saw 7 query/source errors — keyless CC-BY downloads still covered 7/7 scenes, S13 GPU filled the rest); Wikimedia Commons unreachable live (10054) so the music chain fell through to the YouTube rung (verified) and then the generated pad by design; Smithsonian/NASA query helpers not yet written (registry is pluggable); Europe PMC & PubMed query helpers (1.1); optional Kaggle embedding/NLI batches (1.2, 1.4); D8 alignment WER still assumes an HF dataset bus (`workers/hfbus.py`) that the TTS path no longer uses — S17 stores no `wer_score`, so QC assertion 14 skips until D8 lands (assertion 14 will start failing closed then, per its `<5%` rule); A7/A8/A9 LLM paths written but exercised only via deterministic fallbacks (no LLM key in the verification environment); review-UI sentence-edit / asset-swap `PUT` endpoints and SEO meta-sentence filtering still deferred; S20 keeps S18 SEO by design (SEO runs before S20, so a sloppy script still reaches title variant 2 — S20 now blocks delivery until the script is fixed upstream).
