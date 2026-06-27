"""
Route-level tests for the /api/master, /api/master/{job_id}/status, and
/api/download endpoints.

The master endpoint is asynchronous: POST returns a job_id within ~1s, and
the actual rendering happens in a background thread. Tests poll the /status
endpoint until the job is "ready" (or "error"), then verify the per-preset
downloads serve valid 24-bit WAV files.

The `/api` prefix is added by `app.include_router(..., prefix="/api")` in
main.py — the dev Vite proxy strips it before forwarding to the backend, so
the same code works in both environments.
"""

from __future__ import annotations

import time
from io import BytesIO

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.audio_engine import PRESETS
from app.jobs import registry
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def wav_bytes() -> bytes:
    """A tiny but valid stereo WAV in memory."""
    sr = 44100
    t = np.arange(int(0.5 * sr)) / sr
    sig = (0.5 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    stereo = np.stack([sig, sig], axis=0).T
    buf = BytesIO()
    sf.write(buf, stereo, sr, subtype="PCM_16", format="WAV")
    buf.seek(0)
    return buf.read()


def _wait_until_ready(client: TestClient, job_id: str, timeout_s: float = 60.0):
    """Poll /api/master/{job_id}/status until terminal state. Returns the state dict."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = client.get(f"/api/master/{job_id}/status")
        assert r.status_code == 200, r.text
        state = r.json()
        if state["status"] in ("ready", "error"):
            return state
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish within {timeout_s}s")


def test_master_endpoint_returns_job_id_immediately(client: TestClient, wav_bytes: bytes):
    """POST /api/master must return within 1s with {job_id, status:'queued'}."""
    t0 = time.time()
    res = client.post(
        "/api/master",
        files={"file": ("test.wav", wav_bytes, "audio/wav")},
    )
    elapsed = time.time() - t0
    assert res.status_code == 200, res.text
    body = res.json()
    assert "job_id" in body
    assert body["status"] == "queued"
    assert body["status_url"] == f"/api/master/{body['job_id']}/status"
    # Async guarantee: the request returns before the render finishes.
    assert elapsed < 5.0, f"POST /api/master took {elapsed:.1f}s — should be near-instant"


def test_status_endpoint_404_for_unknown_job(client: TestClient):
    """GET /api/master/does_not_exist/status must return 404."""
    r = client.get("/api/master/does_not_exist/status")
    assert r.status_code == 404


def test_status_endpoint_reports_progress_and_eventually_ready(
    client: TestClient, wav_bytes: bytes
):
    """Polling /status eventually returns 'ready' with all 6 variants."""
    res = client.post(
        "/api/master",
        files={"file": ("test.wav", wav_bytes, "audio/wav")},
    )
    job_id = res.json()["job_id"]

    # Poll until terminal.
    state = _wait_until_ready(client, job_id, timeout_s=60.0)
    assert state["status"] == "ready", f"job failed: {state.get('error')}"
    variants = state["variants"]
    assert len(variants) == len(PRESETS)
    assert {v["preset_id"] for v in variants} == set(PRESETS.keys())
    for v in variants:
        assert v["label"]
        assert v["description"]
        assert v["download_url"].endswith(f"/{v['preset_id']}")
        assert all(
            k in v["metrics"]
            for k in ("in_lufs", "out_lufs", "in_peak_dbtp", "out_peak_dbtp", "applied_gain_db")
        )


def test_download_per_preset(client: TestClient, wav_bytes: bytes):
    """GET /api/download/{job_id}/{preset_id} serves the per-preset mastered WAV."""
    res = client.post(
        "/api/master",
        files={"file": ("test.wav", wav_bytes, "audio/wav")},
    )
    state = _wait_until_ready(client, res.json()["job_id"], timeout_s=60.0)
    job_id = state["job_id"]

    for preset_id in PRESETS:
        dres = client.get(f"/api/download/{job_id}/{preset_id}")
        assert dres.status_code == 200, f"preset {preset_id}: {dres.text}"
        assert dres.headers["content-type"] == "audio/wav"
        body = dres.content
        assert len(body) > 1000
        info = sf.info(BytesIO(body))
        assert info.subtype == "PCM_24"


def test_download_unknown_preset(client: TestClient, wav_bytes: bytes):
    """Unknown preset_id must return 404, not 500."""
    res = client.post(
        "/api/master",
        files={"file": ("test.wav", wav_bytes, "audio/wav")},
    )
    state = _wait_until_ready(client, res.json()["job_id"], timeout_s=60.0)
    job_id = state["job_id"]
    bad = client.get(f"/api/download/{job_id}/does_not_exist")
    assert bad.status_code == 404


def teardown_module(module):
    """Stop the registry's sweeper thread so pytest can exit cleanly."""
    if registry._sweep_thread and registry._sweep_thread.is_alive():
        # Daemon threads die on process exit; no explicit stop needed.
        pass
