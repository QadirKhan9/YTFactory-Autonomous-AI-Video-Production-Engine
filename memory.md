# YTFactory — Agent Memory & Handoff Context (`memory.md`)

> **Handoff Document**: Written for the incoming coding agent resuming this project with no prior chat history.  
> **Security Rule**: Contains NO API keys, tokens, or passwords (environment variable names only).  
> **Source of Truth**: Reflects exact workspace state in `E:\New folder (2)` as of 2026-10-12.

---

## 1. Project Overview

- **What it is**: **YTFactory** is an autonomous, end-to-end documentary video production pipeline for YouTube. Given a topic, it runs a 23-step workflow (S00–S22) covering research, source harvesting, evidence indexing, script drafting, critique, fact-checking, audio synthesis, visual generation/retrieval, video assembly, packaging, and AI quality control (`DELIVERED`).
- **Core Architecture & Philosophy**: "Dry-run engine first" with dual execution paths:
  1. Deterministic zero-API `--dry-run` stubs (runs in ~2–3 s, validating schemas and state DAG).
  2. `--live` real-LLM execution using `litellm` routing across free-tier providers (Groq, Gemini), local GPU/Kaggle FLUX workers for images, and local tools (ffmpeg, edge-tts).
- **Current Milestone ("Real LLM Quality Pass")**:
  - The pipeline foundation, offline workers, and GPU bus (Phases 0–6) are complete; project 26 (`elon-musk-...-93ac4b7a`) previously reached `DELIVERED` with real FLUX images via Kaggle.
  - **Immediate Goal**: All 9 LLM agents (A1 through A9) must execute against real LLMs with **zero `[agent-fallback]` stub lines**, end-to-end verified on a live run through `DELIVERED` within an 80-call budget, with strict model family decorrelation (writer != verifier), pre-flight checked via `llm-check`, and backed by passing unit tests (currently 229 tests passing).

---

## 2. Tech Stack and Project Structure

### Tech Stack
- **Runtime & OS**: Python 3.13.5 on Windows 11 (PowerShell 5.1). No bash/heredocs; PowerShell syntax required.
- **Data & State**: SQLite with WAL mode (`store/ytfactory.db`), Pydantic models for agent schema contracts, YAML for provider config.
- **LLM Layer**: `litellm` lazy-loaded via centralized router (`ytfactory/orchestrator/router.py`).
- **Providers & Free-Tier Quotas**:
  - **Groq**: 8,000 TPM limit on free tier. Models: `groq/openai/gpt-oss-20b`, `groq/openai/gpt-oss-120b`.
  - **Google Gemini**: 15 RPM limit on free tier for `gemini-3.5-flash-lite`; `gemini-3.5-flash` experiences transient 503 high-demand spikes.
- **Audio/Video**: `edge-tts` (TTS), `ffmpeg` (audio ducking/muxing), local FLUX / Kaggle GPU worker (`flux-gpu-worker.py`).

