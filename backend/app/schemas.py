"""Pydantic schemas shared by the API layer and the processing pipeline."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ClipDuration = Literal["under_30", "30_60", "60_90", "auto"]
ClipStyle = Literal["gaming", "funny", "best", "reactions", "cinematic", "talking"]
Orientation = Literal["9:16", "16:9", "1:1"]
ReframeMode = Literal["auto", "fit", "fill"]
CaptionAnimation = Literal["word_pop", "karaoke", "bounce", "fade", "scale", "minimal", "gaming_bold"]
CaptionPosition = Literal["top", "middle", "bottom"]
Resolution = Literal["720p", "1080p"]

DURATION_RANGES: dict[str, tuple[float, float, float]] = {
    # min, max, target (seconds)
    "under_30": (12.0, 30.0, 22.0),
    "30_60": (30.0, 60.0, 45.0),
    "60_90": (60.0, 90.0, 75.0),
    "auto": (15.0, 75.0, 40.0),
}

CAPTION_PRESETS: dict[str, dict[str, Any]] = {
    "word_pop": {"font_family": "Inter", "font_size": 64, "color": "#FFFFFF", "highlight_color": "#A78BFA",
                 "outline_color": "#000000", "outline_width": 3, "shadow": 1, "position": "middle",
                 "max_words": 4, "uppercase": False, "bold": True},
    "karaoke": {"font_family": "Inter", "font_size": 60, "color": "#FFFFFF", "highlight_color": "#F5D90A",
                "outline_color": "#000000", "outline_width": 3, "shadow": 1, "position": "bottom",
                "max_words": 5, "uppercase": False, "bold": True},
    "bounce": {"font_family": "Inter", "font_size": 66, "color": "#FFFFFF", "highlight_color": "#22D3EE",
               "outline_color": "#1E1B4B", "outline_width": 4, "shadow": 2, "position": "middle",
               "max_words": 3, "uppercase": True, "bold": True},
    "fade": {"font_family": "Inter", "font_size": 56, "color": "#FFFFFF", "highlight_color": "#FFFFFF",
             "outline_color": "#000000", "outline_width": 2, "shadow": 1, "position": "bottom",
             "max_words": 6, "uppercase": False, "bold": False},
    "scale": {"font_family": "Inter", "font_size": 62, "color": "#FFFFFF", "highlight_color": "#C4B5FD",
              "outline_color": "#000000", "outline_width": 3, "shadow": 1, "position": "middle",
              "max_words": 4, "uppercase": False, "bold": True},
    "minimal": {"font_family": "Inter", "font_size": 46, "color": "#FFFFFF", "highlight_color": "#FFFFFF",
                "outline_color": "#000000", "outline_width": 1, "shadow": 0, "position": "bottom",
                "max_words": 7, "uppercase": False, "bold": False},
    "gaming_bold": {"font_family": "Inter", "font_size": 72, "color": "#FFFFFF", "highlight_color": "#84CC16",
                    "outline_color": "#000000", "outline_width": 5, "shadow": 3, "position": "middle",
                    "max_words": 3, "uppercase": True, "bold": True},
}


class CaptionSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    animation: CaptionAnimation = "word_pop"
    font_family: str = "Inter"
    font_size: int = Field(64, ge=16, le=160)
    color: str = "#FFFFFF"
    highlight_color: str = "#A78BFA"
    outline_color: str = "#000000"
    outline_width: int = Field(3, ge=0, le=12)
    shadow: int = Field(1, ge=0, le=8)
    position: CaptionPosition = "middle"
    max_words: int = Field(4, ge=1, le=10)
    uppercase: bool = False
    bold: bool = True

    @field_validator("color", "highlight_color", "outline_color")
    @classmethod
    def _hex(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("#") or len(v) not in (4, 7):
            raise ValueError("colors must be #RGB or #RRGGBB")
        int(v[1:], 16)
        if len(v) == 4:
            v = "#" + "".join(ch * 2 for ch in v[1:])
        return v.upper()

    @classmethod
    def from_preset(cls, animation: str, **overrides: Any) -> "CaptionSettings":
        base = dict(CAPTION_PRESETS.get(animation, CAPTION_PRESETS["word_pop"]))
        base["animation"] = animation
        base.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**base)


class AudioSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    normalize: bool = True
    source_volume: float = Field(1.0, ge=0.0, le=2.0)
    music_track: str | None = None  # filename inside the operator's licensed music library
    music_volume: float = Field(0.15, ge=0.0, le=1.0)


class ExportSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    resolution: Resolution = "1080p"
    fps: Literal[24, 30, 60] = 30
    bitrate_kbps: int | None = Field(None, ge=1000, le=50000)  # None -> CRF-based high quality
    crf: int = Field(18, ge=10, le=35)
    preset: Literal["slow", "medium", "fast", "veryfast"] = "medium"


class ClipSettings(BaseModel):
    """Everything that affects how one clip is rendered. Stored on `Clip.settings`."""

    model_config = ConfigDict(extra="ignore")

    orientation: Orientation = "9:16"
    reframe: ReframeMode = "auto"
    crop_offset_x: float = Field(0.0, ge=-1.0, le=1.0)  # manual nudge, fraction of free crop range
    crop_offset_y: float = Field(0.0, ge=-1.0, le=1.0)
    facecam_layout: bool = True  # use split layout when a facecam is reliably detected
    remove_silence: bool = False
    captions: CaptionSettings = Field(default_factory=CaptionSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)


# --------------------------------------------------------------------------------------
# API requests
# --------------------------------------------------------------------------------------


class InspectRequest(BaseModel):
    url: str = Field(..., min_length=5, max_length=2048)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    youtube_url: str = Field(..., alias="youtubeUrl", min_length=5, max_length=2048)
    clip_duration: ClipDuration = Field("auto", alias="clipDuration")
    clip_style: ClipStyle = Field("best", alias="clipStyle")
    orientation: Orientation = "9:16"
    caption_style: CaptionAnimation = Field("word_pop", alias="captionStyle")
    captions_enabled: bool = Field(True, alias="captionsEnabled")
    language: Literal["auto", "en", "es"] = "auto"
    max_clips: int = Field(8, alias="maxClips", ge=1, le=12)


class ClipPatch(BaseModel):
    """Partial update for a clip. Trims are expressed in the source timeline."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, max_length=200)
    start_time: float | None = Field(None, ge=0)
    end_time: float | None = Field(None, ge=0)
    settings: dict[str, Any] | None = None  # deep-merged into ClipSettings
    caption_preset: CaptionAnimation | None = None  # applies the full preset (font, colours, position...)
    caption_words: list[dict[str, Any]] | None = None
    caption_text: str | None = None  # convenience: replace words by re-splitting text over timing


