# 🎬 YTFactory — Autonomous AI Video Production Engine

> **Broadcast-grade YouTube documentary generation from a single topic or question.**  
> Powered by an autonomous 23-step state machine, multi-provider LLM routing, stock harvesting, neural TTS, automated QC verification, and a modern dark-glass Web UI.

---

## ⚡ Quick Start (Ready in 3 Minutes)

### 1. Prerequisites
* **Python 3.10+** (Python 3.11–3.13 recommended)
* **FFmpeg**: Must be installed and accessible on your system PATH (`ffmpeg -version`)

---

### 2. Installation

1. **Clone this repository:**
   ```bash
   git clone https://github.com/QadirKhan9/YTFactory-Autonomous-AI-Video-Production-Engine.git
   cd YTFactory-Autonomous-AI-Video-Production-Engine
   ```

2. **(Optional but recommended) Create a virtual environment:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. **Install dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

4. **Set up your API keys:**
   Copy `.env.example` to `.env`:
   ```powershell
   copy .env.example .env
   ```
   Open `.env` and fill in your keys. **At least one LLM key is required** (Groq is free and recommended):
   ```env
   # ── Required: Stock media ──────────────────────────────
   PIXABAY_API_KEY=your_pixabay_key_here

   # ── LLM Providers (at least one required) ─────────────
   GROQ_API_KEY=your_groq_key_here        # Free tier at console.groq.com
   GEMINI_API_KEY=your_gemini_key_here    # Optional — console.cloud.google.com

   # ── Free GPU image gen (optional — see Kaggle section below) ──
   HF_TOKEN=hf_your_token_here            # huggingface.co/settings/tokens
   YT_GPU_BUS_REPO=your-hf-username/ytfactory-gpu-bus
   ```
   > **Tip**: Get a free Groq API key at [console.groq.com](https://console.groq.com) — no credit card needed.

---

### 3. Launch the Web UI

#### Option 1: Run directly with Python
```powershell
python -m ui.app
```

#### Option 2: Run with Docker (Zero Setup — FFmpeg bundled)
```powershell
# Build and run with Docker Compose
docker compose up -d

# Or build and run standalone
docker build -t ytfactory .
docker run -d -p 8000:8000 --env-file .env -v ${PWD}/outputs:/app/outputs -v ${PWD}/store:/app/store ytfactory
```

Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)** in your browser.

---

## 🖥️ How to Use (Two Ways)

### Option A: The Web Dashboard (Recommended)

1. **Start a New Project**:
   * Click **`＋ New Project`** in the top right.
   * Enter your topic (e.g. *"How does SpaceX land rockets?"* or *"Why do stars shine?"*).
   * Choose **Live** (full production with audio & stock video) or **Dry run** (zero-API instant validation).
   * Click **Run Pipeline**.

2. **Live Execution Timeline**:
   * Watch the pipeline automatically progress through all 23 stages: Research ➔ Scripting ➔ Voice Generation ➔ Stock Harvesting ➔ Video Render ➔ Packaging ➔ Automated QC.

3. **Review & Human Sign-off Gate (`S21`)**:
   * Review the rendered video in the interactive player.
   * Check the scene-by-scene storyboard, caption timings, and attribution credits.
   * Review the **20 Automated QC assertions** (Loudness `[-14, -13] LUFS`, black frame checks, subtitle drift, monotonic chapters).
   * Click **`✓ Approve Project`** to finalize delivery, or **`Request Changes`** to rewind to any step.

4. **Download Video Package**:
   * Switch to the **Delivery** tab to download:
     * `final.mp4` (1080p 30fps with ducked background music)
     * `thumbnail.png` (High-contrast 1280x720 cover)
     * `captions.srt` / `captions.vtt` (Word-level subtitle track)
     * `metadata.json` (Optimized YouTube title, description, tags & chapters)
     * `CREDITS.md` & `SOURCES.md` (Attribution & research documentation)

---

### Option B: Command Line (CLI)

For developers and headless servers:

```powershell
# 1. Start a live project from topic
python -m ytfactory.cli run "Why the Roman Empire Fell" --live

# 2. Run a dry run (zero API calls)
python -m ytfactory.cli run "Quantum Computing Explained" --dry-run

# 3. Resume a paused or rejected project
python -m ytfactory.cli resume why-the-roman-empire-fell-481d06d6

# 4. Invalidate and re-run from a specific step (e.g. S16 video render)
python -m ytfactory.cli rerun why-the-roman-empire-fell-481d06d6 --from S16 --live

# 5. Approve project at the S21 human gate
python -m ytfactory.cli approve why-the-roman-empire-fell-481d06d6

# 6. List all projects and current status
python -m ytfactory.cli list
```

---

## 🏛️ Pipeline Architecture