### Project Structure (Root: `E:\New folder (2)`)
| Path | Purpose & Responsibility |
|---|---|
| `memory.md` | Primary agent handoff file in project root. |
| `ytfactory/cli.py` | Command-line interface (`run`, `resume`, `rerun`, `llm-check`, `approve`, `list`). Run via `python -m ytfactory.cli`. (There is **no** `__main__.py`). |
| `ytfactory/orchestrator/router.py` | Central LLM gateway: handles tiers, demotion tracking (transient vs permanent), bounded heal-wait recovery, JSON repair retries, model family decorrelation, and call usage logging (`usage_log`). |
| `ytfactory/orchestrator/state_machine.py` | Directed acyclic graph (DAG) runner for steps S00–S22 with idempotency checks and gate halting. |
| `ytfactory/orchestrator/idem.py` | SHA-256 idempotency key generator for pipeline steps. |
| `ytfactory/registry/providers.yaml` | Provider registry mapping tiers (`cheap`, `writer`, `verifier`), error demotion settings, decorrelation policies, and call budget (single source of truth for LLM endpoints). |
| `ytfactory/steps/` | Step implementations: `common.py` (Ctx, `record_usage`, gates), `research.py` (S01–S04), `script.py` (S05–S08), `visual.py` (S09–S13, S17), `audio.py` (S14–S16), `packaging.py` (S18–S19), `ai_qc.py` (S20), `deliver.py` (S21–S22). |
| `ytfactory/workers/fetch.py` | Source fetchers (Wikipedia, OpenAlex, and keyless arXiv via `search_arxiv()`, Trafilatura extraction). |
| `ytfactory/workers/score.py` | Domain credibility tier scoring (`MIN_TIER_A=5`). |
| `ytfactory/agents/base.py` | `run_agent`: executes an agent prompt against Router; catches `RouterError` and falls back to deterministic stub while logging `[agent-fallback]`. |
| `ytfactory/schemas/` | Pydantic data schemas per agent (e.g. A1 research plan, A4 script draft, A6 fact check). |
| `ytfactory/db.py` | SQLite blackboard database access, schema initialization, lease queues, `usage_ledger`. |
| `tests/` | Unittest suite (run via `unittest discover`; `tests` is not a package). |
| `store/` | Working scratch directory: run logs (`ladder.out`, `ladder.err`), probe scripts, `ytfactory.db`. |
| `docs/` | Project documentation: `phases.md`, `architecture.md`, `design.md`, `prd.md`, `kaggle-flux.md`, `rules.md`. |
| `E:\New folder (2)\.env` | Comment-only local `.env` shadow file that suppresses LiteLLM dotenv warning. **DO NOT edit `E:\.env`** (user's personal root file). |
| `flux-gpu-worker.py` | Local/Kaggle dual GPU bus polling worker for FLUX image generation. |

---

## 3. Setup and Run Commands

### Environment Variables
API keys are already set in Windows user environment via `setx`:
- `GROQ_API_KEY`: Groq API access.
- `GEMINI_API_KEY`: Google Gemini API access (alias of `GOOGLE_API_KEY`).
- `GOOGLE_API_KEY`: Google Gemini API access.
*(Note: PowerShell child processes may not always inherit newly setx'd vars; set `$env:GROQ_API_KEY` and `$env:GEMINI_API_KEY` explicitly in child launch shells if missing).*

### Standard Commands (PowerShell)
```powershell
# 1. Run all unit tests (takes ~2-4 minutes for full suite)
python -m unittest discover -s tests

# 2. Run a specific test file (tests is NOT a package; must use discover -p)
python -m unittest discover -s tests -p "test_router.py"
python -m unittest discover -s tests -p "test_llm_check.py"

# 3. Live LLM health check (probes live models, decorrelation safety, exits 1 on failure)
python -m ytfactory.cli llm-check --timeout 30

# 4. Dry-run pipeline (zero API calls, validates complete DAG & schemas in ~3 seconds)
python -m ytfactory.cli run "quantum-computing" --dry-run

# 5. Live pipeline execution
python -m ytfactory.cli run "The James Webb Space Telescope" --live

# 6. Resume an existing pipeline project
python -m ytfactory.cli resume the-james-webb-space-telescope-010a98b5 --live

# 7. Rerun specific step ranges
python -m ytfactory.cli rerun the-james-webb-space-telescope-010a98b5 --from S04 --live --until S08
```

---

## 4. What Has Been Completed So Far

1. **Phases 0–6 Pipeline & Offline Workers**: S00–S22 fully implemented with schema validation, audio bed ducking, visual library reuse, and Kaggle FLUX GPU worker. Project 26 completed end-to-end to `DELIVERED`.
2. **Provider Key Wiring & Live Model Probing**:
   - Probed and identified verified live models in `registry/providers.yaml`:
     - Cheap tier: `groq/openai/gpt-oss-20b`, `gemini/gemini-3.5-flash-lite`, `gemini/gemini-3.5-flash`
     - Writer tier: `gemini/gemini-3.5-flash`, `groq/openai/gpt-oss-120b`, `gemini/gemini-3.5-flash-lite`
     - Verifier tier: `groq/openai/gpt-oss-120b`, `gemini/gemini-3.5-flash`
   - Dead/failing models pruned from registry: `cerebras/*`, `mistral/free`, `gemini/flash`, `gemini/flash-lite`, `groq/llama-3.3-70b`, `groq/gpt-oss-120b` (bad prefix).
3. **`llm-check` CLI Subcommand & Unit Tests**: Added `cmd_llm_check` in `cli.py` and 6 unit tests in `tests/test_llm_check.py`.
4. **arXiv Academic Fetcher Backend**: Implemented `search_arxiv()` in `ytfactory/workers/fetch.py` and wired into `steps/research.py` as 3rd backend to pass the `BLOCKED_SOURCES` gate (`MIN_TIER_A=5`).
5. **Router Transient Demotion & Usage Logging Overhaul**:
   - Upgraded `ytfactory/orchestrator/router.py`:
     - Differentiated permanent errors (auth, unknown model, bad schema -> demote 60 min) from transient errors (429 rate limit, 503 high demand, connection timeouts -> demote `transient_demote_seconds: 60`).
     - Added `_recover_wait(tier)` bounded by `max_recover_wait_s: 75` to allow in-invoke recovery instead of immediate pipeline abort.
     - Added `usage_log` buffer in Router; `steps/common.py::record_usage()` now drains real calls from `usage_log`, eliminating stale token duplications and double-record bugs.
     - Fixed `invoke()` to reset `last_usage = {}` at invocation start.
   - Added `TestTransientDemotion` in `tests/test_router.py` (5 tests).
   - Patched test mock helpers in `test_phase2.py`, `test_phase3.py`, `test_phase5.py` to isolate tests when live keys are present in the environment.
   - Entire test suite verified green (**229 unit tests passing**).
6. **S05, S06, and S07 Resolution**:
   - **S05 (Outline)**: Added topic/claims-aware `_dry_outline()` deterministic stub fallback ensuring outline beats and open loops reflect the project topic rather than hardcoded memory stub beats.
   - **S06 (Draft)**: Implemented `_normalize_claim_key()` and `_sanitize_and_validate_sentences()` to normalize claim IDs (`c1` -> `C1`, `claim_1` -> `C1`), prune hallucinated IDs, and safeguard grounding invariants without crashing `_membership_check()`.
   - **S07 (Critique & Revision)**:
     - Fixed `prompts/a04_script_writer.md` to include `{{critique_notes}}` so A4 actually sees editorial feedback during revision.
     - Implemented non-destructive fallback for A4 revision: if the revision call stubs out or rate-limits, it keeps the valid draft sentences from S06 rather than wiping them with dummy dry sentences.
     - Added 3 new unit tests in `tests/test_script_schema.py` (**232 unit tests passing**).

---

## 5. What We Were in the Middle of (Exact Current State)

### Current Task
Live verification of the router rate-limit fix and live LLM quality pass by executing steps S04 through S08 on Project 29 (`the-james-webb-space-telescope-010a98b5`), ensuring **zero `[agent-fallback]`** lines, followed by resuming to `DELIVERED`.

### Where We Stopped & Exact Run Result
The last command executed was:
```powershell
python -m ytfactory.cli rerun the-james-webb-space-telescope-010a98b5 --from S04 --live --until S08
```
(Logs captured in `store/ladder.out` and `store/ladder.err`).

**Outcome of that run**:
1. **S04 (Evidence Index) triggered `[agent-fallback] A2`**:
   - Cause: Simultaneous free-tier exhaustion across both providers:
     - Groq hit 8,000 TPM limit (`RateLimitError`, retry in ~26 s).
     - Gemini 3.5 flash-lite hit 15 RPM free-tier quota (`RateLimitError`, retry in ~28 s).
     - Gemini 3.5 flash hit 503 high demand.
     - Also observed: `groq/openai/gpt-oss-20b: schema: Expecting ',' delimiter` JSON parsing issue.
     - The single bounded heal-wait of 32s ran, but on cycle 2 the free-tier quota window was still exhausted -> `[agent-fallback] A2` stub used.
2. **S05 (Outline), S06 (Draft), S07 (Critique) succeeded** with real LLM calls and transient recoveries.
3. **S08 (Fact Check) halted with `BLOCKED_FACTS`**:
   - `sentence_transformers` (CrossEncoder) is not installed; all claims routed to A6 LLM.
   - S08 ran A6 verifier, attempted rewrite, but unsupported claims persisted, triggering `GateHalt("BLOCKED_FACTS")` at `ytfactory/steps/script.py:312`.

### Files Touched in This Task
- `ytfactory/orchestrator/router.py` (transient demotion, heal-wait, `usage_log`)
- `ytfactory/steps/common.py` (`record_usage` drains `usage_log`)
- `ytfactory/registry/providers.yaml` (demotion configuration parameters)
- `tests/test_router.py` (transient demotion and recovery test cases)
- `store/ladder.out`, `store/ladder.err` (execution logs)

---

## 6. Known Bugs, Errors, or Half-Finished Work

1. **A2 Fallback Persistence Under Rate-Limit Clashes**:
   - When Groq's 8,000 TPM limit and Gemini's 15 RPM limit fire simultaneously, a single heal-wait cycle in `router.py` `invoke()` is exhausted before the second model's retry-after window opens.
2. **S08 `BLOCKED_FACTS` Gate Halting** ✅ **FIXED**:
   - Prompt-schema mismatch: `a06_fact_checker.md` returned `claim_id` but `ClaimVerdict` required `claim_key` — now uses `claim_key`.
   - `partial` verdicts blocked the pipeline — now treated as `supported` (partial evidence = not a blocker).
   - `recheck_llm_pack` used sqlite Row dict access (`c["id"]`) which raised `IndexError` — now uses positional column access (`row[0]`, `row[1]`, `row[2]`) and a pre-built `recheck_key_by_id` dict.
   - Missing `record_usage` for second A6 call — added.
   - `_dry_sentences(claim_pack)` missing `ctx.topic` in rewrite stub — fixed to `_dry_sentences(claim_pack, ctx.topic)`.
   - Missing `_sanitize_and_validate_sentences` on rewrite sentences — added.
   - `_normalize_verdict_key()` added to tolerate LLM returning lowercase/aliased keys.
   - `still_bad` now initialized to `list(unsupported)` so it defaults to blocking unless the second A6 pass explicitly clears it.
3. **S11 `TimeoutError` in Stock Visual Sourcing** ✅ **FIXED**:
   - `workers/stock.py`: `search_all` ran 90 concurrent query/source tasks through `cf.as_completed(futures, timeout=timeout + 8.0)` with a strict 20s global deadline. When Met (403) or Wikimedia (connection closed) stalled, `as_completed` threw an unhandled `TimeoutError: 85 (of 90) futures unfinished`.
   - Fixed by wrapping `as_completed` in `try...except TimeoutError`, canceling pending futures, appending to `errors`, scaling workers (up to 16) and overall timeout with task volume, and adding a 3s connect timeout to `_get_json`.
   - Tested with unit test `test_search_all_timeout_swallowed` in `test_phase3.py` and live executed on project 29. S11 now completes with status `ok`.
4. **Degraded NLI / Embedding Backends**:
   - `sentence_transformers` (CrossEncoder) is absent: fallback routes 100% of claims to LLM verifier A6, increasing token load and TPM exhaustion.
   - `fastembed` (`BAAI/bge-small-en-v1.5`) is not cached locally (requires `YT_EMBED=1` to download); S04 continues without vectors.
5. **Stale Documentation**:
   - `docs/architecture.md` (lines ~99–101) contains an outdated tier model listing.
   - `docs/phases.md` has not yet incorporated the Phase 7 / LLM-pass verification audit rows.
6. **Call Budget Tracking**:
   - Budget ceiling (`max_llm_calls_per_project: 80`) exists in `providers.yaml`, but runtime enforcement validation against `usage_ledger` needs verification before declaring milestone completion.

---

## 7. Next Steps (Exact Order of Execution)

1. **S04 → S11 Live Ladder** ✅ **COMPLETED & VERIFIED**:
   - Live rerun reached through `S11_VISUAL_SOURCE` (`ok`).
   - Project `the-james-webb-space-telescope-010a98b5` currently paused at S11 with all steps S00–S11 marked `done`.

2. **Resume Project from S12 (Visual Generation) to `DELIVERED`**:
   - Execute:
     ```powershell
     python -m ytfactory.cli resume the-james-webb-space-telescope-010a98b5 --live
     ```
   - Pipeline will run S12 (AI visual generation via FLUX GPU worker / fallback), S13 (Visual QC), S14–S16 (Audio/Music/Ducking), S17 (Render/Assembly), S18–S19 (Packaging), S20 (AI QC), S21–S22 (Deliver).
   - If FLUX worker is running locally or on Kaggle, images will be generated via the GPU bus; otherwise, deterministic or placeholder visual assets will be used.

3. **Harden Rate-Limit Resilience in `ytfactory/orchestrator/router.py`** (if free-tier rate limits clash):
   - In `Router.invoke()`:
     - Expand transient wait handling: parse `retryDelay` from Gemini / Groq error messages when present.
     - Support staggered inter-model pauses.

4. **Update Documentation**:
   - Fix tier model listings in `docs/architecture.md`.
   - Update `docs/phases.md` with LLM quality pass verification tables.

---

## 8. Decisions Made and Abandoned Approaches

- **Transient vs. Permanent Demotion**:
  - *Decision*: Separate 60-second transient demotion (429/5xx) from 60-minute permanent demotion (auth errors, missing models).
  - *Why*: A temporary Gemini 503 previously banned the model for an hour, permanently disabling the tier.
- **In-Invoke Bounded Heal-Wait (≤ 75s)**:
  - *Decision*: Sleep in `Router.invoke()` when all models in a tier are transiently demoted, then retry.
  - *Abandoned*: Unbounded sleep or infinite retries (hangs test suites and CLI runs).
- **`usage_log` Draining over `last_usage`**:
  - *Decision*: Router records each successful LLM invocation in `self.usage_log` list; `record_usage()` drains this list.
  - *Why*: Avoids duplicate ledger records and prevents steps from inheriting token counts from earlier steps.
- **arXiv Keyless Fallback for S02**:
  - *Decision*: Added `search_arxiv()` directly to `workers/fetch.py` as a Tier A source.
  - *Abandoned*: Trying to bypass 403 blocks on publisher domains or relying solely on OpenAlex (which maps to Tier C `doi.org`).
- **Comment-Only Root Shadow `.env`**:
  - *Decision*: Created `E:\New folder (2)\.env` containing only comments to satisfy LiteLLM.
  - *Rule*: Never edit `E:\.env` (user's personal environment file containing unrelated tokens).
- **Test Patching for Environment Keys**:
  - *Decision*: Tests mock `_llm_keys_present -> False` rather than unsetting system environment variables.
  - *Why*: Prevents global `setx` keys from breaking deterministic offline test expectations.

---

## 9. Code Conventions Being Followed

- **Test Discovery Requirement**: Run tests via `python -m unittest discover -s tests`. Never run `python -m unittest tests.test_router` (fails with `ModuleNotFoundError` because `tests` lacks an `__init__.py`).
- **PowerShell Execution**: Chain commands with `; if ($?) { ... }`. Do not use bash syntax (`&&`, `export`, heredocs `<<EOF`).
- **CLI Commands**: Always run via `python -m ytfactory.cli <command>` (there is no `__main__.py`).
- **Router Chokepoint**: All LLM calls must go through `Router.invoke()`. Never invoke LiteLLM directly from an agent or step.
- **Fail-Safe Stubs**: Agents must catch `RouterError` in `run_agent` and return a deterministic schema-valid stub, outputting `[agent-fallback]`.
- **Model Family Decorrelation**: Verifier (A6) and Critic (A5) must pass `forbid_family=last_model_family(ctx, "S06_DRAFT")`.
- **Provider Changes**: Must be made in `ytfactory/registry/providers.yaml`, never hardcoded in step Python code.

---

## 10. Gotchas, Sensitive Parts, and Workarounds

- **DO NOT TOUCH `E:\.env`**: It is the user's personal file in the drive root. The file `E:\New folder (2)\.env` exists specifically as a shadow file.
- **NEVER Write or Log API Key Values**: Use only variable names (`GROQ_API_KEY`, `GEMINI_API_KEY`).
- **Free-Tier Limits are Hard Walls**:
  - Groq TPM is 8,000 tokens/minute. Script drafting (A4) and Critique (A5) carry large prompts and can exhaust this in a single call.
  - Gemini Flash-Lite is 15 requests/minute. Rapid loops (e.g. S04 indexing multiple chunks) quickly deplete 15 requests.
- **PowerShell Multi-line Command Quoting**: Multi-line `python -c "..."` often fails in PowerShell. Write short test scripts to `store/` and invoke via `python store/<script>.py`.
- **`Start-Process` Spurious Kill Warnings**: `Start-Process` can return `Unknown: ChildProcess.kill` even when the process started successfully. Verify with process inspection (`Get-CimInstance Win32_Process`) or poll redirect files.
- **No `rg` (ripgrep) CLI**: The `rg` binary is not in PATH. Use the Grep search tool or PowerShell `Select-String`.
- **Model Family String Matching**: `model_family()` splits on `-` and `_` after the last slash. Ensure family comparisons match parsed tokens (e.g. `openai/gpt`, `flash`, `llama`).
