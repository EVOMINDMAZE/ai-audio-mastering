"""
POST /reference-master — Matchering-based mastering.
Accepts (target: UploadFile, reference: UploadFile) as multipart fields,
runs `audio_engine.reference_master()` in a background thread, returns job_id.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse

from .. import audio_engine
from ..jobs import registry
from .. import storage
from .analyze import _save_upload

logger = logging.getLogger(__name__)
router = APIRouter(tags=["reference-master"])

_MAX_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024


def _render_reference(job_id: str, target_path: str, reference_path: str, job_dir: Path) -> None:
    """Background render for a Matchering job."""
    # ---- Genre classification (runs on the target, NOT the reference) -----
    # Run in the background thread so the POST response stays fast. A failure
    # here must NOT abort the reference render — it's purely informational.
    try:
        genre = audio_engine.classify_genre(target_path)
    except Exception as e:
        logger.warning("reference-master: genre classification failed: %s", e)
        genre = {"label": None, "score": None, "warning": str(e)}

    job = registry.get(job_id)
    if job is not None:
        job.metadata["genre"] = genre

    out_path = job_dir / "reference_mastered.wav"
    try:
        metrics = audio_engine.reference_master(
            target=target_path,
            reference=reference_path,
            output=str(out_path),
        )
    except Exception as e:
        logger.exception("reference-master render failed for job_id=%s", job_id)
        registry.finish(job_id, error=f"{type(e).__name__}: {e}")
        return
    registry.finish(job_id)


@router.post("/reference-master")
async def reference_master_endpoint(
    target: UploadFile = File(...),
    reference: UploadFile = File(...),
) -> dict:
    """Accept target + reference audio files. Run Matchering in background."""
    if not target.filename:
        raise HTTPException(status_code=400, detail="Missing target filename.")
    if not reference.filename:
        raise HTTPException(status_code=400, detail="Missing reference filename.")

    job_id = uuid.uuid4().hex[:12]
    job_dir = storage.get_job_dir(job_id)
    logger.info("reference-master: job_id=%s target=%s reference=%s",
                job_id, target.filename, reference.filename)

    target_path = _save_upload(target, job_dir)
    reference_path = _save_upload(reference, job_dir)
    await target.close()
    await reference.close()

    # Register the job BEFORE starting the render so status polls find it.
    registry.create(job_id=job_id, filename=target.filename)

    threading.Thread(
        target=_render_reference,
        args=(job_id, str(target_path), str(reference_path), job_dir),
        daemon=True,
    ).start()

    return {
        "job_id": job_id,
        "status": "queued",
        "status_url": f"/api/reference-master/{job_id}/status",
        "download_url": f"/api/reference-master/{job_id}/download",
    }


@router.get("/reference-master/{job_id}/status")
async def reference_master_status(job_id: str) -> dict:
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired.")
    return job.as_dict()


@router.get("/reference-master/{job_id}/download")
async def reference_master_download(job_id: str, request: Request):
    """Stream the reference-mastered WAV. Same Range-support pattern as ai-master."""
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired.")
    if job.status != "ready":
        raise HTTPException(
            status_code=409, detail=f"Job is not ready (status={job.status})."
        )
    wav_path = storage.get_job_dir(job_id) / "reference_mastered.wav"
    if not wav_path.exists():
        raise HTTPException(status_code=404, detail="Rendered file missing.")

    file_size = wav_path.stat().st_size
    range_header = request.headers.get("range")
    if range_header:
        try:
            units, _, rng = range_header.partition("=")
            if units.strip() != "bytes":
                raise ValueError
            start_s, _, end_s = rng.partition("-")
            start = int(start_s)
            end = int(end_s) if end_s else file_size - 1
            if start > end or start < 0 or end >= file_size:
                raise ValueError
        except ValueError:
            raise HTTPException(status_code=416, detail="Invalid Range header.")

        length = end - start + 1
        with open(wav_path, "rb") as f:
            f.seek(start)
            data = f.read(length)
        return Response(
            content=data,
            status_code=206,
            headers={
                "Content-Type": "audio/wav",
                "Content-Length": str(length),
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Disposition": (
                    f'attachment; filename="{Path(job.filename).stem or "audio"}-reference-mastered.wav"'
                ),
            },
        )

    return FileResponse(
        path=str(wav_path),
        media_type="audio/wav",
        filename=f"{Path(job.filename).stem or 'audio'}-reference-mastered.wav",
    )