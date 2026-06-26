"""
storage.py — job directory helpers + Supabase Storage wiring.

The MVP runs entirely on local /tmp; the Supabase client call is behind a
feature flag so the backend is fully functional without external creds. When
``settings.supabase_enabled`` is False, ``upload_to_supabase`` returns a
local file:// URL so the route layer's response shape is stable.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from .config import get_settings

logger = logging.getLogger(__name__)


def get_job_dir(job_id: str) -> Path:
    """Return the per-job working directory, creating it if needed.

    Layout:  {JOB_TMP_DIR}/{job_id}/input.{ext}  (raw upload)
             {JOB_TMP_DIR}/{job_id}/mastered.wav (mastered output)
             {JOB_TMP_DIR}/{job_id}/analysis.json (analysis dump)
    """
    settings = get_settings()
    job_dir = Path(settings.job_tmp_dir) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def upload_to_supabase(local_path: str, dest_path: Optional[str] = None) -> str:
    """Upload a file to Supabase Storage and return a public-ish URL.

    When Supabase is disabled (default), this logs the intent and returns a
    ``file://`` URL pointing at the local copy — the API contract stays
    unchanged whether or not Supabase is wired up.
    """
    settings = get_settings()
    if not settings.supabase_enabled:
        logger.info(
            "storage.upload_to_supabase: SUPABASE_ENABLED=false; returning local file:// URL for %s",
            local_path,
        )
        return Path(local_path).resolve().as_uri()

    if dest_path is None:
        dest_path = os.path.basename(local_path)

    # Lazy import — keeps `import storage` cheap when Supabase is disabled.
    from supabase import create_client

    client = create_client(settings.supabase_url, settings.supabase_service_key)
    bucket = settings.supabase_bucket

    with open(local_path, "rb") as fh:
        client.storage.from_(bucket).upload(
            file=fh,
            path=dest_path,
            file_options={"content-type": "audio/wav", "upsert": "true"},
        )

    public_url = client.storage.from_(bucket).get_public_url(dest_path)
    return public_url


def master_download_url(job_id: str) -> str:
    """Build a local /tmp path URI for the mastered WAV.

    The FastAPI GET /download/{job_id} route serves this file directly during
    the MVP; once Supabase is enabled the route will return the remote URL
    instead.
    """
    settings = get_settings()
    wav_path = Path(settings.job_tmp_dir) / job_id / "mastered.wav"
    return wav_path.resolve().as_uri()


def safe_filename(name: str) -> str:
    """Sanitize a user-supplied filename to a safe basename."""
    # Strip directory components and characters that could escape the job dir.
    basename = os.path.basename(name or "input.wav")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._")
    cleaned = "".join(c if c in allowed else "_" for c in basename)
    return cleaned or "input.wav"


def encode_path_for_url(path: str) -> str:
    """Percent-encode a local path so it's safe in JSON responses."""
    return quote(path, safe="/:")