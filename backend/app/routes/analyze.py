"""
POST /analyze — extract metadata from an uploaded audio file.

Accepts WAV / MP3 / FLAC / OGG via multipart upload. Returns the AnalysisResult
Pydantic model defined in app.models. MP3 inputs are converted to WAV on disk
before being passed to the audio engine (pydub delegates to system ffmpeg).
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from .. import audio_engine
from ..models import AnalysisResult
from ..storage import get_job_dir, safe_filename

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analyze"])

# Upload size cap. Tuned for Render free-tier 512 MB RAM:
#   25 MB ≈ 3 minutes of 44.1 kHz / 16-bit stereo WAV.
# Larger files push pedalboard's intermediate buffers past the OOM threshold
# even with MAX_RENDER_WORKERS=1. Override via MAX_UPLOAD_MB env var.
_MAX_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024


def _save_upload(upload: UploadFile, job_dir: Path) -> Path:
    """Stream an UploadFile to disk under ``job_dir``. Returns the saved path.

    MP3 inputs are converted to WAV (16-bit, 44.1kHz) via pydub so the
    downstream audio_engine.load_audio call is uniform across formats.
    """
    raw_path = job_dir / safe_filename(upload.filename or "input.audio")
    bytes_written = 0
    chunk_size = 1024 * 1024  # 1 MB

    with raw_path.open("wb") as fh:
        while True:
            chunk = upload.file.read(chunk_size)
            if not chunk:
                break
            bytes_written += len(chunk)
            if bytes_written > _MAX_BYTES:
                fh.close()
                raw_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"Upload exceeds {_MAX_BYTES // (1024 * 1024)} MB limit.",
                )
            fh.write(chunk)

    # If MP3, convert to WAV so the engine gets a uniform input format.
    if raw_path.suffix.lower() == ".mp3":
        try:
            from pydub import AudioSegment  # lazy import — pydub is heavy
            wav_path = raw_path.with_suffix(".wav")
            AudioSegment.from_mp3(raw_path).export(wav_path, format="wav")
            raw_path.unlink(missing_ok=True)
            return wav_path
        except Exception as e:  # pragma: no cover — depends on system ffmpeg
            raise HTTPException(
                status_code=400,
                detail=f"Failed to decode MP3 — is ffmpeg installed? ({e})",
            ) from e

    return raw_path


@router.post("/analyze", response_model=AnalysisResult)
async def analyze_endpoint(file: UploadFile = File(...)) -> AnalysisResult:
    """Accept an audio upload and return its analysis payload."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename.")

    job_id = uuid.uuid4().hex[:12]
    job_dir = get_job_dir(job_id)
    logger.info("analyze: job_id=%s filename=%s", job_id, file.filename)

    saved_path = _save_upload(file, job_dir)
    try:
        metrics = audio_engine.analyze(str(saved_path))
    except Exception as e:
        logger.exception("analyze failed for job_id=%s", job_id)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}") from e
    finally:
        await file.close()

    # Persist a copy of the analysis JSON for debugging / future Supabase sync.
    dump_path = job_dir / "analysis.json"
    try:
        dump_path.write_text(json.dumps(metrics, indent=2))
    except OSError:
        logger.warning("Could not persist analysis.json for job_id=%s", job_id)

    return AnalysisResult(job_id=job_id, **metrics)