"""Processing pipeline. Called by Celery tasks; never by API route handlers directly."""
from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal
from app.models import Clip, Job, Project
from app.schemas import CaptionSettings, ClipSettings, ExportSettings
from app.services import audio_analysis, scenes as scene_svc, transcription, vision as vision_svc
from app.services.cache import cache_get, cache_set, fingerprint_file
from app.services.errors import AccessDenied, ClipsError, LimitExceeded, ProcessingFailed, SourceUnavailable
from app.services.ffmpeg import extract_analysis_audio, probe
from app.services.highlights import select_highlights, snap_bounds
from app.services.jobs import ANALYZE_STAGES, RENDER_STAGES, ZIP_STAGES, JobTracker
from app.services.llm_ranker import refine_with_llm
from app.services.media import describe_source, get_adapter_for
from app.services.render import RenderSpec, build_render, render_hash
from app.services.storage import content_type_for, get_storage
from app.services.transcription import all_words

log = logging.getLogger(__name__)


def project_work_dir(project_id: str) -> Path:
    d = get_settings().work_dir / "projects" / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _check_disk(path: Path) -> None:
    s = get_settings()
    usage = shutil.disk_usage(path)
    if usage.free < s.min_free_disk_bytes:
        raise LimitExceeded("Not enough temporary storage available to process a new video right now. "
                            "Please try again later.")


def _touch_source(project: Project) -> None:
    s = get_settings()
    project.source_expires_at = datetime.now(timezone.utc) + timedelta(hours=s.source_retention_hours)


def _vision_for(db, project: Project) -> dict | None:
    if not project.source_fingerprint:
        return None
    return cache_get(db, f"vision:{project.source_fingerprint}:{get_settings().analysis_fps}")


def ensure_source(db, project: Project, tracker: JobTracker | None) -> Path:
    """Return a local path to the source media, re-acquiring it if the working copy expired."""
    if project.source_media_path and Path(project.source_media_path).exists():
        _touch_source(project)
        db.commit()
        return Path(project.source_media_path)
    if tracker:
        tracker.set_stage("retrieving")
    adapter, req, meta, access, _info = describe_source(project.source_url)
    if not access.allowed:
        raise SourceUnavailable("The source media is no longer available and cannot be re-retrieved: "
                                + access.reason)
    work = project_work_dir(project.id)
    _check_disk(work)
    acquired = adapter.acquire(req, work, lambda f, d: tracker.progress(f, d) if tracker else None)
    info = probe(acquired.path)
    fp = fingerprint_file(acquired.path, info.duration)
    if project.source_fingerprint and fp != project.source_fingerprint:
        log.warning("project %s: re-acquired media has a different fingerprint", project.id)
    project.source_media_path = str(acquired.path)
    project.source_fingerprint = project.source_fingerprint or fp
    _touch_source(project)
    db.commit()
    return acquired.path


# ======================================================================================
# Analysis job
# ======================================================================================
def run_analysis(job_id: str) -> None:
    s = get_settings()
    tracker = JobTracker(job_id, ANALYZE_STAGES)
    tracker.start()
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            return
        project = db.get(Project, job.project_id)
        if not project:
            tracker.fail("Project not found")
            return
        project.status = "processing"
        project.error = None
        db.commit()
        try:
            _run_analysis(db, job, project, tracker)
        except ClipsError as exc:
            log.warning("analysis job %s failed: %s", job_id, exc.message)
            project.status = "failed"
            project.error = exc.message
            db.commit()
            tracker.fail(exc.message, code=exc.code)
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("analysis job %s crashed", job_id)
            project.status = "failed"
            project.error = f"Unexpected error: {str(exc)[:300]}"
            db.commit()
            tracker.fail(project.error, code="internal_error")
            raise


