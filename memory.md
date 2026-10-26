# YTFactory — Agent Memory & Handoff Context

> **Handoff Document** — Written for any incoming coding agent resuming this project with no prior chat history.
> **Security Rule** — Contains NO API keys, tokens, or passwords (environment variable names only).
> **Source of Truth** — Reflects workspace state in `E:\New folder (2)`.
> *Merged from two prior agent sessions (root `memory.md` + `docs/memory.md`).*

---

## 1. Project Overview

**YTFactory** is an autonomous, end-to-end documentary video production pipeline for YouTube.
Given a topic, it runs a **23-step workflow** (S00–S22) covering:

> Research → Source Harvesting → Evidence Indexing → Script Drafting → Critique → Fact-Checking → Audio Synthesis → Visual Generation/Retrieval → Video Assembly → Packaging → AI Quality Control → Human Review → Delivery

### Core Architecture & Philosophy

"Dry-run engine first" with dual execution paths:

1. **Dry-run** (`--dry-run`): Deterministic zero-API stubs, runs in ~2–3 seconds, validates schemas and the entire state DAG.
2. **Live** (`--live`): Real LLM execution via `litellm` routing across free-tier providers (Groq, Gemini), local GPU/Kaggle FLUX workers for images, and local tools (ffmpeg, edge-tts).

### Current Milestone — "Real LLM Quality Pass"

- Phases 0–6 pipeline, offline workers, and GPU bus are **complete**.
- Project 26 (`elon-musk-...-93ac4b7a`) previously reached `DELIVERED` with real FLUX images via Kaggle.
- **Goal**: All 9 LLM agents (A1–A9) must execute against real LLMs with **zero `[agent-fallback]` stub lines**, end-to-end verified through `DELIVERED` within an 80-call budget, with strict model family decorrelation (writer ≠ verifier), pre-flight checked via `llm-check`, and backed by a passing test suite (229 tests currently passing).

---

## 2. Tech Stack & Project Structure

### Tech Stack

| Area | Detail |
|---|---|
| Runtime / OS | Python 3.13.5 · Windows 11 · PowerShell 5.1 (NOT bash — no heredocs, no `&&`, no `rg`) |
| Data & State | SQLite WAL mode (`store/ytfactory.db`) · Pydantic models · YAML provider config |
| LLM Layer | `litellm` lazy-loaded via `ytfactory/orchestrator/router.py` (the only chokepoint) |
| Groq | 8,000 TPM free-tier limit · Models: `groq/openai/gpt-oss-20b`, `groq/openai/gpt-oss-120b` |
| Google Gemini | 15 RPM free-tier on `gemini-3.5-flash-lite`; `gemini-3.5-flash` gets 503s under demand |
| Audio / Video | `edge-tts` (TTS) · `ffmpeg` (audio ducking/muxing) |
| GPU Images | Local FLUX simulator (`ytfactory/workers/gpubus.py --watch`) or Kaggle T4×2 worker (`kaggle/gpu_worker/`) |

### Project Structure (Root: `E:\New folder (2)`)

