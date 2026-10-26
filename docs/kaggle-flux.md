# Real Kaggle + FLUX run — step by step

End-to-end guide for running the **real** FLUX.1-schnell GPU worker (Phase 6.1)
instead of the local simulator. Cost: **$0** (Kaggle free GPU quota + Hugging
Face free tier). Tested environment: Windows 11 / PowerShell / Python 3.13.

---

## 0. How the pieces fit

```
 laptop (this repo)                    Hugging Face (the "cable")           Kaggle GPU session
 ──────────────────                    ──────────────────────────           ──────────────────
 rerun --from S13 --live  ──upload──▶  inbox/<batch_id>.json
                                       inbox/<batch_id>.<worker>.claim ◀──  gpu_worker.py claims
                                       outbox/<job_id>.png          ──▶     renders FLUX.1-schnell
                                       outbox/<job_id>.result.json  ──▶     {sha256, model, ...}
                     ◀──reap+sha verify─
 S13 links assets, S12 records license "apache-2.0"
```

Key facts (architecture §1.3):

- Kaggle GPU sessions **cannot accept inbound connections** — the worker is
  *pull-based*: it polls the bus, claims a batch, renders, uploads results.
- Only **FLUX.1-schnell** (Apache 2.0) may run. Blocklists are code in *both*
  `ytfactory/workers/gpubus.py` and `kaggle/gpu_worker/gpu_worker.py`
  (FR-V-08, parity-tested).
- The laptop **verifies sha256** of every result before it touches a project.
- No worker? Timeouts? S13 degrades to library abstracts → typographic cards.
  The pipeline never blocks on the GPU.

---

## 1. Prerequisites (one-time)

- [x] `kaggle` CLI installed (`pip install kaggle`) — **already on this machine**
- [x] `huggingface_hub` installed — **already on this machine** (0.35.x)
- [x] `%USERPROFILE%\.kaggle\kaggle.json` API token — **already present**
- [ ] Hugging Face account + write token (`hf_...`): <https://huggingface.co/settings/tokens>
- [ ] Kaggle account: <https://www.kaggle.com> (username = profile URL segment)
- [ ] **Accept the FLUX.1-schnell license** once while logged in:
      <https://huggingface.co/black-forest-labs/FLUX.1-schnell> → *Agree and
      access* (the repo is `gated: auto`; without this every job fails with
      `GatedRepoError: 403`). Verify with a token-cached
      `hf_hub_download(repo, "model_index.json")`.

Set the HF token for this PowerShell session (or use `setx HF_TOKEN "hf_..."`
once for permanence — then open a new terminal):

```powershell
$env:HF_TOKEN = "hf_YourTokenHere"
```

---

## 2. The bus repo (laptop side)

Nothing to create by hand: the first enqueue auto-creates a **private**
Hugging Face *dataset* repo (`ensure_repo(exist_ok=True, private=True)`).
Pick the repo id once and use it everywhere:

```text
<hf-username>/ytfactory-gpu-bus
```

## 3. Bus config (Kaggle side)

The worker resolves the bus repo in this order (`_load_env()`):

1. **Config dataset** (recommended — versioned, visible in the kernel):
   publish a private Kaggle dataset containing `bus_config.json`:

   ```json
   { "YT_GPU_BUS_REPO": "<hf-username>/ytfactory-gpu-bus",
     "HF_TOKEN": "hf_..." }
   ```

   `kaggle datasets create -p <dir> -t -q` once (metadata file must be named
   **`dataset-metadata.json`**, singular), then
   `kaggle datasets version -p <dir> -m "..."` after every edit. Reference it
   in `kernel-metadata.json` via `dataset_sources`, and mount it by
   `kaggle datasets files` name — the worker globs
   `/kaggle/input/**/bus_config.json` (**recursive**; the real mount path is
   3 levels: `/kaggle/input/datasets/<owner>/<name>/bus_config.json`).
2. **Kaggle Secrets** fallback: Add-ons → Secrets → `YT_GPU_BUS_REPO` +
   `HF_TOKEN`.

If neither is found it prints
`[gpu-worker] YT_GPU_BUS_REPO not set — nothing to do` and exits 1.
Mounting a config dataset means **`kaggle datasets version` must finish
before you push** — a session mounts the dataset version that existed at
push time (stale config = worker watches the wrong bus).

## 4. Point the kernel at your account and push it

Edit `kaggle/gpu_worker/kernel-metadata.json` — change `id` to **your** Kaggle
username:

```json
"id": "<kaggle-username>/flux-gpu-worker"
```

Then push:

```powershell
kaggle kernels push -p kaggle/gpu_worker --accelerator gpuT4x2
```