def _run_analysis(db, job: Job, project: Project, tracker: JobTracker) -> None:
    s = get_settings()
    gen = project.settings or {}
    work = project_work_dir(project.id)

    # 1. checking --------------------------------------------------------------------
    tracker.set_stage("checking")
    adapter, req, meta, access, info = describe_source(project.source_url)
    project.source_kind = req.kind
    project.video_id = req.video_id
    project.title = meta.title or project.title
    project.author = meta.author
    project.thumbnail_url = meta.thumbnail_url
    project.duration_seconds = meta.duration_seconds or project.duration_seconds
    project.metadata_ = {**(project.metadata_ or {}), "source": meta.to_dict(), "access": access.__dict__}
    db.commit()
    if not access.allowed:
        raise AccessDenied(access.reason)
    if meta.duration_seconds and meta.duration_seconds > s.max_source_duration_seconds:
        raise LimitExceeded(f"This video is longer than the {s.max_source_duration_seconds // 60}-minute limit.")
    tracker.progress(1.0, force=True)

    # 2. retrieving ------------------------------------------------------------------
    tracker.set_stage("retrieving")
    if project.source_media_path and Path(project.source_media_path).exists():
        source = Path(project.source_media_path)
        tracker.progress(1.0, "Using cached working copy", force=True)
    else:
        _check_disk(work)
        t0 = time.monotonic()
        acquired = adapter.acquire(req, work, tracker.progress)
        source = acquired.path
        tracker.metric("retrieval_seconds", round(time.monotonic() - t0, 2))
        project.metadata_ = {**project.metadata_, "acquisition": {"adapter": acquired.adapter, **acquired.details}}
    project.source_media_path = str(source)
    _touch_source(project)
    db.commit()

    # 3. preparing -------------------------------------------------------------------
    tracker.set_stage("preparing")
    minfo = probe(source)
    if minfo.duration <= 1.0:
        raise ProcessingFailed("The source media is too short or could not be decoded.")
    if minfo.duration > s.max_source_duration_seconds:
        raise LimitExceeded(f"This video is longer than the {s.max_source_duration_seconds // 60}-minute limit.")
    project.duration_seconds = minfo.duration
    project.width, project.height, project.fps = minfo.width, minfo.height, minfo.fps
    project.source_fingerprint = fingerprint_file(source, minfo.duration)
    tracker.metric("source_bytes", minfo.size_bytes)
    db.commit()
    fp = project.source_fingerprint
    wav = work / "analysis.wav"
    if minfo.has_audio:
        if not wav.exists():
            extract_analysis_audio(source, wav)
    else:
        tracker.warn("The source has no audio track: transcription and audio analysis are skipped.")
    tracker.progress(1.0, force=True)

    # 4. transcribing ----------------------------------------------------------------
    tracker.set_stage("transcribing")
    transcript: dict | None = None
    if minfo.has_audio:
        tkey = f"transcript:{fp}:{s.whisper_model}:{gen.get('language', 'auto')}"
        transcript = cache_get(db, tkey)
        if transcript:
            tracker.progress(1.0, "Transcript cached", force=True)
            tracker.metric("transcript_cached", True)
        else:
            try:
                t0 = time.monotonic()
                transcript = transcription.transcribe(wav, gen.get("language"), progress=tracker.progress)
                tracker.metric("transcription_seconds", round(time.monotonic() - t0, 2))
                cache_set(db, tkey, "transcript", transcript)
                for w in transcript.get("warnings", []):
                    tracker.warn(w)
            except transcription.TranscriptionUnavailable as exc:
                if s.transcription_required:
                    raise
                tracker.warn(f"Transcription unavailable, continuing with audio/visual analysis only: {exc.message}")
                transcript = None
    project.transcript = transcript
    project.language = (transcript or {}).get("language") if transcript else None
    db.commit()

    # 5. analyzing scenes (scene cuts, audio energy, sampled vision) ------------------
    tracker.set_stage("scenes")
    skey = f"scenes:{fp}:{s.scene_threshold}"
    scenes = cache_get(db, skey)
    if not scenes:
        scenes = scene_svc.detect_scenes(source, minfo.duration, minfo.fps,
                                         progress=lambda f: tracker.progress(f * 0.5, "Detecting scene changes"))
        cache_set(db, skey, "scenes", scenes)
    audio = None
    if minfo.has_audio:
        akey = f"audio:{fp}"
        audio = cache_get(db, akey)
        if not audio:
            audio = audio_analysis.analyze_audio(wav)
            cache_set(db, akey, "audio", audio)
    tracker.progress(0.55, "Sampling frames", force=True)
    vkey = f"vision:{fp}:{s.analysis_fps}"
    vision = cache_get(db, vkey)
    if not vision:
        vision = vision_svc.analyze_frames(source, minfo.width, minfo.height, minfo.duration,
                                           progress=lambda f: tracker.progress(0.55 + f * 0.45, "Sampling frames"))
        cache_set(db, vkey, "vision", vision)
    project.analysis = {
        "scene_count": len(scenes.get("scenes", [])), "cuts": scenes.get("cuts", [])[:500],
        "audio_peaks": len((audio or {}).get("peaks", [])), "silences": len((audio or {}).get("silences", [])),
        "sampled_frames": len(vision.get("times", [])), "facecam": vision.get("facecam", {"detected": False}),
        "has_transcript": bool(transcript and transcript.get("segments")),
        "transcript_words": len(all_words(transcript)),
    }
    db.commit()

    # 6. identifying highlights -------------------------------------------------------
    tracker.set_stage("highlights")
    style = gen.get("clip_style", "best")
    result = select_highlights(duration=minfo.duration, transcript=transcript, scenes=scenes, audio=audio,
                               vision=vision, style=style, clip_duration=gen.get("clip_duration", "auto"),
                               max_clips=int(gen.get("max_clips", 8)), language=gen.get("language"))
    picked = result["candidates"]
    min_len, max_len, _target = result["bounds"]
    tracker.metric("candidate_windows", result["candidate_count"])
    tracker.progress(0.5, "Ranking candidates", force=True)
    llm_picks, llm_warnings = refine_with_llm(result["shortlist"], style=style, max_clips=max(len(picked), 1),
                                             language=result["language"], min_len=min_len, max_len=max_len,
                                             video_title=project.title)
    if llm_picks:
        picked = llm_picks
        tracker.metric("llm_ranked", True)
    else:
        tracker.metric("llm_ranked", False)
        for w in llm_warnings:
            if not w.startswith("llm_skipped"):
                tracker.warn(f"LLM ranking unavailable ({w}); using signal-based ranking.")
    if not picked:
        raise ProcessingFailed("No usable highlight segments were found in this video.")
    project.highlights = [c.to_dict() for c in picked]
    db.commit()

    # 7. generating clips -------------------------------------------------------------
    tracker.set_stage("clips")
    words = all_words(transcript)
    captions_cfg = CaptionSettings.from_preset(gen.get("caption_style", "word_pop"))
    captions_cfg.enabled = bool(gen.get("captions_enabled", True))
    talking_styles = {"talking", "funny", "best", "reactions"}
    base_settings = ClipSettings(orientation=gen.get("orientation", "9:16"), reframe="auto",
                                 remove_silence=bool(words) and style in talking_styles, captions=captions_cfg,
                                 export=ExportSettings())
    facecam = vision.get("facecam") or {"detected": False}
    for existing in list(project.clips):
        db.delete(existing)
    db.flush()
    clips: list[Clip] = []
    for idx, c in enumerate(picked):
        start, end = snap_bounds(c.start, c.end, words, scenes, audio, minfo.duration, min_len, max_len)
        cw = [w for w in words if w["end"] > start and w["start"] < end]
        clip = Clip(project_id=project.id, index=idx, title=c.title, reason=c.reason, score=round(c.score, 4),
                    signals={k: round(v, 3) for k, v in c.signals.items()}, start_time=start, end_time=end,
                    detected_start=start, detected_end=end, settings=base_settings.model_dump(),
                    caption_words=cw, layout={"facecam": facecam}, preview_status="pending")
        db.add(clip)
        clips.append(clip)
    db.commit()
    tracker.progress(1.0, force=True)

    # 8. rendering previews -----------------------------------------------------------
    tracker.set_stage("rendering")
    storage = get_storage()
    rendered = 0
    for i, clip in enumerate(clips):
        tracker.progress(i / len(clips), f"Rendering preview {i + 1} of {len(clips)}", force=True)
        try:
            _render_clip_file(db, project, clip, "preview", minfo, vision, storage)
            rendered += 1
        except ClipsError as exc:
            clip.preview_status = "failed"
            clip.render_error = exc.message
            db.commit()
            tracker.warn(f"Preview render failed for clip {i + 1}: {exc.message[:200]}")
    if rendered == 0:
        raise ProcessingFailed("All clip renders failed. " + (clips[0].render_error or ""))
    tracker.metric("clips_rendered", rendered)

    project.status = "completed"
    project.error = None
    db.commit()
    tracker.complete({"clip_ids": [c.id for c in clips], "clips_rendered": rendered,
                      "warnings": tracker.warnings})


