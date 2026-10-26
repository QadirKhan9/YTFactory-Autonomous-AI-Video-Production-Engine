# YTFactory — Operating Rules & Engineering Guidelines (`rules.md`)

**Version:** 1.0  
**Status:** Active Governance Document  
**Scope:** Applied to all code, architecture decisions, and workflow execution within YTFactory.

---

## 1. Financial & Provider Rules ($0.00 / Card-Free Constraint)

1. **Strict $0.00 Operating Cost:**
   - No recurring software subscriptions, paid API keys, or paid compute tiers are permitted.
2. **Zero Payment Card Requirement:**
   - Services requiring credit/debit card registration for identity verification (e.g. AWS, GCP, Azure, Modal, RunPod, Cloudflare R2, OpenRouter paid tiers) are strictly prohibited.
   - Permitted providers: Groq, Cerebras, Google AI Studio, Mistral Experiment, Kaggle, Hugging Face, OpenAlex, Europe PMC, Crossref, Wikipedia, Wikimedia, Pexels/Pixabay free tiers.
3. **No Vendor Lock-In:**
   - Every external dependency must be accessed via a swappable interface layer (e.g. LiteLLM proxy for LLMs, backend adapters for TTS and visual generation).
   - Switching a service provider must require only a single YAML configuration update.

---

## 2. Hardware & Local Resource Rules

1. **Host Specifications:**
   - Target Machine: Intel Core i5-6300U / 8 GB RAM / Intel HD 520 / Windows 11. No discrete GPU.
2. **Memory Ceiling (7.0 GB RAM Max):**
   - Steady-state system memory usage must remain strictly below **7.0 GB** to reserve at least **1.0 GB** for Windows system headroom.
3. **Mutual Exclusion Model Slot:**
   - At most **one heavy ML model** (Kokoro TTS, faster-whisper, or DeBERTa NLI) may be loaded in local RAM at any given time.
   - Controlled via a global mutual exclusion semaphore (`model_slot`).
4. **CPU & Encoding Constraints:**
   - FFmpeg encoding threads must be clamped to `-threads 2`.
   - Use Intel Quick Sync Video (`h264_qsv`) hardware acceleration where supported, falling back to lightweight software H.264 presets.
   - Standardize all video outputs to Constant Frame Rate (`30 fps CFR`, `yuv420p`, `+faststart`).

---

## 3. Remote Compute & Kaggle Execution Rules

1. **Batch Offloading Only:**
   - Heavy computation (such as image generation or batch audio synthesis) offloaded to remote ephemeral environments (Kaggle/Colab) must run strictly as **batch jobs**.
2. **No Persistent Tunnels or Servers:**
   - Persistent web servers, reverse proxies, or tunnels (e.g., ngrok, cloudflared) must **never** be run in Kaggle notebooks to comply with Kaggle terms of service and avoid account termination.
3. **Single-Job Batching Rule:**
   - Because remote jobs carry setup overhead (dataset versioning, kernel startup, dependency install, output download), **all missing scenes for a video must be submitted in a single batch job**. Never run one job per scene.
4. **Pull-Based Communication:**
   - Remote workers poll or read private transport repositories (Hugging Face Dataset repos) or Kaggle CLI calls. Inbound connections to remote workers are prohibited.

---

## 4. TTS & Audio Engineering Rules

1. **Audio as the Master Clock:**
   - Audio narration is synthesized at Step S10 before timeline assembly.
   - Measured audio duration (`samples / sample_rate * 1000`) is the **immutable master clock**. Video timelines conform to audio lengths, never the reverse.
2. **Backend Abstraction:**
   - TTS must be accessed via a swappable interface: `synthesize(scenes: list[dict], out_dir: str) -> list[str]`.
   - Primary backend: Kaggle CPU batch job (Kokoro-82M).
   - Fallback backends, in order: local `kokoro-onnx` (Apache 2.0) on CPU, then Piper (MIT) on CPU. Backend selection is automatic; the DAG must never stall on TTS availability.
3. **Local Hash Caching:**
   - Output audio filenames must follow `scene_<id>_<hash>.wav`, where `hash = md5(text + voice)[:10]`.
   - Before submitting a synthesis job, the system must check `out_dir` for existing matching WAV files and only request missing scenes.
4. **Master Audio Normalization:**
   - Final audio mix must be normalized to YouTube standards: **−14 LUFS integrated, −1.5 dBTP true peak**.
   - Narration audio ducking: Background music ducked 6–10 dB during speech segments.

