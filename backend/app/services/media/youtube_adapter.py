"""YouTube source adapter.

Metadata: oEmbed (title, author, thumbnail) and, when a Data API key is configured, the
Data API v3 (duration, channel). Neither retrieves media.

Media retrieval uses yt-dlp and is only available when the operator has explicitly set
YOUTUBE_MEDIA_RETRIEVAL=authorized, attesting that the deployment processes content it is
permitted to retrieve (own videos, licensed content, or prior written approval from YouTube).
Technical availability of a downloader does not grant permission; the default is disabled.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from app.config import get_settings
from app.services.errors import AccessDenied, LimitExceeded, RetrievalFailed
from app.services.media.base import (
    AccessDecision,
    AcquiredMedia,
    MediaAdapter,
    ProgressCallback,
    SourceRequest,
    VideoMetadata,
)
from app.services.youtube import canonical_url, extract_video_id, is_youtube_url, parse_iso8601_duration

log = logging.getLogger(__name__)

_OEMBED = "https://www.youtube.com/oembed"
_DATA_API = "https://www.googleapis.com/youtube/v3/videos"


class YouTubeAdapter(MediaAdapter):
    name = "youtube"
    kind = "youtube"

    def can_handle(self, url: str) -> bool:
        return is_youtube_url(url if "://" in url else "https://" + url)

    def identify(self, url: str) -> SourceRequest:
        vid = extract_video_id(url)
        return SourceRequest(url=url.strip(), kind="youtube", video_id=vid, canonical_url=canonical_url(vid))

    # ------------------------------------------------------------------ metadata
    def fetch_metadata(self, req: SourceRequest) -> VideoMetadata:
        s = get_settings()
        meta = VideoMetadata()
        errors: list[str] = []
        timeout = httpx.Timeout(12.0)

        if s.youtube_api_key:
            try:
                r = httpx.get(_DATA_API, params={"part": "snippet,contentDetails", "id": req.video_id,
                                                 "key": s.youtube_api_key}, timeout=timeout)
                r.raise_for_status()
                items = r.json().get("items") or []
                if not items:
                    raise RetrievalFailed("This video does not exist or is private")
                sn, cd = items[0].get("snippet", {}), items[0].get("contentDetails", {})
                meta.title = sn.get("title")
                meta.author = sn.get("channelTitle")
                meta.channel_id = sn.get("channelId")
                thumbs = sn.get("thumbnails") or {}
                best = thumbs.get("maxres") or thumbs.get("standard") or thumbs.get("high") or thumbs.get("medium")
                meta.thumbnail_url = (best or {}).get("url")
                if cd.get("duration"):
                    meta.duration_seconds = parse_iso8601_duration(cd["duration"])
                meta.extra["live_broadcast"] = sn.get("liveBroadcastContent")
            except RetrievalFailed:
                raise
            except Exception as exc:  # network / quota
                errors.append(f"data_api: {exc}")

        if not meta.title:
            try:
                r = httpx.get(_OEMBED, params={"url": req.canonical_url, "format": "json"}, timeout=timeout)
                if r.status_code in (401, 403):
                    raise RetrievalFailed("This video is private or embedding is disabled, so it cannot be processed")
                if r.status_code == 404:
                    raise RetrievalFailed("This YouTube video could not be found")
                r.raise_for_status()
                data = r.json()
                meta.title = data.get("title")
                meta.author = data.get("author_name")
                meta.thumbnail_url = data.get("thumbnail_url")
            except RetrievalFailed:
                raise
            except Exception as exc:
                errors.append(f"oembed: {exc}")

        if meta.duration_seconds is None and s.youtube_media_retrieval == "authorized":
            # yt-dlp metadata extraction (no download) - only on authorized deployments
            try:
                info = self._ytdlp_info(req)
                meta.duration_seconds = info.get("duration")
                meta.title = meta.title or info.get("title")
                meta.author = meta.author or info.get("uploader") or info.get("channel")
                meta.channel_id = meta.channel_id or info.get("channel_id")
                meta.thumbnail_url = meta.thumbnail_url or info.get("thumbnail")
                meta.extra["is_live"] = bool(info.get("is_live"))
                meta.extra["width"] = info.get("width")
                meta.extra["height"] = info.get("height")
            except Exception as exc:
                errors.append(f"yt-dlp: {exc}")

        if not meta.title:
            raise RetrievalFailed(
                "Could not retrieve video metadata from YouTube. " + ("; ".join(errors)[:300] if errors else "")
            )
        if not meta.thumbnail_url and req.video_id:
            meta.thumbnail_url = f"https://i.ytimg.com/vi/{req.video_id}/hqdefault.jpg"
        meta.extra["metadata_errors"] = errors
        return meta

    # ------------------------------------------------------------------ policy
    def check_access(self, req: SourceRequest, meta: VideoMetadata) -> AccessDecision:
        s = get_settings()
        if s.youtube_media_retrieval != "authorized":
            return AccessDecision(
                allowed=False,
                adapter=self.name,
                reason=(
                    "YouTube media retrieval is not enabled on this deployment. YouTube's API Terms of "
                    "Service do not permit downloading or storing audiovisual content without prior written "
                    "approval, so this server only shows metadata for YouTube links. The operator can enable "
                    "retrieval for content they own or hold a licence for (YOUTUBE_MEDIA_RETRIEVAL=authorized), "
                    "or you can process a licensed direct media URL / upload instead."
                ),
            )
        if meta.extra.get("is_live") or meta.extra.get("live_broadcast") == "live":
            return AccessDecision(False, self.name, "Live streams that are still running cannot be clipped yet.")
        allowed = s.youtube_allowed_channels
        if allowed:
            if not meta.channel_id:
                return AccessDecision(False, self.name,
                                      "Channel allow-list is configured but the channel could not be verified "
                                      "(set YOUTUBE_API_KEY so the Data API can resolve it).")
            if meta.channel_id not in allowed:
                return AccessDecision(False, self.name,
                                      "This deployment is only authorized to process videos from specific channels.")
        if meta.duration_seconds and meta.duration_seconds > s.max_source_duration_seconds:
            return AccessDecision(False, self.name,
                                  f"Video is longer than the {s.max_source_duration_seconds // 60} minute limit.")
        return AccessDecision(True, self.name, s.youtube_retrieval_notice)

    # ------------------------------------------------------------------ retrieval
    def _ydl_opts(self, dest_dir: Path | None = None) -> dict:
        s = get_settings()
        opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": s.ytdlp_format,
            "merge_output_format": "mp4",
            "max_filesize": s.max_source_bytes,
            "retries": 3,
            "fragment_retries": 3,
            "socket_timeout": 30,
            "noprogress": True,
        }
        if s.ytdlp_cookies_file:
            opts["cookiefile"] = str(s.ytdlp_cookies_file)
        if dest_dir is not None:
            opts["outtmpl"] = str(dest_dir / "source.%(ext)s")
        return opts

    def _ytdlp_info(self, req: SourceRequest) -> dict:
        import yt_dlp

        with yt_dlp.YoutubeDL(self._ydl_opts()) as ydl:
            return ydl.extract_info(req.canonical_url, download=False) or {}

    def acquire(self, req: SourceRequest, dest_dir: Path, progress: ProgressCallback) -> AcquiredMedia:
        s = get_settings()
        if s.youtube_media_retrieval != "authorized":
            raise AccessDenied("YouTube media retrieval is not authorized on this deployment")
        import yt_dlp

        def hook(d: dict) -> None:
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                if total:
                    progress(min(0.99, done / total), f"Retrieving media ({done / 1e6:.0f}/{total / 1e6:.0f} MB)")
                if total and total > s.max_source_bytes:
                    raise LimitExceeded("Source media exceeds the configured size limit")
            elif d.get("status") == "finished":
                progress(1.0, "Download finished, merging streams")

        opts = self._ydl_opts(dest_dir)
        opts["progress_hooks"] = [hook]
        opts["match_filter"] = yt_dlp.utils.match_filter_func(
            f"duration <= {s.max_source_duration_seconds} & !is_live")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(req.canonical_url, download=True)
                if info is None:
                    raise RetrievalFailed("yt-dlp returned no result for this video")
                path = Path(ydl.prepare_filename(info))
        except LimitExceeded:
            raise
        except yt_dlp.utils.DownloadError as exc:
            raise RetrievalFailed(f"Media retrieval failed: {str(exc)[:400]}") from exc
        # merged output may have a different extension than prepare_filename reports
        candidates = sorted(dest_dir.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
        final = next((c for c in candidates if c.suffix in (".mp4", ".mkv", ".webm")), path)
        if not final.exists():
            raise RetrievalFailed("Media retrieval finished but no file was produced")
        return AcquiredMedia(path=final, adapter=self.name,
                             details={"format_id": info.get("format_id"), "ext": final.suffix})
