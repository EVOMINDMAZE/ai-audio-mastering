"""
POST /master   — accept upload, queue render job, return job_id immediately.
GET  /master/{job_id}/status — return current state (variants ready so far).
GET  /download/{job_id}/{preset_id} — stream the mastered WAV for a preset.

The render is fully asynchronous: POST returns within ~1-3s with the
job_id; the frontend polls /status until all 6 presets are ready. This
keeps the response time well under any upstream proxy timeout, even for
long audio files.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse

from .. import audio_engine, storage
from ..config import get_settings
from ..jobs import registry

logger = logging.getLogger(__name__)
router = APIRouter(tags=["master"])

# Reuse the analyze route's uploader for upload-then-convert logic.
from .analyze import _save_upload  # noqa: E402  (intentional intra-package reuse)


# How many presets to render concurrently.
#
# Render free-tier web services are limited to 512 MB RAM. Each render of a
# 3-min 44.1 kHz stereo track holds ~250 MB working set (input float32 +
# pedalboard intermediates + output). With 3 workers that's ~750 MB peak
# which exceeds the limit and triggers an OOM kill.
#
# Reducing to 1 worker keeps peak memory under control. Total throughput is
# not significantly worse — DSP is CPU-bound and a single render saturates
# the free-tier vCPU already; the other workers would just contend for the
# same cores. The bigger win is deterministic memory headroom.
_MAX_RENDER_WORKERS = int(os.environ.get("MAX_RENDER_WORKERS", "1"))

# One global thread pool, shared across all jobs. Avoids spinning a new pool
# per request and bounds total CPU usage on the server.
_POOL: ThreadPoolExecutor | None = None
_POOL_LOCK = threading.Lock()


def _get_pool() -> ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = ThreadPoolExecutor(max_workers=_MAX_RENDER_WORKERS)
    return _POOL


def _mastered_path(job_dir: Path, preset_id: str) -> Path:
    return job_dir / f"mastered_{preset_id}.wav"


def _render_one(
    saved_path: str,
    out_path: str,
    params: dict,
) -> dict:
    """Render one preset in a worker thread. Returns the metrics dict."""
    dsp_params = {k: v for k, v in params.items() if k not in {"label", "description"}}
    return audio_engine.master(saved_path, out_path, **dsp_params)


def _render_all(job_id: str, saved_path_str: str, job_dir: Path) -> None:
    """Background render — runs in a dedicated thread per job."""
    preset_items = list(audio_engine.PRESETS.items())
    pool = _get_pool()
    futures = {
        preset_id: pool.submit(
            _render_one,
            saved_path_str,
            str(_mastered_path(job_dir, preset_id)),
            params,
        )
        for preset_id, params in preset_items
    }
    # Walk in declared order so the status response grows predictably.
    for preset_id, params in preset_items:
        try:
            metrics = futures[preset_id].result()
        except Exception as e:
            logger.exception("render failed in job_id=%s preset=%s", job_id, preset_id)
            registry.finish(job_id, error=f"{type(e).__name__}: {e}")
            return
        registry.add_variant(
            job_id,
            {
                "preset_id": preset_id,
                "label": params["label"],
                "description": params["description"],
                "download_url": f"/api/download/{job_id}/{preset_id}",
                "metrics": metrics,
            },
        )
    registry.finish(job_id)


@router.post("/master")
async def master_endpoint(file: UploadFile = File(...)) -> dict:
    """Accept an audio upload, queue a render job, return job_id immediately."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename.")

    job_id = uuid.uuid4().hex[:12]
    job_dir = storage.get_job_dir(job_id)
    logger.info("master: job_id=%s filename=%s", job_id, file.filename)

    saved_path = _save_upload(file, job_dir)
    await file.close()

    registry.create(job_id=job_id, filename=file.filename)

    # Fire-and-forget the background render. The thread outlives the request.
    threading.Thread(
        target=_render_all,
        args=(job_id, str(saved_path), job_dir),
        daemon=True,
    ).start()

    return {
        "job_id": job_id,
        "status": "queued",
        "status_url": f"/api/master/{job_id}/status",
    }


@router.get("/master/{job_id}/status")
async def master_status(job_id: str) -> dict:
    """Return the current state of a previously-submitted master job."""
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown job_id '{job_id}'")
    return job.as_dict()


