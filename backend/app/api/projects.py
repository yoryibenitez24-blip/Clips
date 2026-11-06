from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import job_out, owner_token, project_out, project_summary
from app.config import get_settings
from app.db import get_db
from app.models import Job, Project
from app.schemas import DownloadAllRequest, JobOut, ProjectOut, ProjectSummary
from app.services.jobs import create_job
from app.services.storage import get_storage
from app.workers.tasks import dispatch

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=list[ProjectSummary])
def list_projects(db: Session = Depends(get_db), owner: str | None = Depends(owner_token),
                  limit: int = Query(50, le=200)) -> list[ProjectSummary]:
    stmt = select(Project).order_by(Project.created_at.desc()).limit(limit)
    if owner:
        stmt = stmt.where(Project.owner_token == owner)
    return [project_summary(p) for p in db.scalars(stmt).all()]


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, db: Session = Depends(get_db)) -> ProjectOut:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project_out(project)


@router.post("/{project_id}/retry", response_model=JobOut, status_code=202)
def retry_project(project_id: str, db: Session = Depends(get_db)) -> JobOut:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if any(j.kind == "analyze" and j.status in ("queued", "running") for j in project.jobs):
        raise HTTPException(409, "This project is already being processed")
    project.status = "queued"
    project.error = None
    job = create_job(db, project, "analyze", params=project.settings)
    job.celery_task_id = dispatch("analyze", job.id)
    db.commit()
    return job_out(job)


@router.post("/{project_id}/download-all", response_model=JobOut, status_code=202)
def download_all(project_id: str, payload: DownloadAllRequest, db: Session = Depends(get_db)) -> JobOut:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if project.status != "completed":
        raise HTTPException(409, "Clips are not ready yet")
    ids = payload.clip_ids or [c.id for c in project.clips if not c.is_deleted]
    valid = {c.id for c in project.clips if not c.is_deleted}
    if not ids or any(i not in valid for i in ids):
        raise HTTPException(400, "Invalid clip selection")
    params = {"clip_ids": ids, "export": payload.export.model_dump() if payload.export else None}
    job = create_job(db, project, "zip", params=params)
    job.celery_task_id = dispatch("zip", job.id)
    db.commit()
    return job_out(job)


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: str, db: Session = Depends(get_db)) -> None:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if any(j.status in ("queued", "running") for j in project.jobs):
        raise HTTPException(409, "Cannot delete a project while it is processing")
    storage = get_storage()
    try:
        storage.delete_prefix(f"projects/{project.id}/")
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(get_settings().work_dir / "projects" / project.id, ignore_errors=True)
    db.delete(project)
    db.commit()