| Phase | Stages | What Happens |
| :--- | :--- | :--- |
| **Phase 1: Research** | `S00`–`S04` | Topic intake, web harvesting, credibility scoring & claim indexing |
| **Phase 2: Script** | `S05`–`S08` | Outline, narrative draft, multi-persona critique & NLI fact-checking |
| **Phase 3: Audio** | `S10`, `S17` | Voice synthesis (Kokoro/Piper), Whisper forced alignment, EBU loudness & `.srt` captions |
| **Phase 4: Visuals** | `S09`, `S11`–`S16`| Scene plan, Pixabay stock harvesting, license gate, sidechain music ducking & FFmpeg render |
| **Phase 5: Packaging**| `S18` | Thumbnail synthesis, SEO description, tags & timestamped chapters |
| **Phase 6: QC & Delivery**| `S19`–`S22` | 20 Automated QC assertions, AI editorial audit, S21 Human Gate & final sealed package |

---

## 🖼️ Free GPU Image Generation (Kaggle + FLUX.1-schnell)

By default S13 uses Pixabay stock images. For **AI-generated, scene-specific visuals** at zero cost, YTFactory integrates a pull-based GPU bus that offloads rendering to a **free Kaggle T4×2 session** running FLUX.1-schnell (Apache 2.0).

### How it works

```
Your laptop                   Hugging Face (message bus)      Kaggle GPU session
──────────────                ──────────────────────────      ──────────────────
rerun --from S13 ──upload──▶  inbox/<batch_id>.json
                              inbox/<batch>.claim       ◀──── gpu_worker.py claims
                              outbox/<job_id>.png       ◀──── renders FLUX scenes
laptop reaps + sha256-verifies ─────────────────────────────▶ pipeline continues
```

> Kaggle sessions cannot accept inbound connections — the worker *polls* the bus (pull-based). The laptop verifies sha256 of every image before use. If the worker is unavailable, S13 silently falls back to library abstracts — **the pipeline never blocks.**

### One-time setup

1. **Create a Hugging Face account + write token** → [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)  
   Set `HF_TOKEN` in your `.env`.

2. **Accept the FLUX.1-schnell license** (one click):  
   [huggingface.co/black-forest-labs/FLUX.1-schnell](https://huggingface.co/black-forest-labs/FLUX.1-schnell) → *Agree and access*

3. **Create a Kaggle account** → [kaggle.com](https://www.kaggle.com)  
   Download your API token to `%USERPROFILE%\.kaggle\kaggle.json`.

4. **Edit the kernel owner** in [`kaggle/gpu_worker/kernel-metadata.json`](kaggle/gpu_worker/kernel-metadata.json):
   ```json
   { "id": "<your-kaggle-username>/flux-gpu-worker" }
   ```

5. **Set the bus repo** in your `.env`:
   ```env
   YT_GPU_BUS_REPO=your-hf-username/ytfactory-gpu-bus
   ```
   The private HF dataset repo is auto-created on first use.

### Running the GPU worker

```powershell
# Push the kernel — this IS the run (starts a T4×2 session immediately)
kaggle kernels push -p kaggle/gpu_worker --accelerator gpuT4x2

# Check session status
kaggle kernels status <your-kaggle-username>/flux-gpu-worker

# Follow logs live
kaggle kernels logs -f <your-kaggle-username>/flux-gpu-worker
```

### Trigger FLUX image generation

```powershell
# Re-run S13 with GPU mode enabled (45-min timeout for GGUF model download)
python -m ytfactory.cli rerun <project-slug> --from S13 --live `
    --set image_gen=gpu --set gpu_timeout_s=2700
```

Watch for `S13 gpu batch <id>: 7/7 verified` in the pipeline notes. GPU-generated images appear in `outputs/<slug>/assets/` (0.5–1.4 MB vs ~35 KB stock images) and are credited as `apache-2.0` in `CREDITS.md`.

### Local dry run (no Kaggle needed)

```powershell
# Terminal 1 — start the local GPU bus simulator
python -m ytfactory.workers.gpubus --watch

# Terminal 2 — trigger S13
python -m ytfactory.cli rerun <slug> --from S13 --live --set image_gen=gpu
```

### Quota & cost

| Resource | Cost |
|---|---|
| Kaggle GPU (T4×2) | **Free** — ~30 h/week quota, worker self-limits to 55 min/session |
| Hugging Face dataset bus | **Free** — private dataset, this traffic is well within limits |
| FLUX.1-schnell model | **Apache 2.0** — commercially usable, no attribution required |

> 📖 Full technical reference: [`docs/kaggle-flux.md`](docs/kaggle-flux.md)

---

## 🔒 Security & Privacy

* **Localhost by default:** Web dashboard is bound to `127.0.0.1:8000` (safe from external network access).
* **Zero credential leaks:** `.env`, databases, and media assets are ignored in `.gitignore`.
* **Zero shell injection:** Subprocess calls pass explicit parameter lists without `shell=True`.
* **SQL Injection proof:** All database operations use SQLite parameterized queries (`?`).