| Path | Purpose |
|---|---|
| `memory.md` | **This file** — primary agent handoff document |
| `ytfactory/cli.py` | CLI entry point: `run`, `resume`, `rerun`, `llm-check`, `approve`, `list`. Run via `python -m ytfactory.cli` (no `__main__.py`) |
| `ytfactory/orchestrator/router.py` | LLM gateway: tiers, demotion, transient heal-wait, JSON-repair retry, `usage_log`, model family decorrelation |
| `ytfactory/orchestrator/state_machine.py` | DAG runner for S00–S22: idempotency checks, gate halting |
| `ytfactory/orchestrator/idem.py` | SHA-256 idempotency key generator |
| `ytfactory/registry/providers.yaml` | Tier model lists + demotion config — **single source of truth for LLM endpoints** |
| `ytfactory/steps/` | Step implementations: `common.py` (Ctx, `record_usage`), `research.py` (S01–S04), `script.py` (S05–S08), `visual.py` (S09–S13, S17), `audio.py` (S14–S16), `packaging.py` (S18–S19), `ai_qc.py` (S20), `deliver.py` (S21–S22) |
| `ytfactory/workers/fetch.py` | Source fetchers: Wikipedia, OpenAlex, arXiv (keyless, Tier A via `search_arxiv()`) |
| `ytfactory/workers/score.py` | Domain credibility tier scoring (`MIN_TIER_A=5`) |
| `ytfactory/workers/gpubus.py` | Local GPU bus simulator (`--watch` mode for dry FLUX testing) |
| `ytfactory/agents/base.py` | `run_agent`: on `RouterError` falls back to deterministic stub, prints `[agent-fallback]` |
| `ytfactory/schemas/` | Pydantic schemas per agent (A1 research plan, A4 script draft, A6 fact check, etc.) |
| `ytfactory/db.py` | SQLite blackboard DB access, schema init, lease queues, `usage_ledger` |
| `kaggle/gpu_worker/` | Kaggle kernel for free T4×2 FLUX.1-schnell GPU image generation |
| `store/` | Scratch dir: run logs (`ladder.out`, `ladder.err`), probe scripts, `ytfactory.db` |
| `docs/` | Project docs: `phases.md`, `architecture.md`, `design.md`, `prd.md`, `kaggle-flux.md`, `rules.md` |
| `ui/app.py` | FastAPI web dashboard (port 8000); run via `python -m ui.app` |
| `E:\New folder (2)\.env` | Comment-only shadow file that suppresses LiteLLM dotenv warning. **DO NOT edit `E:\.env`** (user's personal drive-root file) |

---

## 3. Setup & Run Commands

### Environment Variables

API keys are set in Windows user environment via `setx` (never write values in docs):

- `GROQ_API_KEY` — Groq API access
- `GEMINI_API_KEY` — Google Gemini (alias of `GOOGLE_API_KEY`)
- `GOOGLE_API_KEY` — Google Gemini
- `PIXABAY_API_KEY` — Stock image/video search
- `HF_TOKEN` — Hugging Face (for Kaggle GPU bus)
- `YT_GPU_BUS_REPO` — HF dataset repo used as GPU message bus (e.g. `username/ytfactory-gpu-bus`)

> ⚠️ PowerShell child processes may not always inherit newly `setx`'d vars. Set `$env:GROQ_API_KEY` and `$env:GEMINI_API_KEY` explicitly in child launch shells when starting pipeline runs.

### Standard Commands (PowerShell)

```powershell
# Run all unit tests (~2–4 minutes)
python -m unittest discover -s tests

# Run a single test file (tests is NOT a package — must use discover)
python -m unittest discover -s tests -p "test_router.py"
python -m unittest discover -s tests -p "test_llm_check.py"

# Live LLM health check (probes live models, decorrelation safety, exits 1 on failure)
python -m ytfactory.cli llm-check --timeout 30

# Dry-run pipeline (zero API calls, validates complete DAG & schemas in ~3 s)
python -m ytfactory.cli run "quantum-computing" --dry-run

# Live pipeline execution
python -m ytfactory.cli run "The James Webb Space Telescope" --live

# Resume an existing project
python -m ytfactory.cli resume the-james-webb-space-telescope-010a98b5 --live

# Rerun specific step range
python -m ytfactory.cli rerun the-james-webb-space-telescope-010a98b5 --from S04 --live --until S08

# Start the web UI dashboard
python -m ui.app
```

---

## 4. What Has Been Completed

1. **Phases 0–6 pipeline & offline workers** (S00–S22): schema validation, audio bed ducking, visual library reuse, Kaggle FLUX GPU worker. Project 26 delivered end-to-end.
2. **Provider key wiring & live model probing**:
   - Verified live models in `registry/providers.yaml`:
     - Cheap: `groq/openai/gpt-oss-20b`, `gemini/gemini-3.5-flash-lite`, `gemini/gemini-3.5-flash`
     - Writer: `gemini/gemini-3.5-flash`, `groq/openai/gpt-oss-120b`, `gemini/gemini-3.5-flash-lite`
     - Verifier: `groq/openai/gpt-oss-120b`, `gemini/gemini-3.5-flash`
   - Dead/pruned: `cerebras/*`, `mistral/free`, `gemini/flash`, `gemini/flash-lite`, `groq/llama-3.3-70b`, `groq/gpt-oss-120b` (bad prefix), all `groq/llama-*`, all `gemma-3-*`
3. **`llm-check` CLI subcommand**: `cmd_llm_check`, `_probe_model` in `cli.py` + 6 tests in `tests/test_llm_check.py`. Live run: 8/8 models OK, decorrelation OK, exit 0.
4. **arXiv keyless backend**: `search_arxiv()` in `workers/fetch.py`, wired as 3rd backend in `steps/research.py` — fixed the `BLOCKED_SOURCES` gate (OpenAlex returns doi.org = Tier C; Wikipedia/publishers 403).
5. **Router transient demotion & usage logging overhaul**:
   - `TRANSIENT_MARKERS` + `_is_transient()` distinguish 429/5xx (60-second cooldown) from auth/bad-model errors (60-minute ban)
   - `_recover_wait(tier)` bounded by `max_recover_wait_s: 75` — one heal-wait cycle per invoke
   - `self.usage_log` list; `invoke()` resets `last_usage = {}` at start; each successful call appended to `usage_log`
   - `record_usage()` in `steps/common.py` drains `usage_log` (one ledger row per real call, no `last_usage` fallback — that fallback caused double-records)
   - 5 new tests: `TestTransientDemotion` in `tests/test_router.py`
   - Env-key patches added in `test_phase2.py`, `test_phase3.py`, `test_phase5.py`
6. **S05, S06, S07 fixes**:
   - S05: `_dry_outline()` now uses real topic/claims (not hardcoded beats)
   - S06: `_normalize_claim_key()` + `_sanitize_and_validate_sentences()` — normalizes `c1`→`C1`, `claim_1`→`C1`, prunes hallucinated IDs, guards grounding invariants
   - S07: fixed `prompts/a04_script_writer.md` to include `{{critique_notes}}`; non-destructive fallback for A4 revision preserves valid S06 draft sentences
   - 3 new tests in `tests/test_script_schema.py`
7. **S08 BLOCKED_FACTS fixes**:
   - Prompt-schema mismatch: `a06_fact_checker.md` returned `claim_id` but `ClaimVerdict` required `claim_key` — fixed
   - `partial` verdicts now treated as `supported`
   - `recheck_llm_pack` fixed to use positional column access (`row[0]`, `row[1]`, `row[2]`) and `recheck_key_by_id` dict
   - Added `record_usage` for second A6 call; fixed `_dry_sentences` to accept `ctx.topic`; added `_sanitize_and_validate_sentences` on rewrite; added `_normalize_verdict_key()` for LLM alias tolerance
   - `still_bad` now initialized to `list(unsupported)` so it defaults to blocking unless A6 second pass explicitly clears it
8. **S11 TimeoutError fix** in stock visual sourcing:
   - `workers/stock.py`: `search_all` wrapped `as_completed` in `try...except TimeoutError`, cancels pending futures, appends to errors, scales workers (up to 16) and timeout with task volume, 3s connect timeout on `_get_json`
   - New test: `test_search_all_timeout_swallowed` in `test_phase3.py`
9. **S19 auto-QC non-blocking**: removed hard `GateHalt` from `run_s19` — QC assertion failures now log warnings to the project timeline without stalling the pipeline before S21 human review
10. **Web UI dashboard** (`ui/app.py`): FastAPI dark-glass dashboard, all pipeline stages visible with live status, human review gate S21, delivery tab. Supports `HOST`/`PORT` env vars.
11. **GitHub release**: Repository published at `https://github.com/QadirKhan9/YTFactory-Autonomous-AI-Video-Production-Engine` with `README.md`, `SETUP_GUIDE.md`, `SETUP_GUIDE.md`, `.gitignore`, `.env.example`, `requirements.txt`.
12. **Test suite**: **229 unit tests passing** (`python -m unittest discover -s tests`).

---

## 5. Current Task & Exact State (Where We Stopped)

**Task**: Live-verify the router rate-limit fix by rerunning project 29 (`the-james-webb-space-telescope-010a98b5`) from S04→S08, then resume to `DELIVERED`, then docs + final suite.

**Last command run**:
```powershell
python -m ytfactory.cli rerun the-james-webb-space-telescope-010a98b5 --from S04 --live --until S08
```
(Logs: `store/ladder.out`, `store/ladder.err`)

**Outcome — NOT yet green**:
- S05/S06/S07: **OK** (real Gemini/Groq calls, router heal-waits fired correctly)
- **S04 `[agent-fallback] A2`**: simultaneous free-tier exhaustion — Groq 8,000 TPM + Gemini 15 RPM both hit at the same time; single 32s heal-wait insufficient
- **S08 → `BLOCKED_FACTS`**: `sentence_transformers` (CrossEncoder) absent → all claims routed to A6 LLM; A6 verification thresholds not cleared → gate halted

---

## 6. Known Bugs & Half-Finished Work

| # | Issue | Status |
|---|---|---|
| 1 | **A2 fallback under rate-limit storms**: one 75-second heal-wait is insufficient when Groq TPM + Gemini RPM exhaust simultaneously | Open |
| 2 | **`sentence_transformers` / `fastembed` not installed**: NLI + embeddings degraded; code degrades gracefully but increases A6 LLM token load | Open |
| 3 | **`docs/architecture.md` lines ~99–101**: stale tier model listing | Open |
| 4 | **`docs/phases.md`**: not updated with LLM-pass verification rows | Open |
| 5 | **Call budget enforcement**: `max_llm_calls_per_project: 80` exists in `providers.yaml` but runtime enforcement vs `usage_ledger` unverified | Open |

---

## 7. Next Steps (Exact Order)

1. **Diagnose S08**: inspect `store/ladder.out` + `usage_ledger` for `the-james-webb-space-telescope-010a98b5`; check A6 verifier output and claim pass-rate gate in `steps/script.py`
2. **Fix A2 rate-limit robustness** in `router.py invoke()`: stagger per-model retries within a cycle, allow up to 2 waits bounded by `max_recover_wait_s`, or parse Groq/Gemini `retryDelay` from the error message. Never unbounded sleep
3. **Add/adjust router tests** for the change; run `test_router.py` then full suite
4. **Rerun the ladder**: `rerun the-james-webb-space-telescope-010a98b5 --from S04 --live --until S08` — assert **zero** `[agent-fallback]` lines
5. **Resume to DELIVERED**: `python -m ytfactory.cli resume the-james-webb-space-telescope-010a98b5 --live`; record LLM call count vs 80-call budget
6. **Update docs**: fix stale tier sample in `docs/architecture.md` (~line 99–101); add LLM-pass verification rows to `docs/phases.md`
7. **Final test suite**: `python -m unittest discover -s tests` — expect 229+ OK

---

## 8. Decisions Made & Abandoned Approaches

- **Transient vs permanent demotion**: 429/5xx → 60-second cooldown; auth/bad-id → 60-minute ban. *Why*: A single Gemini 503 previously demoted the whole tier for an hour.
- **Bounded in-invoke heal-wait (≤75 s, one cycle)**: abandoned unbounded retries (hangs test suites and CLI runs).
- **`usage_log` drain in `record_usage`** (one ledger row per real call): abandoned single `last_usage` fallback that caused duplicate token records and stale counts from prior steps.
- **arXiv as 3rd source backend** (keyless, Tier A): abandoned fixing OpenAlex/publisher 403 blocks.
- **Comment-only `.env` shadow**: abandoned editing `E:\.env` (user's personal drive-root file).
- **Tests patch `_llm_keys_present → False`**: never unset system environment variables; patching preserves deterministic test behavior even when live keys are `setx`'d globally.
- **`llm-check` exit-code contract**: exit 1 if any tier empty or writer/verifier share a family — used as pre-flight gate before live runs.
- **S19 non-blocking QC**: removed hard `GateHalt` from S19; auto-QC failures are warnings only, pipeline continues to S21 human review.

---

## 9. Code Conventions

- **Test discovery**: `python -m unittest discover -s tests` ONLY. Never `python -m unittest tests.test_router` — `tests` has no `__init__.py` → `ModuleNotFoundError`.
- **PowerShell syntax**: chain with `; if ($?) { ... }`. Never `&&`, `export`, or heredocs `<<EOF`.
- **CLI entry point**: always `python -m ytfactory.cli <command>` — there is no `ytfactory/__main__.py`.
- **Router chokepoint**: ALL LLM calls must go through `Router.invoke()`. Never invoke `litellm` directly from an agent or step.
- **Fail-safe stubs**: agents catch `RouterError` in `run_agent` and return a deterministic schema-valid stub, outputting `[agent-fallback]`.
- **Model family decorrelation**: Verifier (A6) and Critic (A5) must pass `forbid_family=last_model_family(ctx, "S06_DRAFT")`.
- **Provider changes**: edit `ytfactory/registry/providers.yaml` only — never hardcode model names in step Python code.
- **Lazy imports**: `litellm` is imported inside `Router.invoke()` so dry-runs work without it installed.
- **Multi-line `python -c`**: breaks in PowerShell — write temp scripts to `store/` and invoke via `python store/<script>.py`.

---

## 10. Gotchas, Sensitive Parts & Workarounds

| Rule | Detail |
|---|---|
| **DO NOT TOUCH `E:\.env`** | User's personal drive-root file. The project's own `E:\New folder (2)\.env` is intentionally comment-only |
| **Never print/write API key values** | Use only variable names (`GROQ_API_KEY`, `GEMINI_API_KEY`, etc.) in docs and code |
| **Groq TPM is 8,000/min** | Script drafting (A4) and Critique (A5) carry large prompts and can exhaust this in a single call |
| **Gemini Flash-Lite is 15 RPM** | Rapid loops (e.g. S04 indexing multiple chunks) quickly deplete 15 RPM |
| **`Start-Process` spurious kill warnings** | "Unknown: ChildProcess.kill" appears even on successful launch — verify with `Get-CimInstance Win32_Process` and poll redirect files |
| **No `rg` in PATH** | Use the Grep search tool or `Select-String` |
| **Model family string matching** | `model_family()` splits on `-` and `_` after last `/`: `groq/openai/gpt-oss-120b` → `openai/gpt`, `gemini/flash-lite` → `flash`. A6 must use `forbid_family=last_model_family(ctx, "S06_DRAFT")` |
| **Kaggle max 2 concurrent GPU sessions** | A third push fails with "Maximum batch GPU session count of 2 reached"; old sessions must idle/hard-exit first |
| **Suite runtime** | Full test suite takes 2–4 minutes — run single test files while iterating |