class RenderRequest(BaseModel):
    purpose: Literal["preview", "export"] = "export"
    export: ExportSettings | None = None  # overrides clip export settings when given
    force: bool = False


class DownloadAllRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    clip_ids: list[str] | None = Field(None, alias="clipIds")
    export: ExportSettings | None = None


# --------------------------------------------------------------------------------------
# API responses
# --------------------------------------------------------------------------------------


class AccessInfo(BaseModel):
    allowed: bool
    adapter: str
    reason: str


class VideoInfo(BaseModel):
    source_kind: str
    video_id: str | None = None
    canonical_url: str
    title: str | None = None
    author: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: float | None = None
    access: AccessInfo


class JobOut(BaseModel):
    id: str
    project_id: str
    clip_id: str | None
    kind: str
    status: str
    stage: str | None
    stage_label: str | None
    progress: float
    stage_history: list
    result: dict | None
    error: str | None
    warnings: list
    metrics: dict
    attempts: int
    created_at: datetime
    updated_at: datetime


class ClipOut(BaseModel):
    id: str
    project_id: str
    index: int
    title: str
    reason: str
    score: float | None
    signals: dict
    start_time: float
    end_time: float
    detected_start: float
    detected_end: float
    duration: float
    settings: dict
    caption_words: list
    layout: dict
    preview_url: str | None
    preview_status: str
    export_url: str | None
    export_status: str
    export_info: dict
    render_error: str | None
    created_at: datetime
    updated_at: datetime


class ProjectOut(BaseModel):
    id: str
    source_url: str
    source_kind: str
    video_id: str | None
    title: str | None
    author: str | None
    thumbnail_url: str | None
    duration_seconds: float | None
    width: int | None
    height: int | None
    fps: float | None
    language: str | None
    status: str
    error: str | None
    settings: dict
    analysis_summary: dict
    source_available: bool
    clips: list[ClipOut] = []
    active_job: JobOut | None = None
    created_at: datetime
    updated_at: datetime


class ProjectSummary(BaseModel):
    id: str
    title: str | None
    thumbnail_url: str | None
    duration_seconds: float | None
    status: str
    clip_count: int
    created_at: datetime


class AnalyzeResponse(BaseModel):
    project_id: str
    job_id: str
    video: VideoInfo
