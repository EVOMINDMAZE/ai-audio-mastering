"""
POST /ai-master — analyze an upload, ask the LLM to pick a preset + parameter
overrides, render once with the merged parameters, return job_id.

The flow mirrors /bass-boost (async job, poll status, download WAV) so the
frontend can reuse the same useMasterJob-style polling pattern. The two key
differences:

1. The analyze() call happens server-side (we don't trust the frontend's
   payload — it could be stale).
2. The DSP chain runs with a *merged* parameter dict (base preset + LLM
   overrides) instead of a single preset's defaults.

If the LLM is unavailable or fails, we fall back to a heuristic
recommendation (llm.fallback_recommendation) so the user always gets a
result. The response payload includes `source: "llm" | "fallback"` and
the reasoning string so the frontend can show why a particular preset was
chosen.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse

from .. import audio_engine, llm, storage
from ..jobs import registry
from .analyze import _save_upload  # reuse the upload-then-convert logic

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ai-master"])

# Same upload cap as the other endpoints — analyze() reads the whole file
# into a numpy float32 buffer, so OOM protection is non-negotiable.
_MAX_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024


def _render_one(job_id: str, saved_path: str, job_dir: Path, params: dict) -> None:
    """Background render for an AI-tuned job. Single output (no preset loop)."""
    out_path = job_dir / "ai_mastered.wav"
    try:
        metrics = audio_engine.master(saved_path, str(out_path), **params)
    except Exception as e:
        logger.exception("ai-master render failed for job_id=%s", job_id)
        registry.finish(job_id, error=f"{type(e).__name__}: {e}")
        return

    # Patch the placeholder variant's metrics.
    job = registry.get(job_id)
    if job and job.variants:
        job.variants[0]["metrics"] = metrics
    registry.finish(job_id)


@router.post("/ai-master")
async def ai_master_endpoint(file: UploadFile = File(...)) -> dict:
    """Analyze, ask LLM for preset + overrides, render once."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename.")

    # ---- 1. Save the upload -------------------------------------------------
    job_id = uuid.uuid4().hex[:12]
    job_dir = storage.get_job_dir(job_id)
    logger.info("ai-master: job_id=%s filename=%s", job_id, file.filename)
    saved_path = _save_upload(file, job_dir)
    await file.close()

    # ---- 2. Run analyze() for the LLM prompt + the response payload --------
    try:
        features = audio_engine.analyze(str(saved_path))
    except Exception as e:
        logger.exception("ai-master: analyze failed for job_id=%s", job_id)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}") from e

    # ---- 3. Ask the LLM (or fall back) -------------------------------------
    source = "llm"
    recommendation: dict
    try:
        recommendation = llm.recommend_preset(features)
    except llm.LLMError as e:
        logger.warning("ai-master: LLM unavailable, using heuristic: %s", e)
        source = "fallback"
        recommendation = llm.fallback_recommendation(features)

    # ---- 4. Register the job + queue the render ----------------------------
    registry.create(job_id=job_id, filename=file.filename)
    registry.add_variant(
        job_id,
        {
            "preset_id": recommendation["preset_id"],
            "label": recommendation["preset_label"],
            "description": recommendation["preset_description"],
            "download_url": f"/api/ai-master/{job_id}/download",
            "metrics": None,
            "source": source,
            "overrides": recommendation["overrides"],
            "reasoning": recommendation["reasoning"],
            "input_features": {
                "lufs": features.get("lufs_integrated"),
                "peak_dbtp": features.get("true_peak_dbtp"),
                "bpm": features.get("bpm"),
                "mud_flag": features.get("mud_flag"),
                "clipping_flag": features.get("clipping_flag"),
                "duration_s": features.get("duration_s"),
            },
        },
    )

    threading.Thread(
        target=_render_one,
        args=(job_id, str(saved_path), job_dir, recommendation["params"]),
        daemon=True,
    ).start()

    return {
        "job_id": job_id,
        "status": "queued",
        "status_url": f"/api/ai-master/{job_id}/status",
        "source": source,
        "preset_id": recommendation["preset_id"],
        "overrides": recommendation["overrides"],
        "reasoning": recommendation["reasoning"],
        "input_features": {
            "lufs": features.get("lufs_integrated"),
            "peak_dbtp": features.get("true_peak_dbtp"),
            "bpm": features.get("bpm"),
            "mud_flag": features.get("mud_flag"),
            "clipping_flag": features.get("clipping_flag"),
            "duration_s": features.get("duration_s"),
        },
    }


@router.get("/ai-master/{job_id}/status")
async def ai_master_status(job_id: str) -> dict:
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired.")
    return job.as_dict()


@router.get("/ai-master/{job_id}/download")
async def ai_master_download(job_id: str, request: Request):
    """Stream the AI-mastered WAV. Same Range-support pattern as bass-boost."""
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired.")
    if job.status != "ready":
        raise HTTPException(
            status_code=409, detail=f"Job is not ready (status={job.status})."
        )
    wav_path = storage.get_job_dir(job_id) / "ai_mastered.wav"
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
                    f'attachment; filename="{Path(job.filename).stem or "audio"}-ai-mastered.wav"'
                ),
            },
        )

    return FileResponse(
        path=str(wav_path),
        media_type="audio/wav",
        filename=f"{Path(job.filename).stem or 'audio'}-ai-mastered.wav",
    )