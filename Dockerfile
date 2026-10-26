# ==============================================================
# YTFactory - Production Container
# Broadcast-grade autonomous video production engine
# ==============================================================

FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
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
COPY ytfactory/ ./ytfactory/
COPY ui/ ./ui/
COPY library/ ./library/
COPY README.md .
COPY .env.example .

# Create persistent storage directories
RUN mkdir -p /app/outputs /app/store

# Mount points for outputs and SQLite database
VOLUME ["/app/outputs", "/app/store", "/app/library"]

# Expose Web UI port
EXPOSE 8000

# Health check to ensure Web UI responds
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# Start the Web UI
CMD ["python", "-m", "ui.app"]