@router.get("/download/{job_id}/{preset_id}")
async def download_mastered_preset(job_id: str, preset_id: str, request: Request):
    """Stream the mastered WAV for a single preset of a previous job."""
    if preset_id not in audio_engine.PRESETS:
        raise HTTPException(status_code=404, detail=f"Unknown preset '{preset_id}'")
    settings = get_settings()
    wav_path = _mastered_path(Path(settings.job_tmp_dir) / job_id, preset_id)
    if not wav_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"No mastered file for job_id={job_id} preset={preset_id}",
        )
    
    range_header = request.headers.get("Range")
    if range_header:
        file_size = wav_path.stat().st_size
        try:
            byte_range = range_header.replace("bytes=", "").split("-")
            start = int(byte_range[0]) if byte_range[0] else 0
            end = int(byte_range[1]) if len(byte_range) > 1 and byte_range[1] else file_size - 1
            if start >= file_size or end >= file_size or start > end:
                raise ValueError()
        except ValueError:
            raise HTTPException(status_code=416, detail="Requested Range Not Satisfiable")
            
        chunk_size = end - start + 1
        with open(wav_path, "rb") as f:
            f.seek(start)
            data = f.read(chunk_size)
            
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_size),
            "Content-Type": "audio/wav",
            "Content-Disposition": f'attachment; filename="{job_id}_{preset_id}_mastered.wav"',
        }
        return Response(content=data, status_code=206, headers=headers)

    return FileResponse(
        path=str(wav_path),
        media_type="audio/wav",
        filename=f"{job_id}_{preset_id}_mastered.wav",
    )


@router.post("/bass-boost")
async def bass_boost_endpoint(file: UploadFile = File(...)) -> dict:
    """Accept an audio upload, queue a bass-boost render, return job_id immediately.

    Synchronous download of the mastered WAV can blow past the upstream
    proxy's 30-second timeout on a long track, which manifests as a
    confusing ``Failed to fetch`` error in the browser. So we use the same
    async job pattern as ``/master`` — return ``job_id`` within ~1s and let
    the frontend poll ``/bass-boost/{job_id}/status``, then hit
    ``/bass-boost/{job_id}/download`` for the WAV.

    The DSP parameters come from ``audio_engine.PRESETS["bass_boosted"]`` so
    any future tweak to that preset propagates here automatically.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename.")

    job_id = uuid.uuid4().hex[:12]
    job_dir = storage.get_job_dir(job_id)
    logger.info("bass-boost: job_id=%s filename=%s", job_id, file.filename)

    saved_path = _save_upload(file, job_dir)
    await file.close()

    registry.create(job_id=job_id, filename=file.filename)
    # Register a single "variant" up front so the status response carries the
    # download URL even before the render finishes. Keeps the frontend contract
    # identical to the multi-preset flow.
    preset_params = audio_engine.PRESETS["bass_boosted"]
    registry.add_variant(
        job_id,
        {
            "preset_id": "bass_boosted",
            "label": preset_params["label"],
            "description": preset_params["description"],
            "download_url": f"/api/bass-boost/{job_id}/download",
            "metrics": None,
        },
    )

    # Fire-and-forget the background render.
    threading.Thread(
        target=_render_bass_boost,
        args=(job_id, str(saved_path), job_dir),
        daemon=True,
    ).start()

    return {
        "job_id": job_id,
        "status": "queued",
        "status_url": f"/api/bass-boost/{job_id}/status",
    }


def _render_bass_boost(job_id: str, saved_path_str: str, job_dir: Path) -> None:
    """Background bass-boost render — mirrors _render_all but single preset."""
    preset_params = dict(audio_engine.PRESETS["bass_boosted"])
    dsp_params = {k: v for k, v in preset_params.items() if k not in {"label", "description"}}
    out_path = job_dir / "bass_boosted.wav"
    try:
        metrics = audio_engine.master(saved_path_str, str(out_path), **dsp_params)
    except Exception as e:
        logger.exception("bass-boost render failed: job_id=%s", job_id)
        registry.finish(job_id, error=f"{type(e).__name__}: {e}")
        return

    # Replace the placeholder variant with one carrying real metrics.
    job = registry.get(job_id)
    if job and job.variants:
        job.variants[0]["metrics"] = metrics
    registry.finish(job_id)


@router.get("/bass-boost/{job_id}/status")
async def bass_boost_status(job_id: str) -> dict:
    """Return the current state of a bass-boost job."""
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired.")
    return job.as_dict()


@router.get("/bass-boost/{job_id}/download")
async def bass_boost_download(job_id: str, request: Request):
    """Stream the bass-boosted WAV for a completed job."""
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired.")
    if job.status != "ready":
        raise HTTPException(status_code=409, detail=f"Job is not ready (status={job.status}).")
    wav_path = storage.get_job_dir(job_id) / "bass_boosted.wav"
    if not wav_path.exists():
        raise HTTPException(status_code=404, detail="Rendered file missing.")

    # Same Range-support pattern as the multi-preset /download route, so a
    # 50 MB mastered WAV can be served through the upstream proxy without
    # hitting the 30-second timeout.
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
                    f'attachment; filename="{Path(job.filename).stem or "audio"}-bass-boosted.wav"'
                ),
            },
        )

    return FileResponse(
        path=str(wav_path),
        media_type="audio/wav",
        filename=f"{Path(job.filename).stem or 'audio'}-bass-boosted.wav",
    )
