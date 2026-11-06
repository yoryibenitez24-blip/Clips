from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, owner_token, rate_limiter
from app.config import get_settings
from app.db import get_db
from app.models import Job, Project
from app.schemas import AnalyzeRequest, AnalyzeResponse, InspectRequest, VideoInfo
from app.services.errors import LimitExceeded
from app.services.jobs import create_job
from app.services.media import describe_source
from app.workers.tasks import dispatch

router = APIRouter(prefix="/api/videos", tags=["videos"])

_ALLOWED_UPLOAD_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


@router.post("/inspect", response_model=VideoInfo)
def inspect(payload: InspectRequest) -> VideoInfo:
    """Validate a link and return metadata + whether media retrieval is permitted. No job is created."""
    _adapter, _req, _meta, _access, info = describe_source(payload.url)
    return info


@router.post("/analyze", response_model=AnalyzeResponse, status_code=202)
def analyze(payload: AnalyzeRequest, request: Request, db: Session = Depends(get_db),
            owner: str | None = Depends(owner_token)) -> AnalyzeResponse:
    s = get_settings()
    rate_limiter.check(f"analyze:{client_ip(request)}", s.rate_limit_analyze_per_hour)

    _adapter, req, meta, access, info = describe_source(payload.youtube_url)
    if not access.allowed:
        raise HTTPException(status_code=403, detail={"code": "media_access_denied", "message": access.reason,
                                                     "video": info.model_dump()})
    if meta.duration_seconds and meta.duration_seconds > s.max_source_duration_seconds:
        raise LimitExceeded(f"This video is longer than the {s.max_source_duration_seconds // 60}-minute limit.")

    active = db.scalar(select(func.count()).select_from(Job).where(Job.kind == "analyze",
                                                                     Job.status.in_(("queued", "running"))))
    if active is not None and active >= s.max_active_analysis_jobs * 4:
        raise HTTPException(503, "The processing queue is full right now. Please try again in a few minutes.")

    project = Project(
        source_url=req.canonical_url or req.url, source_kind=req.kind, video_id=req.video_id, title=meta.title,
        author=meta.author, thumbnail_url=meta.thumbnail_url, duration_seconds=meta.duration_seconds,
        status="queued", owner_token=owner,
        settings={"clip_duration": payload.clip_duration, "clip_style": payload.clip_style,
                  "orientation": payload.orientation, "caption_style": payload.caption_style,
                  "captions_enabled": payload.captions_enabled, "language": payload.language,
                  "max_clips": payload.max_clips},
        metadata_={"source": meta.to_dict(), "access": access.__dict__},
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    job = create_job(db, project, "analyze", params=project.settings)
    job.celery_task_id = dispatch("analyze", job.id)
    db.commit()
    return AnalyzeResponse(project_id=project.id, job_id=job.id, video=info)


@router.post("/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    """Secondary ingestion path for media you hold the rights to. Returns an upload:// reference."""
    s = get_settings()
    if not s.upload_ingest_enabled:
        raise HTTPException(404, "Uploads are disabled on this deployment")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _ALLOWED_UPLOAD_EXT:
        raise HTTPException(400, "Unsupported file type. Upload an MP4, MOV, WebM or MKV file.")
    token = uuid.uuid4().hex
    dest_dir = s.upload_dir / token
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "upload" + ext
    dest = dest_dir / safe_name
    size = 0
    with dest.open("wb") as fh:
        while chunk := await file.read(4 * 1024 * 1024):
            size += len(chunk)
            if size > s.max_upload_bytes:
                fh.close()
                dest.unlink(missing_ok=True)
                dest_dir.rmdir()
                raise HTTPException(413, "The uploaded file exceeds the size limit")
            fh.write(chunk)
    try:
        from app.services.ffmpeg import probe

        info = probe(dest)
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        dest_dir.rmdir()
        raise HTTPException(400, f"The uploaded file could not be decoded as video: {str(exc)[:200]}") from exc
    return {"url": f"upload://{token}", "filename": file.filename, "bytes": size, "duration_seconds": info.duration,
            "width": info.width, "height": info.height}
