# 🎬 YTFactory — Complete Beginner Setup Guide

> **No coding experience required.** Follow every step in order and you will have YTFactory running on your computer.
> ⏱️ Estimated time: **20–30 minutes** (most of that is waiting for downloads).

---

## 📋 Table of Contents

1. [Install Python](#step-1-install-python)
2. [Install FFmpeg](#step-2-install-ffmpeg)
3. [Install Git](#step-3-install-git)
4. [Download YTFactory](#step-4-download-ytfactory)
5. [Install YTFactory Dependencies](#step-5-install-ytfactory-dependencies)
6. [Get Your Free API Keys](#step-6-get-your-free-api-keys)
7. [Configure YTFactory](#step-7-configure-ytfactory)
8. [Run YTFactory](#step-8-run-ytfactory)
9. [Make Your First Video](#step-9-make-your-first-video)
10. [(Optional) Enable Free AI Image Generation via Kaggle](#step-10-optional-free-ai-images-via-kaggle--flux)

---

## Step 1: Install Python

Python is the programming language YTFactory is built with. You need version **3.11 or newer**.

1. Go to -> **https://www.python.org/downloads/**
2. Click the big yellow **"Download Python 3.x.x"** button.
3. Run the installer.
4. **IMPORTANT**: On the first installer screen, **check the box that says "Add Python to PATH"** before clicking Install.
5. Click **"Install Now"** and wait for it to finish.

**Verify it worked:**
Open **Command Prompt** (press `Windows + R`, type `cmd`, press Enter) and type:
```
python --version
```
You should see something like `Python 3.11.9`. ✅

---

## Step 2: Install FFmpeg

FFmpeg is the video-processing engine YTFactory uses to render your videos. It is free and open source.

1. Go to -> **https://ffmpeg.org/download.html**
2. Click **Windows** -> click **"Windows builds from gyan.dev"**
3. Under **Release builds**, download the file ending in **`-essentials_build.zip`**
4. Extract (unzip) the downloaded file to `C:\ffmpeg`
5. Add FFmpeg to your system PATH:
   - Press `Windows + S`, search **"Edit the system environment variables"**, open it
   - Click **"Environment Variables"**
   - Under **"System variables"**, find **"Path"**, click it, then click **"Edit"**
   - Click **"New"** and type: `C:\ffmpeg\bin`
   - Click **OK** on all windows

**Verify it worked:**
Open a **new** Command Prompt and type:
```
ffmpeg -version
```
You should see version info. ✅

> **Easier alternative**: If you have Chocolatey, run `choco install ffmpeg` in an Administrator Command Prompt.

---

## Step 3: Install Git

Git is how you download YTFactory from GitHub.

1. Go to -> **https://git-scm.com/download/win**
2. Download and run the installer. Click **Next** on every screen — the defaults are fine.

**Verify it worked:**
Open a **new** Command Prompt and type:
```
git --version
```
You should see `git version 2.x.x`. ✅

---

## Step 4: Download YTFactory

1. Open **Command Prompt**.
2. Navigate to where you want to save YTFactory. For example your Desktop:
   ```
   cd Desktop
   ```
3. Run this command to download YTFactory:
   ```
   git clone https://github.com/QadirKhan9/YTFactory-Autonomous-AI-Video-Production-Engine.git
   ```
4. Enter the downloaded folder:
   ```
   cd YTFactory-Autonomous-AI-Video-Production-Engine
   ```

You now have all the code on your computer. ✅

---

## Step 5: Install YTFactory Dependencies

Still in the same Command Prompt window:

1. Create a virtual environment (keeps everything tidy):
   ```
   python -m venv .venv
   .venv\Scripts\activate
   ```
   You should now see `(.venv)` at the start of your prompt line.

2. Install all dependencies:
   ```
   pip install -r requirements.txt
   ```
   This may take **3–5 minutes**. Done when you see no red error messages. ✅

---

## Step 6: Get Your Free API Keys

### 6a. Pixabay API Key (Required — stock images & video)

1. Go to -> **https://pixabay.com/api/docs/**
2. Create a free account and log in.
3. Your API key is shown on that page. Copy it.

---

### 6b. Groq API Key (Required — the AI brain, completely free)

1. Go to -> **https://console.groq.com**
2. Sign up for a free account (no credit card needed).
3. Click **"API Keys"** in the left sidebar.
4. Click **"Create API Key"**, name it `ytfactory`, click Create.
5. **Copy the key now** — it starts with `gsk_...`. You will not be able to see it again!

---

### 6c. Gemini API Key (Optional — backup AI)

1. Go to -> **https://aistudio.google.com/apikey**
2. Sign in with your Google account.
3. Click **"Create API key"** and copy it.

---

## Step 7: Configure YTFactory

1. In your Command Prompt (inside the YTFactory folder), run:
   ```
   copy .env.example .env
   ```

2. Open the `.env` file in Notepad:
   ```
   notepad .env
   ```

3. Replace the placeholder text with your real keys:
   ```
   PIXABAY_API_KEY=paste_your_pixabay_key_here
   GROQ_API_KEY=paste_your_groq_key_here
   GEMINI_API_KEY=paste_your_gemini_key_or_leave_blank
   ```

4. Press **Ctrl + S** to save, then close Notepad. ✅

---

## Step 8: Run YTFactory

1. Make sure `(.venv)` is active in your Command Prompt. If you opened a new window:
   ```
   cd Desktop\YTFactory-Autonomous-AI-Video-Production-Engine
   .venv\Scripts\activate
   ```

2. Start the server:
   ```
   python -m ui.app
   ```

3. You should see:
   ```
   INFO:     Uvicorn running on http://127.0.0.1:8000
   ```

4. Open your browser: **http://127.0.0.1:8000**

🎉 **YTFactory is running!**

> Leave the Command Prompt window open while using YTFactory. Closing it stops the server.

---

## Step 9: Make Your First Video

1. Click **New Project** (top right of the dashboard).
2. Type a topic — for example: `Why did the Roman Empire fall?`
3. Choose **Dry Run** for your first test (runs instantly, no API calls used).
4. Click **Run Pipeline**.
5. Watch the 23-step pipeline run automatically!
6. At **S21 — Human Review**, click **Approve Project** to finalize.

Your video package (.mp4, thumbnail, captions, metadata) will appear in the **Delivery** tab. ✅

---

## Step 10: (Optional) Free AI Images via Kaggle + FLUX

By default YTFactory uses Pixabay stock footage. You can upgrade to AI-generated images for free using Kaggle's GPU quota and FLUX.1-schnell (Apache 2.0 — commercially usable).

> **Note**: If this is too complex right now, skip it. YTFactory works perfectly without it.

### 10a. Create a Hugging Face account

1. Go to -> **https://huggingface.co/join** and sign up.
2. Go to **Settings -> Access Tokens -> New token**.
3. Choose **Write**, name it `ytfactory`, copy the token (starts with `hf_...`).

### 10b. Accept the FLUX model license (one click)

1. Visit -> **https://huggingface.co/black-forest-labs/FLUX.1-schnell**
2. Click **"Agree and access repository"**.

### 10c. Create a Kaggle account

1. Go to -> **https://www.kaggle.com** and sign up.
2. Click your profile picture -> **Settings -> Account -> Create New API Token**.
3. This downloads `kaggle.json`. Move it to:
   ```
   C:\Users\<YourWindowsUsername>\.kaggle\kaggle.json
   ```
   (Create the `.kaggle` folder if it does not exist.)

### 10d. Install the Kaggle CLI

In your Command Prompt (with `.venv` active):
```
pip install kaggle
```

### 10e. Edit the Kaggle kernel config

Open `kaggle\gpu_worker\kernel-metadata.json` in Notepad.
Change the `id` field to **your** Kaggle username:
```json
{
  "id": "your-kaggle-username/flux-gpu-worker"
}
```
Save and close.

### 10f. Add GPU keys to your `.env`

Open `.env` in Notepad again and add these two lines:
```
HF_TOKEN=hf_your_token_here
YT_GPU_BUS_REPO=your-huggingface-username/ytfactory-gpu-bus
```
Save and close.

### 10g. Start the Kaggle GPU session

```
kaggle kernels push -p kaggle\gpu_worker --accelerator gpuT4x2
```

Check its status:
```
kaggle kernels status your-kaggle-username/flux-gpu-worker
```
Wait until you see `RUNNING`.

### 10h. Run FLUX image generation

```
python -m ytfactory.cli rerun <your-project-slug> --from S13 --live --set image_gen=gpu --set gpu_timeout_s=2700
```

Replace `<your-project-slug>` with the slug shown in the dashboard URL.
Watch for `S13 gpu batch: 7/7 verified`. Your scenes now have real AI images! 🖼️

### Kaggle limits at a glance

| Resource | Limit |
|---|---|
| GPU quota | ~30 h free/week |
| Max simultaneous sessions | 2 |
| Model | FLUX.1-schnell (Apache 2.0, free commercial use) |

> If the Kaggle session is unavailable, YTFactory automatically falls back to Pixabay stock images — the video still gets made.

---

## 🆘 Common Problems

| Problem | Fix |
|---|---|
| `python` not recognized | Re-install Python and tick **"Add to PATH"** on the first screen |
| `ffmpeg` not recognized | Restart Command Prompt after adding FFmpeg to PATH |
| `pip install` errors | Make sure `(.venv)` is shown in your prompt |
| Browser shows "This site can't be reached" | The server isn't running — go back to Step 8 |
| API key errors | Re-open `.env` in Notepad and check for extra spaces around the `=` sign |
| Groq rate limit error | Wait 1 minute, or add a Gemini key as a backup |
| Kaggle push fails: "Maximum 2 sessions" | Wait for an old session to finish, then push again |

---

## 🔄 Starting YTFactory Next Time

You only need to install things once. From next time:

```
cd Desktop\YTFactory-Autonomous-AI-Video-Production-Engine
.venv\Scripts\activate
python -m ui.app
```

Then open **http://127.0.0.1:8000** in your browser. That is it!

---

*Need help? Open an issue at https://github.com/QadirKhan9/YTFactory-Autonomous-AI-Video-Production-Engine/issues*