**`push` IS the run** — this kaggle CLI (2.2.4) has **no `kaggle kernels
run/stop/attach`** commands. A successful push prints
`Kernel version N successfully pushed` and starts a session immediately.
(`enable_gpu` and `enable_internet` are already `true` in the metadata.)

To follow logs (plain `kaggle kernels logs` returns **empty** — `-f` is
required for a live tail):

```powershell
kaggle kernels logs -f zain234/flux-gpu-worker
```

Console-encoding gotcha on Windows: the log contains progress-bar characters
that break PowerShell's codepage — redirect to a file instead:
`Start-Process kaggle -ArgumentList 'kernels','logs','-f','<user>/<slug>' -RedirectStandardOutput wlog.txt`.

## 5. Configure the laptop

```powershell
$env:YT_GPU_BUS_REPO = "<hf-username>/ytfactory-gpu-bus"
# HF_TOKEN from step 1 must be set too (HfBus uses it for uploads)
```

Real FLUX needs a **much bigger timeout than the 60 s default** (model download
+ first render easily exceed it). `--set gpu_timeout_s=2700` (45 min) is what
the verified live run used; per-scene renders after the first pipeline load
run ~1–3 min each:

```powershell
python -m ytfactory.cli rerun <slug> --from S13 --live `
    --set image_gen=gpu --set gpu_timeout_s=2700
