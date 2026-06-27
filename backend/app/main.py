"""
main.py — FastAPI application factory.

Run with:
    cd backend && uvicorn app.main:app --reload --port 8000

In production (Docker image), the React build output at ``../frontend_dist``
relative to this file is mounted at ``/`` so the same process serves both
the API and the UI on a single port.
"""

from __future__ import annotations

import logging
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .jobs import registry
from .routes import ai_master as ai_master_routes
from .routes import analyze as analyze_routes
from .routes import master as master_routes

logger = logging.getLogger(__name__)

# Resolve the static frontend directory once at import time. In dev (running
# `uvicorn app.main:app` from backend/), the directory doesn't exist and the
# SPA mount is skipped — the Vite dev server on :5173 handles the UI.
_BACKEND_DIR = Path(__file__).resolve().parent
_FRONTEND_DIST = _BACKEND_DIR / "frontend_dist"
if not _FRONTEND_DIST.exists():
    # Allow override for unusual layouts (e.g. running from a different cwd).
    alt = Path(os.environ.get("FRONTEND_DIST", str(_BACKEND_DIR / "frontend_dist")))
    if alt.exists():
        _FRONTEND_DIST = alt


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — validate environment on startup.

    - Confirms ffmpeg is on PATH (required for pydub MP3 decode).
    - Confirms the job tmp dir is writable.
    """
    settings = get_settings()
    logging.basicConfig(level=logging.INFO)

    if shutil.which("ffmpeg") is None:
        logger.warning(
            "ffmpeg not found on PATH — MP3 uploads will fail. "
            "Install ffmpeg (apt install ffmpeg / brew install ffmpeg)."
        )
    else:
        logger.info("ffmpeg OK at %s", shutil.which("ffmpeg"))

    # Touch the tmp dir so we fail fast if it's not writable.
    try:
        import os

        os.makedirs(settings.job_tmp_dir, exist_ok=True)
    except OSError as e:
        logger.error("Cannot create job_tmp_dir %s: %s", settings.job_tmp_dir, e)
        raise

    logger.info("App ready — env=%s supabase_enabled=%s", settings.app_env, settings.supabase_enabled)
    # Start the daemon sweeper that drops master-job state older than 1 hour.
    registry.start_sweeper()
    yield
    # No explicit shutdown work needed; the tmp dir persists for inspection.


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="AI Audio Mastering API",
        version="0.1.0",
        description=(
            "Automated audio mastering + analysis for AI-generated music. "
            "Phase 1: WAV/MP3 upload, Librosa analysis, pedalboard mastering chain."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Prefix all API routes with /api so they match the frontend contract
    # (frontend calls /api/bass-boost, /api/master, etc.) and the backend's
    # own status_url/download_url responses in master.py. The /api prefix is
    # NOT used in dev — the Vite proxy strips it via `rewrite: p => p.replace(/^\/api/, "")`.
    app.include_router(analyze_routes.router, prefix="/api")
    app.include_router(master_routes.router, prefix="/api")
    app.include_router(ai_master_routes.router, prefix="/api")

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok", "version": app.version}

    # ---- Production: serve the built React frontend ------------------------
    # When `frontend_dist/` exists (i.e. we're running inside the Docker
    # image or after `npm run build`), mount it as the root so the SPA loads
    # at `/`. We mount on a sub-path first to avoid the catch-all route
    # shadowing the API; the catch-all then falls back to `index.html` for
    # any non-API GET so React Router / direct URLs work.
    if _FRONTEND_DIST.is_dir():
        assets_dir = _FRONTEND_DIST / "assets"
        if assets_dir.is_dir():
            app.mount(
                "/assets",
                StaticFiles(directory=str(assets_dir)),
                name="frontend-assets",
            )

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str, request: Request):
            # If the file exists in the dist, serve it directly (favicon, etc.)
            target = (_FRONTEND_DIST / full_path).resolve()
            try:
                if target.is_file() and _FRONTEND_DIST in target.parents:
                    return FileResponse(str(target))
            except (OSError, ValueError):
                # Path resolution can raise on weird inputs; fall through.
                pass
            # Otherwise hand control to the SPA router.
            return FileResponse(str(_FRONTEND_DIST / "index.html"))

        logger.info("Serving built frontend from %s", _FRONTEND_DIST)
    else:
        logger.info(
            "No frontend_dist/ found — UI must be served separately (Vite dev server)."
        )

    return app


# Module-level instance so `uvicorn app.main:app` works without changes.
app = create_app()