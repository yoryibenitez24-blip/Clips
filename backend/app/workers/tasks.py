"""Celery task entry points. All real work lives in app.services.pipeline."""
from __future__ import annotations

import logging

from app.config import get_settings
from app.db import SessionLocal
from app.models import Job
from app.services.errors import ClipsError, RetrievalFailed
from app.services.pipeline import cleanup_expired, run_analysis, run_render, run_zip
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


def _should_retry(job_id: str) -> bool:
    if celery_app.conf.task_always_eager:
        return False  # eager mode (tests/dev): surface the failure immediately
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        return bool(job and job.attempts <= get_settings().job_max_retries)


def _requeue(job_id: str, reason: str) -> None:
    """Put a crashed job back in the queue and tell the UI a retry is coming."""
    from app.models import Project

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job:
            job.status = "queued"
            job.error = f"Attempt {job.attempts} failed ({reason[:200]}). Retrying automatically…"
            project = db.get(Project, job.project_id)
            if project and job.kind == "analyze":
                project.status = "queued"
            db.commit()


@celery_app.task(name="clips.analyze_video", bind=True, max_retries=3)
def analyze_video_task(self, job_id: str) -> None:
    try:
        run_analysis(job_id)
    except RetrievalFailed as exc:
        # transient network problems while retrieving media are retried with backoff
        if _should_retry(job_id):
            _requeue(job_id, str(exc))
            raise self.retry(exc=exc, countdown=min(60 * (2 ** self.request.retries), 600))
    except ClipsError:
        return  # user-facing failure already recorded on the job
    except Exception as exc:  # noqa: BLE001
        log.exception("analysis job %s crashed", job_id)
        if _should_retry(job_id):
            _requeue(job_id, repr(exc))
            raise self.retry(countdown=30)


@celery_app.task(name="clips.render_clip", bind=True, max_retries=2)
def render_clip_task(self, job_id: str) -> None:
    try:
        run_render(job_id)
    except ClipsError:
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("render job %s crashed", job_id)
        if _should_retry(job_id):
            _requeue(job_id, repr(exc))
            raise self.retry(countdown=20)


@celery_app.task(name="clips.zip_clips", bind=True, max_retries=1)
def zip_task(self, job_id: str) -> None:
    try:
        run_zip(job_id)
    except ClipsError:
        return


@celery_app.task(name="clips.cleanup")
def cleanup_task() -> dict:
    return cleanup_expired()


def dispatch(job_kind: str, job_id: str) -> str | None:
    """Enqueue the matching task; returns the Celery task id (None in eager mode)."""
    task = {"analyze": analyze_video_task, "render": render_clip_task, "zip": zip_task}[job_kind]
    result = task.apply_async(args=[job_id])
    return getattr(result, "id", None)
