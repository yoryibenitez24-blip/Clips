from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import clip_out, job_out
from app.db import get_db
from app.models import Clip, Job, Project
from app.schemas import CaptionSettings, ClipOut, ClipPatch, ClipSettings, JobOut, RenderRequest
from app.services.captions import redistribute_text
from app.services.jobs import create_job
from app.services.storage import get_storage
from app.workers.tasks import dispatch

router = APIRouter(prefix="/api/clips", tags=["clips"])


def _get_clip(db: Session, clip_id: str) -> Clip:
    clip = db.get(Clip, clip_id)
    if not clip or clip.is_deleted:
        raise HTTPException(404, "Clip not found")
    return clip


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@router.get("/{clip_id}", response_model=ClipOut)
def get_clip(clip_id: str, db: Session = Depends(get_db)) -> ClipOut:
    return clip_out(_get_clip(db, clip_id))


@router.patch("/{clip_id}", response_model=ClipOut)
def patch_clip(clip_id: str, patch: ClipPatch, db: Session = Depends(get_db)) -> ClipOut:
    clip = _get_clip(db, clip_id)
    project = db.get(Project, clip.project_id)
    duration = project.duration_seconds or clip.detected_end
    if patch.title is not None:
        clip.title = patch.title.strip() or clip.title
    start = clip.start_time if patch.start_time is None else patch.start_time
    end = clip.end_time if patch.end_time is None else patch.end_time
    if patch.start_time is not None or patch.end_time is not None:
        start, end = max(0.0, start), min(float(duration), end)
        if end - start < 2.0:
            raise HTTPException(400, "A clip must be at least 2 seconds long")
        if end - start > 180.0:
            raise HTTPException(400, "A clip cannot be longer than 3 minutes")
        # expanding beyond the detected range pulls in more transcript words from the project transcript
        if (start < clip.detected_start or end > clip.detected_end) and project.transcript:
            from app.services.transcription import all_words

            words = all_words(project.transcript)
            existing = {(w["start"], w["end"]) for w in clip.caption_words}
            extra = [w for w in words if w["end"] > start and w["start"] < end and (w["start"], w["end"]) not in existing]
            clip.caption_words = sorted([*clip.caption_words, *extra], key=lambda w: w["start"])
        clip.start_time, clip.end_time = round(start, 3), round(end, 3)
    if patch.caption_preset is not None:
        current = ClipSettings(**(clip.settings or {}))
        preset = CaptionSettings.from_preset(patch.caption_preset, enabled=current.captions.enabled)
        clip.settings = {**(clip.settings or {}), "captions": preset.model_dump()}
    if patch.settings is not None:
        merged = _deep_merge(clip.settings or {}, patch.settings)
        try:
            clip.settings = ClipSettings(**merged).model_dump()
        except Exception as exc:  # pydantic validation
            raise HTTPException(422, f"Invalid settings: {exc}") from exc
    if patch.caption_words is not None:
        words = []
        for w in patch.caption_words:
            try:
                ws, we, text = float(w["start"]), float(w["end"]), str(w["text"]).strip()
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(422, "caption_words entries need start, end and text") from exc
            if text and we > ws:
                words.append({"start": round(ws, 3), "end": round(we, 3), "text": text[:80]})
        clip.caption_words = sorted(words, key=lambda w: w["start"])
    elif patch.caption_text is not None:
        clip.caption_words = redistribute_text(clip.caption_words or [], patch.caption_text)
    db.commit()
    db.refresh(clip)
    return clip_out(clip)


@router.post("/{clip_id}/render", response_model=JobOut, status_code=202)
def render_clip(clip_id: str, payload: RenderRequest, db: Session = Depends(get_db)) -> JobOut:
    clip = _get_clip(db, clip_id)
    project = db.get(Project, clip.project_id)
    running = db.query(Job).filter(Job.clip_id == clip.id, Job.status.in_(("queued", "running"))).first()
    if running and running.params.get("purpose") == payload.purpose:
        return job_out(running)
    params = {"purpose": payload.purpose, "export": payload.export.model_dump() if payload.export else None,
              "force": payload.force}
    if payload.export:
        clip.settings = {**(clip.settings or {}), "export": payload.export.model_dump()}
    if payload.force:
        if payload.purpose == "preview":
            clip.preview_hash = None
        else:
            clip.export_hash = None
    job = create_job(db, project, "render", params=params, clip_id=clip.id)
    job.celery_task_id = dispatch("render", job.id)
    db.commit()
    return job_out(job)


@router.get("/{clip_id}/download")
def download_clip(clip_id: str, db: Session = Depends(get_db)):
    clip = _get_clip(db, clip_id)
    if clip.export_status != "ready" or not clip.export_key:
        raise HTTPException(409, {"code": "export_not_ready", "message": "This clip has not been exported yet. "
                                  "Start an export with POST /api/clips/{id}/render."})
    return RedirectResponse(get_storage().url_for(clip.export_key), status_code=302)


@router.get("/{clip_id}/preview")
def preview_clip(clip_id: str, db: Session = Depends(get_db)):
    clip = _get_clip(db, clip_id)
    if clip.preview_status != "ready" or not clip.preview_key:
        raise HTTPException(409, "Preview not ready")
    return RedirectResponse(get_storage().url_for(clip.preview_key), status_code=302)


@router.delete("/{clip_id}", status_code=204)
def delete_clip(clip_id: str, db: Session = Depends(get_db)) -> None:
    clip = _get_clip(db, clip_id)
    clip.is_deleted = True
    db.commit()
