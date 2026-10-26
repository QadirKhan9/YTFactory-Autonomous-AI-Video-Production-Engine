# ==============================================================
# YTFactory - Production Container
# Broadcast-grade autonomous video production engine
# ==============================================================

FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=7860 \
    DEBIAN_FRONTEND=noninteractive

# Install system dependencies:
# - ffmpeg: audio/video encoding, muxing, filtering
# - espeak-ng & libsndfile1: neural TTS phonemizer & audio I/O
# - curl & git: health checks and model downloads
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    espeak-ng \
    libsndfile1 \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY app.py .
COPY ytfactory/ ./ytfactory/
COPY ui/ ./ui/
COPY README.md .
COPY .env.example .

# Create persistent storage directories
# library/ is seeded at runtime by vislib.py — do not COPY from repo (it is gitignored)
RUN mkdir -p /app/outputs /app/store /app/library

# Mount points for outputs and SQLite database
VOLUME ["/app/outputs", "/app/store", "/app/library"]

# Expose Web UI port
# Port 7860 is required by Hugging Face Spaces; locally you can map it via -p 8000:7860
EXPOSE 7860

# Health check to ensure Web UI responds
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:7860/ || exit 1

# Start the Web UI
# app.py is the root-level entry point (required by Hugging Face Spaces)
CMD ["python", "app.py"]
