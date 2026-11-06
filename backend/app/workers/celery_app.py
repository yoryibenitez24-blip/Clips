from __future__ import annotations

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery("clips", broker=settings.redis_url, backend=None)
celery_app.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    task_routes={
        "clips.analyze_video": {"queue": "analysis"},
        "clips.render_clip": {"queue": "render"},
        "clips.zip_clips": {"queue": "render"},
        "clips.cleanup": {"queue": "render"},
    },
    beat_schedule={"cleanup-expired": {"task": "clips.cleanup", "schedule": 3600.0}},
    task_time_limit=4 * 3600,
    task_soft_time_limit=4 * 3600 - 60,
)
celery_app.autodiscover_tasks(["app.workers"])
