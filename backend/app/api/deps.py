"""Shared API dependencies: DB session, owner token, rate limiting, serialisation."""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request

from app.config import get_settings
from app.models import Clip, Job, Project
from app.schemas import ClipOut, JobOut, ProjectOut, ProjectSummary
from app.services.jobs import STAGE_LABELS
from app.services.storage import get_storage


def owner_token(x_owner_token: str | None = Header(default=None)) -> str | None:
    if x_owner_token and 8 <= len(x_owner_token) <= 64 and x_owner_token.replace("-", "").isalnum():
        return x_owner_token
    return None


class RateLimiter:
    """Fixed-window limiter backed by Redis when available, in-memory otherwise."""

    def __init__(self):
        self._local: dict[str, deque] = defaultdict(deque)
        self._redis = None
        self._redis_checked = False

    def _client(self):
        if not self._redis_checked:
            self._redis_checked = True
            s = get_settings()
            if s.environment != "test":
                try:
                    import redis

                    client = redis.Redis.from_url(s.redis_url, socket_connect_timeout=1)
                    client.ping()
                    self._redis = client
                except Exception:  # noqa: BLE001
                    self._redis = None
        return self._redis

    def check(self, key: str, limit: int, window: int = 3600) -> None:
        client = self._client()
        if client is not None:
            try:
                bucket = f"ratelimit:{key}:{int(time.time() // window)}"
                n = client.incr(bucket)
                if n == 1:
                    client.expire(bucket, window)
                if n > limit:
                    raise HTTPException(429, "Too many analysis requests. Please try again later.")
                return
            except HTTPException:
                raise
            except Exception:  # noqa: BLE001
                pass
        q = self._local[key]
        now = time.time()
        while q and q[0] < now - window:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, "Too many analysis requests. Please try again later.")
        q.append(now)


rate_limiter = RateLimiter()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def job_out(job: Job) -> JobOut:
    return JobOut(id=job.id, project_id=job.project_id, clip_id=job.clip_id, kind=job.kind, status=job.status,
                  stage=job.stage, stage_label=STAGE_LABELS.get(job.stage or "", job.stage), progress=job.progress,
                  stage_history=job.stage_history or [], result=job.result, error=job.error,
                  warnings=job.warnings or [], metrics=job.metrics or {}, attempts=job.attempts,
                  created_at=job.created_at, updated_at=job.updated_at)


def clip_out(clip: Clip) -> ClipOut:
    storage = get_storage()
    return ClipOut(
        id=clip.id, project_id=clip.project_id, index=clip.index, title=clip.title, reason=clip.reason,
        score=clip.score, signals=clip.signals or {}, start_time=clip.start_time, end_time=clip.end_time,
        detected_start=clip.detected_start, detected_end=clip.detected_end,
        duration=round(clip.end_time - clip.start_time, 3), settings=clip.settings or {},
        caption_words=clip.caption_words or [], layout=clip.layout or {},
        preview_url=storage.url_for(clip.preview_key) if clip.preview_key and clip.preview_status == "ready" else None,
        preview_status=clip.preview_status,
        export_url=storage.url_for(clip.export_key) if clip.export_key and clip.export_status == "ready" else None,
        export_status=clip.export_status, export_info=clip.export_info or {}, render_error=clip.render_error,
        created_at=clip.created_at, updated_at=clip.updated_at,
    )


def project_out(project: Project) -> ProjectOut:
    active = next((j for j in reversed(project.jobs) if j.kind == "analyze"), None)
    from pathlib import Path

    return ProjectOut(
        id=project.id, source_url=project.source_url, source_kind=project.source_kind, video_id=project.video_id,
        title=project.title, author=project.author, thumbnail_url=project.thumbnail_url,
        duration_seconds=project.duration_seconds, width=project.width, height=project.height, fps=project.fps,
        language=project.language, status=project.status, error=project.error, settings=project.settings or {},
        analysis_summary=project.analysis or {},
        source_available=bool(project.source_media_path and Path(project.source_media_path).exists()),
        clips=[clip_out(c) for c in project.clips if not c.is_deleted],
        active_job=job_out(active) if active else None, created_at=project.created_at, updated_at=project.updated_at,
    )


def project_summary(project: Project) -> ProjectSummary:
    return ProjectSummary(id=project.id, title=project.title, thumbnail_url=project.thumbnail_url,
                          duration_seconds=project.duration_seconds, status=project.status,
                          clip_count=sum(1 for c in project.clips if not c.is_deleted), created_at=project.created_at)
