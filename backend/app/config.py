"""Application configuration.

Everything is driven by environment variables so the same image runs as the API,
the Celery worker and the beat scheduler. See `.env.example` at the repo root.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Clips"
    environment: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"
    api_base_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:3000"

    # --- persistence -----------------------------------------------------------------
    database_url: str = "postgresql+psycopg://clips:clips@localhost:5432/clips"
    redis_url: str = "redis://localhost:6379/0"

    # --- storage ---------------------------------------------------------------------
    storage_backend: Literal["local", "s3"] = "local"
    local_storage_dir: Path = Path("./data/storage")
    s3_endpoint_url: str | None = None
    s3_bucket: str = "clips"
    s3_region: str = "us-east-1"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_public_base_url: str | None = None  # optional CDN / public bucket URL
    s3_presign_expiry_seconds: int = 3600

    # --- working directories ---------------------------------------------------------
    work_dir: Path = Path("./data/work")
    upload_dir: Path = Path("./data/uploads")
    local_media_dir: Path | None = None  # dev/test only: allows file:// URLs under this dir
    whisper_cache_dir: Path = Path("./data/models")
    music_library_dir: Path | None = None  # operator-provided, licensed background music

    # --- media acquisition policy ----------------------------------------------------
    # YouTube media retrieval is OFF by default. YouTube's API Terms of Service prohibit
    # downloading or storing audiovisual content without prior written approval. Only set
    # this to "authorized" when you hold the required permission (own content, licensed
    # content, or written approval from YouTube) for the material processed by this
    # deployment. Metadata lookups (title/thumbnail via oEmbed, duration via the Data API)
    # do not retrieve media and are always available.
    youtube_media_retrieval: Literal["disabled", "authorized"] = "disabled"
    youtube_retrieval_notice: str = (
        "Operator attests that media retrieved by this deployment is covered by the "
        "required permissions or licences."
    )
    youtube_allowed_channel_ids: str = ""  # optional comma list restricting retrieval to given channels
    youtube_api_key: str | None = None  # YouTube Data API v3 key (metadata only)
    ytdlp_cookies_file: Path | None = None
    ytdlp_format: str = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best"
    direct_url_ingest_enabled: bool = True
    upload_ingest_enabled: bool = True
    local_file_ingest_enabled: bool = False

    # --- limits ----------------------------------------------------------------------
    max_source_duration_seconds: int = 3 * 60 * 60
    max_source_bytes: int = 6 * 1024 * 1024 * 1024
    max_upload_bytes: int = 4 * 1024 * 1024 * 1024
    max_active_analysis_jobs: int = 4
    rate_limit_analyze_per_hour: int = 10
    min_free_disk_bytes: int = 2 * 1024 * 1024 * 1024
    source_retention_hours: int = 24
    output_retention_days: int = 14
    job_max_retries: int = 2

    # --- analysis --------------------------------------------------------------------
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    transcription_languages: str = "en,es"
    transcription_required: bool = False
    scene_threshold: float = 27.0
    analysis_fps: float = 1.0  # sampled frames per second for vision analysis
    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5-5"
    llm_highlight_selection: bool = True

    # --- rendering -------------------------------------------------------------------
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    ffmpeg_threads: int = 0
    preview_height: int = 960
    render_version: int = 1

    # --- celery ----------------------------------------------------------------------
    celery_task_always_eager: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def transcription_language_list(self) -> list[str]:
        return [o.strip() for o in self.transcription_languages.split(",") if o.strip()]

    @property
    def youtube_allowed_channels(self) -> set[str]:
        return {c.strip() for c in self.youtube_allowed_channel_ids.split(",") if c.strip()}

    def ensure_dirs(self) -> None:
        for d in (self.work_dir, self.upload_dir, self.whisper_cache_dir):
            Path(d).mkdir(parents=True, exist_ok=True)
        if self.storage_backend == "local":
            Path(self.local_storage_dir).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