# ======================================================================================
# Render helpers
# ======================================================================================
def _clip_render_signature(clip: Clip, settings: ClipSettings, purpose: str) -> dict:
    sig = {"start": clip.start_time, "end": clip.end_time, "purpose": purpose, "v": get_settings().render_version,
           "orientation": settings.orientation, "reframe": settings.reframe, "offset": [settings.crop_offset_x,
           settings.crop_offset_y], "facecam_layout": settings.facecam_layout,
           "remove_silence": settings.remove_silence, "captions": settings.captions.model_dump(),
           "audio": settings.audio.model_dump(), "words": clip.caption_words}
    if purpose == "export":
        sig["export"] = settings.export.model_dump()
    return sig


def _render_clip_file(db, project: Project, clip: Clip, purpose: str, minfo, vision: dict | None, storage,
                      export_override: ExportSettings | None = None, tracker: JobTracker | None = None) -> dict:
    settings = ClipSettings(**(clip.settings or {}))
    if export_override:
        settings.export = export_override
    sig = _clip_render_signature(clip, settings, purpose)
    h = render_hash(sig)
    if purpose == "preview":
        if clip.preview_status == "ready" and clip.preview_hash == h and clip.preview_key \
                and storage.exists(clip.preview_key):
            return {"cached": True}
        clip.preview_status = "rendering"
    else:
        if clip.export_status == "ready" and clip.export_hash == h and clip.export_key \
                and storage.exists(clip.export_key):
            return {"cached": True, **clip.export_info}
        clip.export_status = "rendering"
    clip.render_error = None
    db.commit()

    work = project_work_dir(project.id) / "renders" / f"{clip.id}-{purpose}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    out = work / "out.mp4"
    spec = RenderSpec(source_path=Path(project.source_media_path), start=clip.start_time, end=clip.end_time,
                      settings=settings, caption_words=clip.caption_words or [], vision=vision,
                      facecam=(clip.layout or {}).get("facecam"), src_w=minfo.width, src_h=minfo.height,
                      src_fps=minfo.fps, has_audio=minfo.has_audio, purpose=purpose)
    t0 = time.monotonic()
    info = build_render(spec, work, out)
    info["render_seconds"] = round(time.monotonic() - t0, 2)
    key = f"projects/{project.id}/clips/{clip.id}/{purpose}-{h[:12]}.mp4"
    storage.put_file(out, key, content_type_for(out))
    old_key = clip.preview_key if purpose == "preview" else clip.export_key
    if old_key and old_key != key:
        try:
            storage.delete(old_key)
        except Exception:  # noqa: BLE001
            pass
    if purpose == "preview":
        clip.preview_key, clip.preview_status, clip.preview_hash = key, "ready", h
    else:
        clip.export_key, clip.export_status, clip.export_hash = key, "ready", h
        clip.export_info = info
    db.commit()
    shutil.rmtree(work, ignore_errors=True)
    return info


