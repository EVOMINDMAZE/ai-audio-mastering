"""
In-memory job registry for the async master endpoint.

A ``MasterJob`` is created on POST /master and mutated by the background
render thread as each preset finishes. The dict is swept every 10 minutes
to remove jobs older than 1 hour; the corresponding /tmp/audio_jobs/{id}/
files are NOT deleted here (the route module handles file cleanup if/when
that becomes necessary; the registry is the in-memory state only).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MasterJob:
    job_id: str
    filename: str
    created_at: float = field(default_factory=time.time)
    status: str = "queued"  # "queued" | "processing" | "ready" | "error"
    error: Optional[str] = None
    variants: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "error": self.error,
            "variants": list(self.variants),
            "metadata": dict(self.metadata),
        }


class JobRegistry:
    """Thread-safe singleton holding all in-flight master jobs."""

    TTL_SECONDS = 60 * 60  # 1 hour
    SWEEP_INTERVAL_SECONDS = 10 * 60  # 10 minutes

    def __init__(self) -> None:
        self._jobs: Dict[str, MasterJob] = {}
        self._lock = threading.Lock()
        self._sweep_thread: Optional[threading.Thread] = None

    def create(self, job_id: str, filename: str) -> MasterJob:
        job = MasterJob(job_id=job_id, filename=filename)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Optional[MasterJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def add_variant(self, job_id: str, variant: Dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return  # job was swept; ignore
            job.variants.append(variant)
            if job.status == "queued":
                job.status = "processing"

    def finish(self, job_id: str, error: Optional[str] = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if error is not None:
                job.status = "error"
                job.error = error
            else:
                job.status = "ready"

    def sweep(self) -> int:
        """Remove jobs older than TTL_SECONDS. Returns count removed."""
        cutoff = time.time() - self.TTL_SECONDS
        with self._lock:
            stale = [jid for jid, j in self._jobs.items() if j.created_at < cutoff]
            for jid in stale:
                del self._jobs[jid]
        if stale:
            logger.info("swept %d stale master jobs: %s", len(stale), stale[:5])
        return len(stale)

    def start_sweeper(self) -> None:
        """Start a daemon thread that sweeps every SWEEP_INTERVAL_SECONDS."""
        if self._sweep_thread and self._sweep_thread.is_alive():
            return

        def _loop() -> None:
            while True:
                time.sleep(self.SWEEP_INTERVAL_SECONDS)
                self.sweep()

        self._sweep_thread = threading.Thread(target=_loop, daemon=True)
        self._sweep_thread.start()


# Singleton — the route module imports this instance.
registry = JobRegistry()
