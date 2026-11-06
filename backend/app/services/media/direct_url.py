"""Direct media URL adapter: a licensed MP4/WebM/MOV reachable over public http(s)."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from app.config import get_settings
from app.services.errors import LimitExceeded, RetrievalFailed, UnsupportedSource
from app.services.media.base import (
    AccessDecision,
    AcquiredMedia,
    MediaAdapter,
    ProgressCallback,
    SourceRequest,
    VideoMetadata,
)
from app.services.netguard import validate_public_http_url
from app.services.youtube import is_youtube_url

_VIDEO_TYPES = ("video/", "application/octet-stream", "binary/octet-stream")
_VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv")
_MAX_REDIRECTS = 5


class DirectUrlAdapter(MediaAdapter):
    name = "direct_url"
    kind = "direct_url"

    def enabled(self) -> bool:
        return get_settings().direct_url_ingest_enabled

    def can_handle(self, url: str) -> bool:
        return url.startswith(("http://", "https://")) and not is_youtube_url(url)

    def identify(self, url: str) -> SourceRequest:
        safe = validate_public_http_url(url, resolve=False)
        return SourceRequest(url=safe, kind="direct_url", canonical_url=safe)

    def _head(self, url: str) -> tuple[str, httpx.Headers]:
        """HEAD with manual, SSRF-checked redirects. Returns (final_url, headers)."""
        current = url
        with httpx.Client(follow_redirects=False, timeout=20.0) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                validate_public_http_url(current)
                r = client.head(current)
                if r.is_redirect and r.headers.get("location"):
                    current = str(r.next_request.url) if r.next_request else r.headers["location"]
                    continue
                if r.status_code == 405:  # HEAD not allowed -> probe with a 1-byte range GET
                    r = client.get(current, headers={"Range": "bytes=0-0"})
                if r.status_code >= 400:
                    raise RetrievalFailed(f"The media URL returned HTTP {r.status_code}")
                return current, r.headers
        raise RetrievalFailed("Too many redirects")

    def fetch_metadata(self, req: SourceRequest) -> VideoMetadata:
        s = get_settings()
        final, headers = self._head(req.url)
        ctype = (headers.get("content-type") or "").split(";")[0].strip().lower()
        path = unquote(urlsplit(final).path)
        ext = Path(path).suffix.lower()
        if not (ctype.startswith(_VIDEO_TYPES) or ext in _VIDEO_EXT):
            raise UnsupportedSource(f"URL does not point to a video file (content-type {ctype or 'unknown'})")
        length = headers.get("content-length") or headers.get("content-range", "").split("/")[-1]
        size = int(length) if length and length.isdigit() else None
        if size and size > s.max_source_bytes:
            raise LimitExceeded("The media file is larger than the configured limit")
        title = Path(path).stem.replace("_", " ").replace("-", " ").strip() or urlsplit(final).hostname
        return VideoMetadata(title=title, author=urlsplit(final).hostname, extra={"content_type": ctype,
                                                                                   "size_bytes": size,
                                                                                   "final_url": final})

    def check_access(self, req: SourceRequest, meta: VideoMetadata) -> AccessDecision:
        return AccessDecision(True, self.name,
                              "Direct media URLs are processed on the basis that you hold the rights to the file.")

    def acquire(self, req: SourceRequest, dest_dir: Path, progress: ProgressCallback) -> AcquiredMedia:
        s = get_settings()
        final, headers = self._head(req.url)
        ext = Path(unquote(urlsplit(final).path)).suffix.lower() or ".mp4"
        if ext not in _VIDEO_EXT:
            ext = ".mp4"
        dest = dest_dir / f"source{ext}"
        total = int(headers.get("content-length") or 0)
        done = 0
        with httpx.Client(follow_redirects=False, timeout=httpx.Timeout(30.0, read=120.0)) as client:
            with client.stream("GET", final) as r:
                if r.status_code >= 400:
                    raise RetrievalFailed(f"The media URL returned HTTP {r.status_code}")
                with dest.open("wb") as fh:
                    for chunk in r.iter_bytes(1024 * 1024):
                        fh.write(chunk)
                        done += len(chunk)
                        if done > s.max_source_bytes:
                            dest.unlink(missing_ok=True)
                            raise LimitExceeded("Source media exceeds the configured size limit")
                        if total:
                            progress(min(0.99, done / total), f"Retrieving media ({done / 1e6:.0f}/{total / 1e6:.0f} MB)")
        return AcquiredMedia(path=dest, adapter=self.name, details={"bytes": done, "url": final})