```

`image_gen=gpu` forces the bus on; (Config knobs: `gpu_model` default
`FLUX.1-schnell`, `gpu_hf_repo` = bus repo if you prefer config over env.)

## 6. Start the GPU session, then trigger S13

**Order matters**: the worker exits after ~15 minutes with an empty inbox
(`IDLE_EXIT_POLLS=45` × 20 s poll), so push it shortly before the laptop
enqueues (or re-push to refresh the window).

1. Push (this starts the session — see step 4):

   ```powershell
   kaggle kernels push -p kaggle/gpu_worker --accelerator gpuT4x2
   kaggle kernels status <user>/<slug>          # RUNNING / COMPLETE
   ```

   **Max 2 concurrent GPU sessions** per account — a third push fails with
   `Maximum batch GPU session count of 2 reached`. Old sessions must
   idle/hard-exit first (no stop command exists); plan pushes accordingly:
   never push while two sessions are `RUNNING`.

2. In the logs expect:

   ```text
   [gpu-worker] kaggle-xxxxxxxx watching <hf-username>/ytfactory-gpu-bus
   ```

   First session downloads the **GGUF-quantized transformer**
   (`city96/FLUX.1-schnell-gguf` repo root, default `flux1-schnell-Q5_K_S.gguf`
   ≈ 8.3 GB — city96 ships **no Q4_K_M**; override with `YT_FLUX_GGUF`) plus
   the fp16 pipeline components from `black-forest-labs/FLUX.1-schnell`
   (`YT_FLUX_PIPELINE_REPO` can point at an ungated mirror, e.g.
   `YuCollection/FLUX.1-schnell-Diffusers`) — a few minutes on Kaggle's
   network. Then it polls every 20 s.

   Two subtle requirements, both handled in `gpu_worker.py`:

   - `gguf` must be **≥ 0.10** — `_ensure_deps()` pip-upgrades it, then
     **purges `diffusers`/`gguf`/`accelerate` from `sys.modules`**: diffusers
     caches `is_gguf_available()` at import time, so a pip install after the
     first `import diffusers` is invisible without the purge (symptom:
     `Loading a GGUF checkpoint in PyTorch, requires ... GGUF>=0.10.0`).
   - Full fp16 FLUX is ~34 GB RAM — pointless on the 29 GB session, so the
     full-load fallback is skipped below 40 GB RAM (`psutil` guard) and the
     GGUF path is the only path.

3. From the laptop (second terminal) run step 5's command. Watch S13 notes:

   ```text
   S13 gpu batch <id>: 7 job(s) enqueued
   S13 gpu batch <id>: 7 result(s) verified
   ```

Worker lifecycle: heartbeat every 60 s, batch lease 15 min, self-stops after
**55 minutes** (`MAX_SESSION_S`) so the session is returned to the pool.
A batch already fully rendered in the outbox is never re-rendered — re-running
the notebook later is always safe.

## 7. Verify the run

```powershell
python -m ytfactory.cli list --project <slug>
```

- `S13_IMAGE_GEN ok` — outputs contain `"gpu_generated": N` (N = scene count)
- CREDITS/SOURCES list the images with `license_id apache-2.0`
- spot-check the DB (`--%` = PowerShell stop-parsing; without it PowerShell
  eats the inner quotes):

```powershell
python --% -c "import sqlite3;c=sqlite3.connect('store/ytfactory.db');c.row_factory=sqlite3.Row;rows=c.execute('SELECT sha256, license_id, source_url FROM media_assets').fetchall();print([dict(r) for r in rows if r['license_id']=='apache-2.0'])"
```

- eyeball a result: open `outputs/<slug>/assets/` — real FLUX images, not the
  grey-gradient simulator placeholders (real renders are 0.5–1.4 MB vs ~35 KB
  library abstracts).

**Verified live 2026-10-09**: slug `elon-musk-...-93ac4b7a`, 7/7 scenes
`gpu_generated` (library_reused 0, cards 0), `license_id apache-2.0`,
S13 ok → approve → resume → **DELIVERED**.

---

## 8. Failure modes (all non-fatal)

| What happened | What you'll see | What happens next |
|---|---|---|
| Worker not running / wrong repo | `S13 gpu unavailable ... timeout after Ns` note | S13 falls back: library abstracts → typographic cards; run still delivers |
| `YT_GPU_BUS_REPO` missing on laptop | every batch note says `gpu unavailable` | same fallback; set the env var |
| Notebook secret missing | `[gpu-worker] YT_GPU_BUS_REPO not set` exit 1 | fix secret (step 3), re-run session |
| Default 60 s timeout | `gpu bus timeout after 60s: 0/7 done` | `--set gpu_timeout_s=900` |
| sha mismatch (corrupt upload) | `sha mismatch for <job>` | treated as bus failure → fallback; re-run S13 |
| Model blocklisted | per-job `model not allowed` results → fallback | only `FLUX.1-schnell` is allowed (FR-V-08) |
| Session died mid-batch | batch claim stuck forever (HF claims have no lease steal) | delete the stale file: `inbox/<batch_id>.<worker_id>.claim` in the HF repo (UI or `HfApi.delete_file`), re-run notebook |
| Worker idle-exits before enqueue | log: `inbox idle — ending session` | start worker first, then run S13; or just re-run the session (rendered batches are skipped) |
| `ImportError: diffusers` | notebook crash on first render | Kaggle images ship diffusers; if not, add a first cell `%pip install -q diffusers transformers accelerate safetensors` and switch kernel type to **notebook** |
| HF rate limit (429) | intermittent claim/upload errors | wait a minute; keep one worker session only |
| Kaggle GPU quota used up | session won't start | free quota resets weekly (~30 h GPU); S13 falls back meanwhile |
| FLUX license not accepted | per-job `GatedRepoError: 403` → fallback | accept the license once (step 1); clear scene assets and re-run S13 |
| Old/absent `gguf` on the image | `Loading a GGUF checkpoint in PyTorch, requires ... GGUF>=0.10.0` → `OSError: Unable to load weights` | already fixed: `_ensure_deps()` upgrades `gguf` **and purges `diffusers` from `sys.modules`** (availability flags are cached at import); RAM guard then skips the doomed full load |
| Session mounted a stale config dataset | worker logs a different bus repo than the laptop enqueues to; laptop times out | `kaggle datasets version` **before** `kaggle kernels push`; rotate the bus repo (`-v2`, `-v3`) rather than digging through stale claims/error files |
| Stale `outbox/*.result.json` from an older attempt | new batch "verifies" instantly with old error results | fixed: S13 job ids carry a per-attempt nonce (test `test_gpu_job_ids_unique_per_attempt`); safest is also rotating the bus repo |
| `Maximum batch GPU session count of 2 reached` | push rejected | wait for a session to idle/hard-exit (`kaggle kernels status`), then push |
| Transient HF outage during result download | (previously) crash: `ReadTimeout: cdn-lfs-us-1.hf.co` killed the run | fixed: `gpubus.reap` retries download/sha errors until the deadline |

---

## 9. Dry practice without Kaggle (local simulator)

Same inbox/outbox protocol, placeholder pixels — verified end-to-end in
Phase 6. In one terminal:

```powershell
python -m ytfactory.workers.gpubus --watch
```

Then in another terminal the same command from step 5. Ctrl+C stops the
simulator. This is how every live Phase-6 verification ran on this machine.

---

## 10. Quota & cost recap

- Kaggle GPU: free (~30 h/week, 2× T4); worker self-limits to 55 min/session.
- Hugging Face: free tier covers a private dataset bus of this size.
- FLUX.1-schnell: Apache 2.0 — commercially usable, no attribution required
  (credited anyway, as `apache-2.0`, in CREDITS.md).
- Blocked models (never ran, never will): FLUX.1-dev, XTTS v2, F5-TTS,
  Fish Speech NC, MusicGen, Wav2Lip, SD 3.5 Large — see FR-V-08.

## 11. Clean-up between experiments

Bus files accumulate in the HF repo (`inbox/`, `outbox/`). Safe to delete
everything when no session is running — the laptop re-enqueues fresh batches
on the next S13 run. Local simulator state lives in `store/gpu_bus/`.