---

## 5. Compliance, Copyright & YouTube Policy Rules

1. **Inauthentic Content Policy Defense:**
   - Direct, fully automated publishing loops to YouTube are forbidden.
   - A mandatory **Human Review Gate (`AWAITING_HUMAN`)** at Step S21 must require explicit operator authorization before final asset delivery.
   - Target production volume is **1–3 high-quality, verified videos per day**, prioritizing narrative variety and fact checking over generic mass output.
2. **Asset Licensing Wall:**
   - Media assets default to `gate_status = pending`. The video renderer is strictly walled off from using any asset not marked `approved`.
   - `CC-BY-NC` (Non-Commercial) and `CC-BY-SA` (ShareAlike) licenses are excluded by default.
   - HTML licensing terms for all downloaded media must be captured and stored as immutable CAS artifacts.
   - Exact attribution for `CC-BY` assets must be auto-generated into `CREDITS.md` and the video description.
3. **Non-Commercial AI Model Blocklist:**
   - The code must hard-code an explicit blocklist against non-commercial AI weights, including: `FLUX.1 [dev]`, `XTTS v2`, `F5-TTS`, `Fish Speech NC`, `MusicGen`, `Wav2Lip`, and `SD 3.5 Large`.
4. **AI Content Disclosure:**
   - `ai_generated_content: true` must be set in all project `metadata.json` deliverables.

---

## 6. System Architecture & Code Hygiene Rules

1. **Orchestrator is a State Machine (Not an Agent Loop):**
   - The pipeline is a deterministic Directed Acyclic Graph (DAG) managed by a Python state machine over SQLite WAL.
   - Agents are stateless functions `(input) -> output` operating on the central database (Blackboard Pattern). Agents never call other agents directly.
2. **Fact Grounding & Schema Validation:**
   - Every non-rhetorical factual sentence in a script must link to at least one verified `claim_id`.
   - Sentences failing grounding validation must be structurally rejected by Pydantic schemas.
   - Fact Checker (A6) and Script Writer (A4) must execute on **decorrelated model families**.
3. **Idempotency & Resumability:**
   - Every pipeline step must be indexed by `idem_key = sha256(step_name | canonical_json(inputs) | prompt_version | model_tier | code_version)`.
   - Step execution must be crash-safe; re-running a project skips already completed steps.
4. **Windows & Environment Execution:**
   - Set `PYTHONUTF8=1` and specify `encoding="utf-8"` for all subprocesses.
   - Execute subprocesses using argument lists with `shell=False` to prevent command injection.
   - Never print or leak secret keys (e.g. Kaggle API token, provider keys).

---

## 7. Technology Stack & Approved Libraries

### 7.1 Approved Python Libraries
- **Web & Control Dashboard:** `fastapi`, `uvicorn`, `htmx` (CDN hypermedia UI, no Node.js build step), `apscheduler`.
- **Database & Data Validation:** `sqlite3` (WAL mode), `sqlite-vec`, `pydantic` (v2).
- **LLM Abstraction & HTTP:** `litellm` (Python SDK), `httpx`, `trafilatura` (web text extraction).
- **Local Machine Learning (CPU Optimized):** `fastembed` (bge-small-en-v1.5 ONNX), `onnxruntime` (deberta-v3-small NLI), `kokoro` / `kokoro-onnx` (Kokoro-82M TTS), `faster-whisper` (int8).
- **Media, Audio & Rendering:** `soundfile`, `numpy`, `pyloudnorm`, `playwright` (Chromium thumbnail rendering), `pillow` (PIL), `manim` / `matplotlib`, `pyscenedetect`.
- **System Utilities & Orchestration:** `ffmpeg` (CLI system dependency), `kaggle` (Kaggle API CLI), `python-dotenv`, `keyring`, `loguru`.

### 7.2 Explicitly Prohibited Libraries & Frameworks
- **Heavy Agent Frameworks:** `langchain`, `llamaindex`, `crewai`, `autogen` (prohibited due to RAM overhead, high token consumption, and non-deterministic loops).
- **Heavy Distributed Queues:** `celery`, `redis` (prohibited; SQLite WAL provides zero-overhead transactional queuing).
- **Heavy Frontend Frameworks:** `react`, `next.js`, `vue`, `npm`/`node` build stacks (prohibited; FastAPI + htmx eliminates frontend build complexity).
- **Heavy Container Runtime Overhead:** `docker-compose` stacks (prohibited to conserve RAM for TTS and FFmpeg).
