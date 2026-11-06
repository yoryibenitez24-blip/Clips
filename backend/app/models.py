"""SQLAlchemy models: Project (one source video), Job (background work), Clip (one output)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    source_url: Mapped[str] = mapped_column(Text)
    source_kind: Mapped[str] = mapped_column(String(32))  # youtube | direct_url | upload | local_file
    video_id: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    language: Mapped[str | None] = mapped_column(String(8))

    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|processing|completed|failed
    error: Mapped[str | None] = mapped_column(Text)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)  # generation settings chosen by the user
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    transcript: Mapped[dict | None] = mapped_column(JSON)
    analysis: Mapped[dict | None] = mapped_column(JSON)  # scenes, audio, vision summaries, warnings
    highlights: Mapped[list | None] = mapped_column(JSON)  # ranked candidate list (before clip creation)

    source_fingerprint: Mapped[str | None] = mapped_column(String(80), index=True)
    source_media_path: Mapped[str | None] = mapped_column(Text)  # local working copy (temporary)
    source_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_token: Mapped[str | None] = mapped_column(String(64), index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    clips: Mapped[list["Clip"]] = relationship(back_populates="project", cascade="all, delete-orphan",
                                               order_by="Clip.index")
    jobs: Mapped[list["Job"]] = relationship(back_populates="project", cascade="all, delete-orphan",
                                             order_by="Job.created_at")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    clip_id: Mapped[str | None] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # analyze | render | zip
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|completed|failed
    stage: Mapped[str | None] = mapped_column(String(32))
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage_history: Mapped[list] = mapped_column(JSON, default=list)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    celery_task_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="jobs")


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float | None] = mapped_column(Float)
    signals: Mapped[dict] = mapped_column(JSON, default=dict)

    # Effective boundaries in the SOURCE timeline (seconds). The detected boundaries are kept
    # separately so trims can be undone and so the UI can show the original selection.
    start_time: Mapped[float] = mapped_column(Float)
    end_time: Mapped[float] = mapped_column(Float)
    detected_start: Mapped[float] = mapped_column(Float)
    detected_end: Mapped[float] = mapped_column(Float)

    settings: Mapped[dict] = mapped_column(JSON, default=dict)  # orientation, reframe, captions, audio, export
    caption_words: Mapped[list] = mapped_column(JSON, default=list)  # [{start,end,text}] (source timeline)
    layout: Mapped[dict] = mapped_column(JSON, default=dict)  # facecam detection etc. (analysis output)

    preview_key: Mapped[str | None] = mapped_column(Text)
    preview_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|rendering|ready|failed
    preview_hash: Mapped[str | None] = mapped_column(String(64))
    export_key: Mapped[str | None] = mapped_column(Text)
    export_status: Mapped[str] = mapped_column(String(16), default="none")  # none|rendering|ready|failed
    export_hash: Mapped[str | None] = mapped_column(String(64))
    export_info: Mapped[dict] = mapped_column(JSON, default=dict)
    render_error: Mapped[str | None] = mapped_column(Text)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="clips")


class AnalysisCache(Base):
    """Caches expensive analysis (transcripts, scene lists, sampled vision) by source fingerprint."""

    __tablename__ = "analysis_cache"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
