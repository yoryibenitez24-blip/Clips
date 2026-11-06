"""Server capabilities exposed to the UI so it only offers what the backend can actually do."""
from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.schemas import CAPTION_PRESETS, DURATION_RANGES

_PREFERRED_FONTS = ["Inter", "Inter Display", "DejaVu Sans", "Liberation Sans", "FreeSans", "Noto Sans",
                    "Roboto", "Montserrat", "Open Sans", "Arial", "Helvetica"]


@lru_cache
def installed_fonts() -> list[str]:
    try:
        out = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ["DejaVu Sans"]
    families: set[str] = set()
    for line in out.splitlines():
        for fam in line.split(","):
            fam = fam.strip()
            if fam and not fam.lower().startswith(("noto color", "noto sans mono", "dejavu sans mono")):
                families.add(fam)
    preferred = [f for f in _PREFERRED_FONTS if f in families]
    rest = sorted(f for f in families if f not in preferred and not f.endswith(("Mono", "Symbols")))
    return (preferred + rest)[:40] or ["DejaVu Sans"]


def music_tracks() -> list[dict]:
    s = get_settings()
    if not s.music_library_dir or not Path(s.music_library_dir).is_dir():
        return []
    tracks = []
    for p in sorted(Path(s.music_library_dir).iterdir()):
        if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg", ".flac") and p.is_file():
            tracks.append({"id": p.name, "name": p.stem.replace("_", " ")})
    return tracks


def options_payload() -> dict:
    s = get_settings()
    return {
        "clip_durations": [{"id": k, "min": v[0], "max": v[1], "target": v[2]} for k, v in DURATION_RANGES.items()],
        "clip_styles": ["gaming", "funny", "best", "reactions", "cinematic", "talking"],
        "orientations": ["9:16", "16:9", "1:1"],
        "reframe_modes": ["auto", "fill", "fit"],
        "caption_presets": CAPTION_PRESETS,
        "caption_positions": ["top", "middle", "bottom"],
        "fonts": installed_fonts(),
        "music_tracks": music_tracks(),
        "resolutions": ["720p", "1080p"],
        "fps_options": [24, 30, 60],
        "languages": ["auto", *s.transcription_language_list],
        "limits": {"max_source_duration_seconds": s.max_source_duration_seconds,
                   "max_upload_bytes": s.max_upload_bytes, "rate_limit_per_hour": s.rate_limit_analyze_per_hour},
        "ingest": {
            "youtube_metadata": True,
            "youtube_media_retrieval": s.youtube_media_retrieval == "authorized",
            "direct_url": s.direct_url_ingest_enabled,
            "upload": s.upload_ingest_enabled,
        },
        "transcription_model": s.whisper_model,
        "llm_ranking": bool(s.anthropic_api_key and s.llm_highlight_selection),
    }
