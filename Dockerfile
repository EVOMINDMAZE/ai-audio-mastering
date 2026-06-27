# =============================================================================
# AI Audio Mastering — Hugging Face Spaces production image
# =============================================================================
# Single-image build that runs both the FastAPI backend (uvicorn) and serves
# the built React frontend as static assets. Exposes port 7860 (HF Spaces
# default). Optimized for size: multi-stage so the final image doesn't ship
# node_modules or the build toolchain.
# =============================================================================

# ---- Stage 1: build the React frontend ---------------------------------------
FROM node:20-bookworm-slim AS frontend-builder
WORKDIR /build

# Install only what's needed for the build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund --prefer-offline

COPY frontend/ ./
RUN npm run build

# ---- Stage 2: production runtime ---------------------------------------------
FROM python:3.11-slim AS runtime

# System deps:
#   ffmpeg       — pydub MP3 decoding
#   libsndfile1  — soundfile backend
#   build-essential — pedalboard / numpy wheels (fallback compile)
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Working directory for backend
WORKDIR /app/backend

# Python deps (cached layer)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

# Backend source
COPY backend/ /app/backend/

# Frontend dist from stage 1
COPY --from=frontend-builder /build/dist /app/backend/frontend_dist

# Runtime config. Defaults keep the image lean for the free-tier Render
# deploy (512 MB RAM cap). OMP/MKL/OPENBLAS thread counts are forced to 1
# so a single render doesn't fan out into multiple BLAS threads that each
# allocate their own working buffers and push us over the OOM threshold.
#
# FRONTEND_DIST override compensates for the wrong default
# _BACKEND_DIR = Path(__file__).resolve().parent in backend/app/main.py:35
# (one level too shallow). Without it, _FRONTEND_DIST resolves to the
# non-existent /app/backend/app/frontend_dist and the SPA mount is
# silently skipped → / returns 404.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=7860 \
    JOB_TMP_DIR=/tmp/audio_jobs \
    APP_ENV=production \
    CORS_ORIGINS='["*"]' \
    FRONTEND_DIST=/app/backend/frontend_dist \
    MAX_RENDER_WORKERS=1 \
    MAX_UPLOAD_MB=25 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1

# HF Spaces container orchestration respects $PORT; default to 7860 if absent.
EXPOSE 7860

# Healthcheck — `/health` is the liveness endpoint registered in main.py
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:'+__import__('os').environ.get('PORT','7860')+'/health',timeout=5).status==200 else 1)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --workers 1"]