"""YouTube URL validation + video-id extraction (pure, no network)."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from app.services.errors import InvalidURL

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com",
             "www.youtube-nocookie.com"}
_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}


def is_youtube_url(url: str) -> bool:
    try:
        host = (urlsplit(url.strip()).hostname or "").lower()
    except ValueError:
        return False
    return host in _YT_HOSTS or host in _SHORT_HOSTS


def extract_video_id(url: str) -> str:
    """Accepts watch, youtu.be, live, shorts, embed and /v/ URLs. Raises InvalidURL otherwise."""
    url = url.strip()
    if not url:
        raise InvalidURL("Paste a YouTube link to get started")
    if "://" not in url:
        url = "https://" + url
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise InvalidURL("That doesn't look like a valid URL") from exc
    if parts.scheme not in ("http", "https"):
        raise InvalidURL("Only http(s) YouTube links are supported")
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    candidate: str | None = None

    if host in _SHORT_HOSTS:
        candidate = path.strip("/").split("/")[0]
    elif host in _YT_HOSTS:
        if path == "/watch":
            candidate = parse_qs(parts.query).get("v", [None])[0]
        else:
            m = re.match(r"^/(live|shorts|embed|v)/([A-Za-z0-9_-]{11})", path)
            if m:
                candidate = m.group(2)
    else:
        raise InvalidURL("Only YouTube links are supported here")

    if not candidate or not _VIDEO_ID_RE.match(candidate):
        raise InvalidURL("Could not find a video ID in that YouTube link")
    return candidate


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def parse_iso8601_duration(value: str) -> float:
    """YouTube Data API returns ISO-8601 durations such as PT1H2M3S."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not m:
        raise ValueError(f"bad duration {value!r}")
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s
