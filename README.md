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

## 🔒 Security & Privacy

* **Localhost by default:** Web dashboard is bound to `127.0.0.1:8000` (safe from external network access).
* **Zero credential leaks:** `.env`, databases, and media assets are ignored in `.gitignore`.
* **Zero shell injection:** Subprocess calls pass explicit parameter lists without `shell=True`.
* **SQL Injection proof:** All database operations use SQLite parameterized queries (`?`).
