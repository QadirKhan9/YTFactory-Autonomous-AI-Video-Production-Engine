# YTFactory — Agent Memory (handoff file)

> Written by a previous agent session. Contains NO API keys/passwords (env var names only).

## 1. Project overview

YTFactory is an autonomous YouTube production pipeline: given a topic, it runs a
20+ step workflow (research → sourcing → evidence → script → critique → fact-check
→ visuals → voice → packaging → QC) and delivers a video. It is "phase-built":
each step has a dry-run stub path and a `--live` real-LLM path.

**Current goal (the "Real LLM quality pass"):** all 9 agents (A1–A9) must run on
real LLMs with **zero `[agent-fallback]` stub lines**, verified end-to-end on a
fresh project through `DELIVERED`, plus an `llm-check` CLI command, refreshed
`providers.yaml`, updated docs, and a green test suite (229 tests currently pass).

Everything before that (phases 0–6, GPU/FLUX Kaggle worker) is complete; project 26
(`elon-musk-...-93ac4b7a`) already reached `DELIVERED` with real FLUX images.

## 2. Tech stack and structure

- Python 3.13.5, Windows 11, PowerShell 5.1 (NOT bash). No heredocs, no `rg`.
- Pydantic schemas, sqlite blackboard DB, `litellm` for model calls, `yaml` registry.
- LLM providers: Groq (8000 TPM free tier), Google Gemini (flash-lite 15 RPM free
  tier, flash 503s under demand). Model families enforced decorrelated (writer vs
  verifier must differ: e.g. `openai/gpt` vs `gemini`).

Main layout (root: `E:\New folder (2)`):

| Path | Purpose |
|---|---|
| `ytfactory/cli.py` | argparse CLI: `run`, `resume`, `rerun`, `llm-check`, etc. Entry: `python -m ytfactory.cli` (there is **no** `ytfactory/__main__.py`) |
| `ytfactory/orchestrator/router.py` | LLM chokepoint: tiers, demotion, JSON-repair retry, `usage_log`, transient heal-wait |
| `ytfactory/registry/providers.yaml` | Tier model lists + demotion config (edit YAML to change providers) |
| `ytfactory/steps/` | The pipeline steps: `common.py` (Ctx, `record_usage`), `research.py` (S01–S04), `script.py` (S05–S08), `visual.py` (S09+), `packaging.py` (S18), `ai_qc.py` (S20), `fetch.py` (source fetching incl. `search_arxiv`) |
| `ytfactory/workers/score.py` | Source tier gate (`MIN_TIER_A=5`, `TIER_A_DOMAINS`) |
| `ytfactory/agents/base.py` | `run_agent`: on `RouterError` falls back to deterministic stub (prints `[agent-fallback]`) |
| `ytfactory/schemas/` | Pydantic schemas per agent |
| `ytfactory/db.py` | sqlite blackboard; `usage_ledger` table via `add_usage()` |
| `tests/` | unittest suite, run with **discover** (tests is NOT a package) |
| `store/` | scratch dir (probe scripts, run logs, `ytfactory.db`) |
| `docs/` | `phases.md`, `architecture.md`, `kaggle-flux.md` (stale tier sample at line ~99–101) |
| `E:\New folder (2)\.env` | comment-only dotenv shadow that suppresses litellm's dotenv warning. **Do NOT edit `E:\.env`** (user's personal file; its line 22 `vultr  code` is the root cause of the original warning) |

## 3. Setup / run commands

Env vars (values already set via `setx` on this machine; never write values here):
- `GROQ_API_KEY`
- `GEMINI_API_KEY` (alias of `GOOGLE_API_KEY`, also setx'd; child shells may see
  GROQ but not GEMINI — set `$env:` defensively in launch commands)
- `GOOGLE_API_KEY`