def run_render(job_id: str) -> None:
    tracker = JobTracker(job_id, RENDER_STAGES)
    tracker.start()
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            return
        clip = db.get(Clip, job.clip_id) if job.clip_id else None
        project = db.get(Project, job.project_id)
        if not clip or not project:
            tracker.fail("Clip not found")
            return
        purpose = job.params.get("purpose", "export")
        try:
            source = ensure_source(db, project, tracker)
            minfo = probe(source)
            vision = _vision_for(db, project)
            tracker.set_stage("rendering")
            override = ExportSettings(**job.params["export"]) if job.params.get("export") else None
            info = _render_clip_file(db, project, clip, purpose, minfo, vision, get_storage(),
                                     export_override=override, tracker=tracker)
            storage = get_storage()
            key = clip.export_key if purpose == "export" else clip.preview_key
            tracker.complete({"clip_id": clip.id, "purpose": purpose, "url": storage.url_for(key) if key else None,
                              **{k: v for k, v in info.items() if k != "cached"}})
        except ClipsError as exc:
            if purpose == "preview":
                clip.preview_status = "failed"
            else:
                clip.export_status = "failed"
            clip.render_error = exc.message
            db.commit()
            tracker.fail(exc.message, code=exc.code)
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("render job %s crashed", job_id)
            if purpose == "preview":
                clip.preview_status = "failed"
            else:
                clip.export_status = "failed"
            clip.render_error = str(exc)[:300]
            db.commit()
            tracker.fail(f"Unexpected error: {str(exc)[:300]}", code="internal_error")
            raise


