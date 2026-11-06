"""Local file adapter (development/tests): file:// URLs restricted to LOCAL_MEDIA_DIR."""
from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.config import get_settings
from app.services.errors import InvalidURL, RetrievalFailed
from app.services.ffmpeg import probe
from app.services.media.base import (
    AccessDecision,
    AcquiredMedia,
    MediaAdapter,
    ProgressCallback,
    SourceRequest,
    VideoMetadata,
)


class LocalFileAdapter(MediaAdapter):
    name = "local_file"
    kind = "local_file"

    def enabled(self) -> bool:
        s = get_settings()
        return bool(s.local_file_ingest_enabled and s.local_media_dir)

    def can_handle(self, url: str) -> bool:
        return url.startswith("file://")

    def _resolve(self, url: str) -> Path:
        s = get_settings()
        root = Path(s.local_media_dir or "").resolve()
        p = Path(unquote(urlsplit(url).path)).resolve()
        if root not in p.parents:
            raise InvalidURL("Local files must live inside LOCAL_MEDIA_DIR")
        if not p.is_file():
            raise RetrievalFailed("Local file not found")
        return p

    def identify(self, url: str) -> SourceRequest:
        p = self._resolve(url)
        return SourceRequest(url=url, kind="local_file", video_id=p.stem, canonical_url=url)

    def fetch_metadata(self, req: SourceRequest) -> VideoMetadata:
        p = self._resolve(req.url)
        info = probe(p)
        return VideoMetadata(title=p.stem, author="Local file", duration_seconds=info.duration,
                             extra=info.to_dict())

    def check_access(self, req: SourceRequest, meta: VideoMetadata) -> AccessDecision:
        return AccessDecision(True, self.name, "Local development source")

    def acquire(self, req: SourceRequest, dest_dir: Path, progress: ProgressCallback) -> AcquiredMedia:
        p = self._resolve(req.url)
        dest = dest_dir / f"source{p.suffix.lower()}"
        try:
            dest.hardlink_to(p)
        except OSError:
            shutil.copyfile(p, dest)
        progress(1.0, "Local file linked")
        return AcquiredMedia(path=dest, adapter=self.name)