Commands (PowerShell):
```powershell
# tests (full suite: ~140–245 s)
python -m unittest discover -s tests

# single test file (tests is not a package — must use discover)
python -m unittest discover -s tests -p "test_router.py"

# live LLM health check (probes all tiers, decorrelation verdict, exit 1 if unsafe)
python -m ytfactory.cli llm-check --timeout 30

# live pipeline
python -m ytfactory.cli run <topic> --live
python -m ytfactory.cli resume <project-slug> --live
python -m ytfactory.cli rerun <project-slug> --from S04 --live --until S08
```

## 4. Completed so far

- Keys wired (`setx`), live-probed working model IDs (probe results recorded in
  `providers.yaml` header comment; dead: all Groq `llama-*`, `groq/gpt-oss-120b`
  wrong prefix, `gemini/flash`, `gemini/flash-lite`, `gemma-3-*`, `mistral/free`).
- `providers.yaml` refreshed: cheap=[`groq/openai/gpt-oss-20b`,
  `gemini/gemini-3.5-flash-lite`, `gemini/gemini-3.5-flash`], writer=[`gemini/gemini-3.5-flash`,
  `groq/openai/gpt-oss-120b`, `gemini/gemini-3.5-flash-lite`], verifier=[`groq/openai/gpt-oss-120b`,
  `gemini/gemini-3.5-flash`]; demotion block now has `demote_minutes: 60`,
  `transient_demote_seconds: 60`, `max_recover_wait_s: 75`.
- `llm-check` CLI added (`_probe_model`, `cmd_llm_check`) + `tests/test_llm_check.py`
  (6 tests). Live run: 8/8 models OK, decorrelation OK, exit 0.
- arXiv backend `search_arxiv()` added to `workers/fetch.py` (keyless, Tier A) and
  wired as 3rd backend in `steps/research.py` — fixed the `BLOCKED_SOURCES` gate
  (OpenAlex returns doi.org = Tier C; Wikipedia/publishers 403).
- **Router rate-limit overhaul (just finished):**
  - `ytfactory/orchestrator/router.py`: `TRANSIENT_MARKERS` + `_is_transient()`;
    `_demoted` is `dict[str, tuple[float, bool]]`; `_demote(..., transient=False)`
    uses `transient_demote_seconds` (60 s) vs `demote_minutes` (60 min);
    `_recover_wait(tier)` bounded by `max_recover_wait_s` (75 s);
    `self.usage_log` list; `invoke()` resets `last_usage = {}` at start (fixes stale
    usage), appends each successful call to `usage_log`, and does **one** bounded
    heal-wait cycle (cycle 0 → sleep → cycle 1) for rate-limit storms.
  - `ytfactory/steps/common.py::record_usage` now drains `usage_log` (one row per
    real call; no `last_usage` fallback — that fallback caused double-records).
- Tests: new `TestTransientDemotion` class in `tests/test_router.py` (5 tests:
  short/long demotion, bounded heal-wait with `_FakeClock`, stale-usage clear,
  usage_log drain). Env-key test patches added in `test_phase2.py`, `test_phase3.py`,
  `test_phase5.py`. **Suite: 229 tests OK.**
- Dotenv shadow `.env` created (comment-only).

## 5. Where we stopped (current task, exact state)

**Task:** live-verify the router fix by rerunning project 29
(`the-james-webb-space-telescope-010a98b5`, JWST) from S04→S08, then resume to
`DELIVERED`, then docs + final suite.

**Last command run:**
```powershell
Start-Process -FilePath "python" -ArgumentList "-m","ytfactory.cli","rerun",
  "the-james-webb-space-telescope-010a98b5","--from","S04","--live","--until","S08"
  -WorkingDirectory "E:\New folder (2)"
  -RedirectStandardOutput "E:\New folder (2)\store\ladder.out"
  -RedirectStandardError "E:\New folder (2)\store\ladder.err"
```
(with `$env:GROQ_API_KEY` and `$env:GEMINI_API_KEY` set in the launching shell).

**Result of that run — NOT YET GREEN:**
- Output: `store/ladder.out`, `store/ladder.err`.
- S05/S06/S07 = ok (real Gemini/Groq calls). Router heal-waits fired correctly
  (`waiting 32s for recovery`, `waiting 11s for recovery`).
