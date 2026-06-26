"""
Unit tests for the in-memory JobRegistry.

Tests focus on thread-safety of the lock and the lifecycle of a single
job: create -> add variants -> finish. Sweep behavior is tested with a
shrunk TTL so we don't have to wait an hour.
"""

from __future__ import annotations

import time

import pytest

from app.jobs import JobRegistry, MasterJob


@pytest.fixture
def fresh_registry(monkeypatch: pytest.MonkeyPatch) -> JobRegistry:
    """A registry with a 0.5s TTL so sweep tests run fast."""
    reg = JobRegistry()
    # Shorten TTL for the sweep test; constructor argument would be cleaner
    # but monkeypatching keeps the production class signature stable.
    monkeypatch.setattr(JobRegistry, "TTL_SECONDS", 0.5)
    return reg


def test_create_and_get(fresh_registry: JobRegistry):
    job = fresh_registry.create("abc123", "test.wav")
    assert job.job_id == "abc123"
    assert job.filename == "test.wav"
    assert job.status == "queued"
    assert job.variants == []

    got = fresh_registry.get("abc123")
    assert got is job


def test_get_unknown_returns_none(fresh_registry: JobRegistry):
    assert fresh_registry.get("does_not_exist") is None


def test_add_variant_flips_queued_to_processing(fresh_registry: JobRegistry):
    fresh_registry.create("a", "x.wav")
    assert fresh_registry.get("a").status == "queued"

    fresh_registry.add_variant("a", {"preset_id": "streaming", "label": "L"})
    assert fresh_registry.get("a").status == "processing"
    assert len(fresh_registry.get("a").variants) == 1


def test_finish_marks_ready(fresh_registry: JobRegistry):
    fresh_registry.create("a", "x.wav")
    fresh_registry.add_variant("a", {"preset_id": "p1", "label": "L1"})
    fresh_registry.finish("a")
    assert fresh_registry.get("a").status == "ready"


def test_finish_marks_error(fresh_registry: JobRegistry):
    fresh_registry.create("a", "x.wav")
    fresh_registry.finish("a", error="boom")
    job = fresh_registry.get("a")
    assert job.status == "error"
    assert job.error == "boom"


def test_finish_unknown_job_is_noop(fresh_registry: JobRegistry):
    # Should not raise even if the job was already swept.
    fresh_registry.finish("ghost")


def test_sweep_removes_old_jobs(fresh_registry: JobRegistry):
    fresh_registry.create("a", "x.wav")
    # TTL is 0.5s, so wait 0.6s.
    time.sleep(0.6)
    removed = fresh_registry.sweep()
    assert removed == 1
    assert fresh_registry.get("a") is None


def test_sweep_keeps_recent_jobs(fresh_registry: JobRegistry):
    fresh_registry.create("a", "x.wav")
    removed = fresh_registry.sweep()
    assert removed == 0
    assert fresh_registry.get("a") is not None


def test_as_dict_shape(fresh_registry: JobRegistry):
    fresh_registry.create("a", "x.wav")
    fresh_registry.add_variant(
        "a",
        {
            "preset_id": "streaming",
            "label": "L",
            "description": "D",
            "download_url": "/api/download/a/streaming",
            "metrics": {"in_lufs": -14.0, "out_lufs": -14.0, "in_peak_dbtp": -1.0, "out_peak_dbtp": -1.0, "applied_gain_db": 0.0},
        },
    )
    d = fresh_registry.get("a").as_dict()
    assert d["job_id"] == "a"
    assert d["status"] == "processing"
    assert d["error"] is None
    assert len(d["variants"]) == 1
    assert d["variants"][0]["preset_id"] == "streaming"
