"""Media acquisition adapters.

The pipeline never touches a download tool directly. Every source goes through an adapter
that (1) identifies the source, (2) fetches metadata through permitted mechanisms,
(3) decides whether media retrieval is *authorized* for this deployment, and (4) obtains the
source media. If step 3 says no, the job fails with a real error - no fallback media is ever
substituted.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from app.services.errors import UnsupportedSource

if TYPE_CHECKING:
    from app.schemas import VideoInfo

ProgressCallback = Callable[[float, str], None]


@dataclass
class SourceRequest:
    url: str
    kind: str  # youtube | direct_url | upload | local_file
    video_id: str | None = None
    canonical_url: str = ""


@dataclass
class VideoMetadata:
    title: str | None = None
    author: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: float | None = None
    channel_id: str | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AccessDecision:
    allowed: bool
    adapter: str
    reason: str


@dataclass
class AcquiredMedia:
    path: Path
    adapter: str
    details: dict = field(default_factory=dict)


class MediaAdapter:
    name: str = "base"
    kind: str = "base"

    def enabled(self) -> bool:
        return True

    def can_handle(self, url: str) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def identify(self, url: str) -> SourceRequest:  # pragma: no cover - interface
        raise NotImplementedError

    def fetch_metadata(self, req: SourceRequest) -> VideoMetadata:  # pragma: no cover - interface
        raise NotImplementedError

    def check_access(self, req: SourceRequest, meta: VideoMetadata) -> AccessDecision:  # pragma: no cover
        raise NotImplementedError

    def acquire(self, req: SourceRequest, dest_dir: Path, progress: ProgressCallback) -> AcquiredMedia:
        raise NotImplementedError  # pragma: no cover


def _adapters() -> list[MediaAdapter]:
    from app.services.media.direct_url import DirectUrlAdapter
    from app.services.media.local_file import LocalFileAdapter
    from app.services.media.upload import UploadAdapter
    from app.services.media.youtube_adapter import YouTubeAdapter

    return [YouTubeAdapter(), UploadAdapter(), LocalFileAdapter(), DirectUrlAdapter()]


def get_adapter_for(url: str) -> MediaAdapter:
    url = (url or "").strip()
    for adapter in _adapters():
        if adapter.can_handle(url):
            if not adapter.enabled():
                raise UnsupportedSource(f"{adapter.name} sources are disabled on this deployment")
            return adapter
    raise UnsupportedSource("Unsupported source. Paste a YouTube link, a direct media URL or an upload reference.")


def describe_source(url: str) -> tuple[MediaAdapter, SourceRequest, VideoMetadata, AccessDecision, "VideoInfo"]:
    from app.schemas import AccessInfo, VideoInfo

    adapter = get_adapter_for(url)
    req = adapter.identify(url)
    meta = adapter.fetch_metadata(req)
    access = adapter.check_access(req, meta)
    info = VideoInfo(
        source_kind=req.kind,
        video_id=req.video_id,
        canonical_url=req.canonical_url or req.url,
        title=meta.title,
        author=meta.author,
        thumbnail_url=meta.thumbnail_url,
        duration_seconds=meta.duration_seconds,
        access=AccessInfo(allowed=access.allowed, adapter=access.adapter, reason=access.reason),
    )
    return adapter, req, meta, access, info