- **S04_EVIDENCE_INDEX → `[agent-fallback] A2` (still 2 fallback lines total).**
  Cause: two simultaneous free-tier limits hit at once —
  - Groq: `RateLimitError` 8000 TPM exhausted (retry in ~17–26 s)
  - Gemini flash-lite: 429 `generate_content_free_tier_requests` limit **15 RPM**
    (retry in ~28 s); note the message says "You exceeded your current quota" but
    it is the per-minute free-tier limit, retryable
  - Gemini flash: 503 high demand (transient)
  - The 32 s heal-wait was NOT enough because the FIRST attempt already burned the
    Gemini 15 RPM window and the wait+cycle-2 still got 429'd → exhausted → stub.
  - Also seen: `groq/openai/gpt-oss-20b: schema: Expecting ',' delimiter` (JSON
    parse failure on one reply; repair attempt also failed).
- **S08_FACT_CHECK → `BLOCKED_FACTS`** (run finished with
  `=> the-james-webb-space-telescope-010a98b5: BLOCKED_FACTS`).
  Also logged: `[nli] CrossEncoder unavailable (No module named 'sentence_transformers');
  routing all claims to A6` and `[embed] fastembed unavailable (BAAI/bge-small-en-v1.5
  not cached; set YT_EMBED=1 to download)`.

Files touched in this task: `ytfactory/orchestrator/router.py`,
`ytfactory/steps/common.py`, `ytfactory/registry/providers.yaml`,
`tests/test_router.py`.

## 6. Known bugs / errors / half-finished work

1. **A2 fallback persists** under rate-limit storms: one 75 s-capped heal-wait is
   sometimes insufficient for Gemini's 15 RPM window + Groq's TPM window
   simultaneously. Options: longer/second wait, or per-model staggered retries.
2. **S08 `BLOCKED_FACTS`**: fact-check gate failing on the live run — root cause not
   yet investigated (crossencoder/NLI missing means all claims go through A6 LLM;
   A6 may be failing verification thresholds).
3. `sentence_transformers` / `fastembed` not installed (NLI + embeddings degraded;
   code degrades gracefully but may affect S03/S08 gates).
4. `docs/architecture.md:99-101` tier sample is stale; `docs/phases.md` not yet
   updated with this LLM-pass verification rows.
5. Budget field `max_llm_calls_per_project: 80` exists in providers.yaml but
   enforcement is unverified; ledger count must be reported vs 80.
6. Groq key was pasted in chat earlier (by user) — recommend user rotate it; do not
   ever write it into files.

## 7. Next steps (exact order)

1. Diagnose S08: inspect `store/ladder.out` + `usage_ledger`/`events` for
   `the-james-webb-space-telescope-010a98b5`; find why `BLOCKED_FACTS`
   (check A6 verifier output, claim pass-rate gate in `steps/script.py` S08).
2. Fix A2 fallback robustness in `router.py` `invoke()`:
   - current single heal-wait (cycle 0 → sleep → cycle 1) is not enough for
     Gemini 15-RPM + Groq 8000-TPM overlap. Consider: stagger per-model retries
     within a cycle, or allow up to 2 waits bounded by `max_recover_wait_s`,
     or parse Gemini `retryDelay` (~28 s) from the error message. Keep the wait
     bounded (never unbounded sleep) and keep non-transient errors on 60-min demote.
   - Also check the `Expecting ',' delimiter` JSON-repair path for
     `groq/openai/gpt-oss-20b` (maybe increase repair attempts or lower max_tokens).
3. Add/adjust router tests for whatever change is made; run
   `python -m unittest discover -s tests -p "test_router.py"` then the FULL suite.
4. Rerun the ladder: `rerun the-james-webb-space-telescope-010a98b5 --from S04
   --live --until S08` (set `$env:GROQ_API_KEY`/`$env:GEMINI_API_KEY` first;
   use Start-Process + poll output files). Assert: **zero** `[agent-fallback]`
   lines, and A6's model family != A4/A5 family (query `usage_ledger`, latest row
   per step via `id DESC`).