def run_zip(job_id: str) -> None:
    import zipfile

    tracker = JobTracker(job_id, ZIP_STAGES)
    tracker.start()
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            return
        project = db.get(Project, job.project_id)
        if not project:
            tracker.fail("Project not found")
            return
        try:
            clip_ids = job.params.get("clip_ids") or [c.id for c in project.clips if not c.is_deleted]
            clips = [c for c in project.clips if c.id in clip_ids and not c.is_deleted]
            if not clips:
                raise ProcessingFailed("No clips selected")
            override = ExportSettings(**job.params["export"]) if job.params.get("export") else None
            storage = get_storage()
            tracker.set_stage("rendering")
            needs_render = []
            for c in clips:
                settings = ClipSettings(**(c.settings or {}))
                if override:
                    settings.export = override
                h = render_hash(_clip_render_signature(c, settings, "export"))
                if not (c.export_status == "ready" and c.export_hash == h and c.export_key and storage.exists(c.export_key)):
                    needs_render.append(c)
            if needs_render:
                source = ensure_source(db, project, tracker)
                minfo = probe(source)
                vision = _vision_for(db, project)
                tracker.set_stage("rendering")
                for i, c in enumerate(needs_render):
                    tracker.progress(i / len(needs_render), f"Exporting clip {i + 1} of {len(needs_render)}", force=True)
                    _render_clip_file(db, project, c, "export", minfo, vision, storage, export_override=override)
            tracker.set_stage("packaging")
            work = project_work_dir(project.id) / "zips"
            work.mkdir(parents=True, exist_ok=True)
            zpath = work / f"{job.id}.zip"
            with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_STORED) as zf:
                for i, c in enumerate(clips):
                    local = work / f"{c.id}.mp4"
                    storage.get_file(c.export_key, local)
                    safe = "".join(ch if ch.isalnum() or ch in " -_" else "" for ch in c.title).strip()[:60] or "clip"
                    zf.write(local, f"{i + 1:02d} - {safe}.mp4")
                    local.unlink(missing_ok=True)
                    tracker.progress((i + 1) / len(clips), force=True)
            key = f"projects/{project.id}/exports/{job.id}.zip"
            storage.put_file(zpath, key, "application/zip")
            zpath.unlink(missing_ok=True)
            tracker.complete({"url": storage.url_for(key), "key": key, "clip_ids": [c.id for c in clips],
                              "count": len(clips)})
        except ClipsError as exc:
            tracker.fail(exc.message, code=exc.code)
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("zip job %s crashed", job_id)
            tracker.fail(f"Unexpected error: {str(exc)[:300]}", code="internal_error")
            raise


# ======================================================================================
# Retention / cleanup
# ======================================================================================
def cleanup_expired() -> dict:
    s = get_settings()
    now = datetime.now(timezone.utc)
    removed_sources = removed_outputs = removed_uploads = 0
    with SessionLocal() as db:
        projects = db.query(Project).all()
        storage = get_storage()
        for p in projects:
            active = any(j.status in ("queued", "running") for j in p.jobs)
            if active:
                continue
            if p.source_media_path and p.source_expires_at and p.source_expires_at < now:
                work = s.work_dir / "projects" / p.id
                for name in ("source.mp4", "source.mkv", "source.webm", "source.mov", "analysis.wav"):
                    f = work / name
                    if f.exists():
                        f.unlink()
                        removed_sources += 1
                shutil.rmtree(work / "renders", ignore_errors=True)
                p.source_media_path = None
            if p.created_at and p.created_at < now - timedelta(days=s.output_retention_days):
                for c in p.clips:
                    for key in (c.preview_key, c.export_key):
                        if key:
                            try:
                                storage.delete(key)
                                removed_outputs += 1
                            except Exception:  # noqa: BLE001
                                pass
                    c.preview_key = c.export_key = None
                    c.preview_status, c.export_status = "expired", "none"
        db.commit()
    for d in s.upload_dir.iterdir() if s.upload_dir.exists() else []:
        try:
            if d.is_dir() and datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc) < now - timedelta(hours=48):
                shutil.rmtree(d, ignore_errors=True)
                removed_uploads += 1
        except OSError:
            pass
    return {"sources": removed_sources, "outputs": removed_outputs, "uploads": removed_uploads}
