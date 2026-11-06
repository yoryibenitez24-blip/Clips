"""Upload adapter: files previously stored via POST /api/videos/upload (upload://<token>)."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

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

_TOKEN_RE = re.compile(r"^upload://([a-f0-9]{32})$")


def upload_path(token: str) -> Path | None:
    d = get_settings().upload_dir / token
    if not d.is_dir():
        return None
    files = [p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")]
    return files[0] if files else None


class UploadAdapter(MediaAdapter):
    name = "upload"
    kind = "upload"

    def enabled(self) -> bool:
        return get_settings().upload_ingest_enabled

    def can_handle(self, url: str) -> bool:
        return url.startswith("upload://")

    def identify(self, url: str) -> SourceRequest:
        m = _TOKEN_RE.match(url.strip())
        if not m:
            raise InvalidURL("Invalid upload reference")
        return SourceRequest(url=url.strip(), kind="upload", video_id=m.group(1), canonical_url=url.strip())

    def fetch_metadata(self, req: SourceRequest) -> VideoMetadata:
        path = upload_path(req.video_id or "")
        if not path:
            raise RetrievalFailed("The uploaded file is no longer available. Upload it again.")
        info = probe(path)
        return VideoMetadata(title=Path(path).stem, author="Upload", duration_seconds=info.duration,
                             extra={"filename": path.name, **info.to_dict()})

    def check_access(self, req: SourceRequest, meta: VideoMetadata) -> AccessDecision:
        return AccessDecision(True, self.name, "Uploaded files are processed on the basis that you hold the rights.")

    def acquire(self, req: SourceRequest, dest_dir: Path, progress: ProgressCallback) -> AcquiredMedia:
        path = upload_path(req.video_id or "")
        if not path:
            raise RetrievalFailed("The uploaded file is no longer available. Upload it again.")
        dest = dest_dir / f"source{path.suffix.lower() or '.mp4'}"
        try:
            dest.hardlink_to(path)
        except OSError:
            shutil.copyfile(path, dest)
        progress(1.0, "Upload linked")
        return AcquiredMedia(path=dest, adapter=self.name, details={"filename": path.name})