5. `python -m ytfactory.cli resume the-james-webb-space-telescope-010a98b5 --live`
   → status `DELIVERED`; record LLM call count/tokens vs the 80-call budget.
6. Update docs: `docs/phases.md` (progress + verification rows), fix stale tier
   sample in `docs/architecture.md` (~line 99–101), optionally `docs/kaggle-flux.md`.
7. Final `python -m unittest discover -s tests` (expect 229+ OK).

## 8. Decisions made (and abandoned approaches)

- **Transient vs permanent demotion** instead of one flat 60-min ban: 429/5xx heal
  fast (60 s), auth/bad-id stay banned 60 min. Reason: a single Gemini 503 during
  S06 previously cascaded and demoted the whole ladder for an hour.
- **Bounded in-invoke heal-wait** (≤75 s, one cycle) rather than infinite retry —
  keeps steps interactive; abandoned: unbounded retries (would hang tests/CI).
- **`usage_log` drain in `record_usage`** (one ledger row per real call) instead of
  single `last_usage` — fixed stale/duplicated token rows (S07/S08 showed S06's
  counts) and the double-record bug from a `last_usage` fallback.
- **arXiv as 3rd source backend** instead of fixing OpenAlex/publisher 403s —
  keyless, Tier A, satisfies `MIN_TIER_A=5`.
- **comment-only `.env` shadow** to silence litellm's dotenv warning — abandoned:
  editing `E:\.env` (user's personal file, must not touch).
- **Tests must patch `_llm_keys_present` → False** when env keys are now setx'd
  globally, because keys present flips deterministic test behavior (existing
  convention: patch the check, not the env).
- **`llm-check` exit-code contract**: 1 if any tier empty or writer/verifier share
  a family — used as pre-flight before live runs.

## 9. Code conventions

- Tests: `python -m unittest discover -s tests` only (`tests` has no `__init__`;
  `python -m unittest tests.test_router` FAILS with ModuleNotFoundError).
- No comments unless asked; docstrings for public functions; concise style.
- Lazy imports for `litellm` inside `Router.invoke` (dry-run must work without it).
- Errors: `RouterError(RuntimeError)`; agents catch it in `run_agent` and fall back
  to the deterministic stub, logging `[agent-fallback]`.
- YAML config over code for providers/demotion (vendor death = YAML edit).
- Multi-line inline `python -c` breaks in PowerShell — use temp scripts under
  `store/` instead (never `python -` stdin).
- PowerShell: chain with `; if ($?) { ... }`; avoid `&&`.

## 10. Gotchas / do-not-touch

- **Do NOT edit `E:\.env`** (user's personal file). The project's own
  `E:\New folder (2)\.env` is intentionally comment-only.
- **Never print/write API keys** — env var names only in docs and code.
- `Start-Process` often throws spurious `Unknown: ChildProcess.kill` — the process
  usually still launches; verify with `Get-CimInstance Win32_Process -Filter
  "Name='python.exe'"` and poll the redirected output files.
- `python --% -c "..."` swallows the rest of the line — launch commands must be
  separate bash tool calls.
- No `rg` in this environment — use the Grep tool / `Select-String`.
- Groq TPM 8000 and Gemini flash-lite 15 RPM are the hard free-tier ceilings;
  runs that reuse stale demotions (60-min bans) can starve later steps — a fresh
  router per run process means demotions reset on each CLI invocation.
- `model_family()` parses by the segment after the last `/` split on `-`/`_`:
  `groq/openai/gpt-oss-120b` → `openai/gpt`, `gemini/flash-lite` → `flash`,
  `groq/llama-3.3-70b` → `llama`. The A6 verifier must use
  `forbid_family=last_model_family(ctx, "S06_DRAFT")`.
- Suite runtime is minutes long — run single test files while iterating.
