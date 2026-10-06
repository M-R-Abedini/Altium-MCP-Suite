# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Async job runner for long engine runs (roadmap 1.5).

Some offline engines (routing a dense board, a big placement solve) take
longer than an MCP tool's default 10 s timeout. Rather than block the client
or raise a spurious timeout, submit the work as a job: ``design_job_start``
returns an id immediately, and the client polls ``design_job_status`` /
``design_job_result``. The engine runs on a background worker; the loop stays
responsive.

Design: an in-process ``ThreadPoolExecutor``-backed store (bounded workers →
natural queueing and a runaway cap). Each job's state is a ``JobRecord``
updated under a lock; a per-job ``Event`` lets tests wait deterministically.
Job ids are a monotonic counter, so a given store's ids are reproducible.
The jobs run pure-Python engine functions (no bridge), which keeps this fully
unit-testable.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

PENDING = "pending"
RUNNING = "running"
DONE = "done"
ERROR = "error"


@dataclass
class JobRecord:
    id: str
    kind: str
    status: str = PENDING
    created: str = ""
    started: Optional[str] = None
    finished: Optional[str] = None
    error: Optional[str] = None
    # result is kept off the summary payload (can be large); fetch via result().
    result: Any = field(default=None, repr=False)

    def summary(self) -> dict:
        # Built explicitly rather than via asdict() so the potentially-large
        # ``result`` (a full routing solution) is never deep-copied just to be
        # dropped from the summary payload on every status poll.
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "error": self.error,
        }


class JobStore:
    """Bounded in-process job runner."""

    def __init__(self, max_workers: int = 2):
        self._ex = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="eda-job"
        )
        self._lock = threading.Lock()
        self._jobs: dict[str, JobRecord] = {}
        self._events: dict[str, threading.Event] = {}
        self._counter = 0

    def submit(self, kind: str, fn: Callable, *args, **kwargs) -> str:
        with self._lock:
            self._counter += 1
            jid = f"job-{self._counter:04d}"
            rec = JobRecord(id=jid, kind=kind, status=PENDING,
                            created=datetime.now().isoformat(timespec="seconds"))
            self._jobs[jid] = rec
            self._events[jid] = threading.Event()

        def _run():
            with self._lock:
                rec.status = RUNNING
                rec.started = datetime.now().isoformat(timespec="seconds")
            try:
                out = fn(*args, **kwargs)
                with self._lock:
                    rec.result = out
                    rec.status = DONE
            except Exception as e:  # noqa: BLE001 - report, don't crash the worker
                with self._lock:
                    rec.error = f"{type(e).__name__}: {e}"
                    rec.status = ERROR
            finally:
                with self._lock:
                    rec.finished = datetime.now().isoformat(timespec="seconds")
                self._events[jid].set()

        self._ex.submit(_run)
        return jid

    def get(self, job_id: str) -> Optional[JobRecord]:
        with self._lock:
            return self._jobs.get(job_id)

    def status(self, job_id: str) -> Optional[dict]:
        # Snapshot under the lock: a worker mutates the record's fields under
        # the same lock, so reading them outside it could observe a torn state.
        with self._lock:
            rec = self._jobs.get(job_id)
            return rec.summary() if rec else None

    def result(self, job_id: str) -> Optional[dict]:
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return None
            return {
                "id": rec.id,
                "kind": rec.kind,
                "status": rec.status,
                "error": rec.error,
                "result": rec.result if rec.status == DONE else None,
            }

    def list(self) -> list[dict]:
        with self._lock:
            recs = sorted(self._jobs.values(), key=lambda r: r.id, reverse=True)
            return [r.summary() for r in recs]

    def wait(self, job_id: str, timeout: Optional[float] = None) -> bool:
        """Block until the job finishes. Returns False on timeout/unknown id."""
        with self._lock:
            ev = self._events.get(job_id)
        if ev is None:
            return False
        return ev.wait(timeout)


# --- module singleton -------------------------------------------------------
_STORE: Optional[JobStore] = None


def get_job_store() -> JobStore:
    global _STORE
    if _STORE is None:
        _STORE = JobStore()
    return _STORE


# --- job-kind registry (offline engines runnable as jobs) -------------------
def _run_route(params: dict) -> dict:
    from ..route.router import route_geometry
    geometry = params.get("geometry")
    if not geometry:
        raise ValueError("route job requires a 'geometry' dict")
    kwargs = {k: v for k, v in params.items() if k != "geometry"}
    return route_geometry(geometry, **kwargs)


# Only geometry-dict-driven engines are registered here. Placement
# (pcb_plan_placement) builds structured PlaceComp/PlaceNet/BoardRegion inputs
# via the construct engine rather than taking a raw geometry dict, so wiring
# it as a job kind needs that adapter first: a follow-up.
JOB_KINDS: dict[str, Callable[[dict], dict]] = {
    "route": _run_route,
}
