"""Job state tracking. Progress is derived from real stage completion, never from timers."""
from __future__ import annotations

import logging
import resource
import time
from datetime import datetime, timezone

from app.db import SessionLocal
from app.models import Job, Project

log = logging.getLogger(__name__)

ANALYZE_STAGES: list[tuple[str, str, float]] = [
    ("checking", "Checking video", 3),
    ("retrieving", "Retrieving media", 15),
    ("preparing", "Preparing video", 5),
    ("transcribing", "Transcribing", 22),
    ("scenes", "Analyzing scenes", 12),
    ("highlights", "Identifying highlights", 4),
    ("clips", "Generating clips", 4),
    ("rendering", "Rendering", 35),
    ("complete", "Complete", 0),
]
RENDER_STAGES = [("retrieving", "Retrieving media", 20), ("rendering", "Rendering", 80), ("complete", "Complete", 0)]
ZIP_STAGES = [("rendering", "Rendering", 85), ("packaging", "Packaging ZIP", 15), ("complete", "Complete", 0)]

STAGE_LABELS = {k: v for k, v, _ in ANALYZE_STAGES + RENDER_STAGES + ZIP_STAGES}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobTracker:
    """Persists stage/progress updates for a job using short-lived DB sessions."""

    def __init__(self, job_id: str, stages: list[tuple[str, str, float]]):
        self.job_id = job_id
        self.stages = stages
        self.total = sum(w for _, _, w in stages) or 1.0
        self.stage_index = -1
        self.stage_started = time.monotonic()
        self._last_write = 0.0
        self._cpu_start = resource.getrusage(resource.RUSAGE_SELF).ru_utime
        self._metrics: dict = {"stage_seconds": {}}
        self.warnings: list[str] = []

    # -- helpers -------------------------------------------------------------------
    def _base_progress(self, idx: int) -> float:
        return sum(w for _, _, w in self.stages[:idx]) / self.total * 100.0

    def _write(self, **fields) -> None:
        with SessionLocal() as db:
            job = db.get(Job, self.job_id)
            if not job:
                return
            for k, v in fields.items():
                setattr(job, k, v)
            db.commit()

    def start(self) -> None:
        self._write(status="running", started_at=_now(), attempts=self._attempts() + 1)

    def _attempts(self) -> int:
        with SessionLocal() as db:
            job = db.get(Job, self.job_id)
            return job.attempts if job else 0

    def set_stage(self, name: str) -> None:
        if self.stage_index >= 0:
            prev = self.stages[self.stage_index][0]
            self._metrics["stage_seconds"][prev] = round(time.monotonic() - self.stage_started, 2)
        self.stage_index = next(i for i, (n, _, _) in enumerate(self.stages) if n == name)
        self.stage_started = time.monotonic()
        with SessionLocal() as db:
            job = db.get(Job, self.job_id)
            if not job:
                return
            history = list(job.stage_history or [])
            for h in history:
                if h.get("status") == "running":
                    h["status"] = "done"
                    h["finished_at"] = _now().isoformat()
            history.append({"stage": name, "label": STAGE_LABELS.get(name, name), "status": "running",
                            "started_at": _now().isoformat()})
            job.stage_history = history
            job.stage = name
            job.progress = round(self._base_progress(self.stage_index), 1)
            job.metrics = {**(job.metrics or {}), **self._metrics}
            db.commit()
        self._last_write = 0.0

    def progress(self, fraction: float, detail: str | None = None, *, force: bool = False) -> None:
        """fraction = completion of the current stage (0..1). Throttled to ~2 writes/sec."""
        now = time.monotonic()
        if not force and now - self._last_write < 0.5:
            return
        self._last_write = now
        weight = self.stages[self.stage_index][2] if self.stage_index >= 0 else 0
        pct = self._base_progress(max(self.stage_index, 0)) + weight / self.total * 100.0 * min(max(fraction, 0), 1)
        fields: dict = {"progress": round(pct, 1)}
        with SessionLocal() as db:
            job = db.get(Job, self.job_id)
            if not job:
                return
            job.progress = fields["progress"]
            if detail:
                history = list(job.stage_history or [])
                if history:
                    history[-1] = {**history[-1], "detail": detail}
                    job.stage_history = history
            db.commit()

    def warn(self, message: str) -> None:
        log.warning("job %s: %s", self.job_id, message)
        self.warnings.append(message)
        with SessionLocal() as db:
            job = db.get(Job, self.job_id)
            if job:
                job.warnings = list(job.warnings or []) + [message]
                db.commit()

    def metric(self, key: str, value) -> None:
        self._metrics[key] = value

    def complete(self, result: dict | None = None) -> None:
        if self.stage_index >= 0:
            prev = self.stages[self.stage_index][0]
            self._metrics["stage_seconds"][prev] = round(time.monotonic() - self.stage_started, 2)
        self._metrics["cpu_seconds"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_utime - self._cpu_start, 2)
        with SessionLocal() as db:
            job = db.get(Job, self.job_id)
            if not job:
                return
            history = list(job.stage_history or [])
            for h in history:
                if h.get("status") == "running":
                    h["status"] = "done"
                    h["finished_at"] = _now().isoformat()
            history.append({"stage": "complete", "label": "Complete", "status": "done",
                            "started_at": _now().isoformat(), "finished_at": _now().isoformat()})
            job.stage_history = history
            job.stage = "complete"
            job.status = "completed"
            job.progress = 100.0
            job.result = result or {}
            job.finished_at = _now()
            job.metrics = {**(job.metrics or {}), **self._metrics}
            db.commit()

    def fail(self, error: str, *, code: str | None = None) -> None:
        with SessionLocal() as db:
            job = db.get(Job, self.job_id)
            if not job:
                return
            history = list(job.stage_history or [])
            for h in history:
                if h.get("status") == "running":
                    h["status"] = "failed"
                    h["finished_at"] = _now().isoformat()
            job.stage_history = history
            job.status = "failed"
            job.error = error
            job.result = {"error_code": code} if code else job.result
            job.finished_at = _now()
            job.metrics = {**(job.metrics or {}), **self._metrics}
            db.commit()


def create_job(db, project: Project, kind: str, params: dict | None = None, clip_id: str | None = None) -> Job:
    job = Job(project_id=project.id, kind=kind, params=params or {}, clip_id=clip_id, status="queued",
              stage=None, progress=0.0)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
