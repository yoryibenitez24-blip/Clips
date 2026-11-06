"""Thin wrappers around ffprobe/ffmpeg used by the analysis and rendering stages."""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.services.errors import ProcessingFailed

log = logging.getLogger(__name__)


@dataclass
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    video_codec: str | None
    size_bytes: int

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _parse_rate(rate: str | None) -> float:
    if not rate:
        return 0.0
    if "/" in rate:
        num, den = rate.split("/")
        return float(num) / float(den) if float(den) else 0.0
    return float(rate)


def probe(path: Path | str) -> MediaInfo:
    s = get_settings()
    cmd = [s.ffprobe_bin, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120).stdout
    except subprocess.CalledProcessError as exc:
        raise ProcessingFailed(f"ffprobe failed: {exc.stderr.strip()[:400]}") from exc
    data = json.loads(out)
    video = next((st for st in data.get("streams", []) if st.get("codec_type") == "video"), None)
    audio = next((st for st in data.get("streams", []) if st.get("codec_type") == "audio"), None)
    if not video:
        raise ProcessingFailed("The media file has no video stream")
    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or video.get("duration") or 0.0)
    fps = _parse_rate(video.get("avg_frame_rate")) or _parse_rate(video.get("r_frame_rate")) or 30.0
    return MediaInfo(
        duration=duration,
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        fps=round(fps, 3),
        has_audio=audio is not None,
        video_codec=video.get("codec_name"),
        size_bytes=int(fmt.get("size") or Path(path).stat().st_size),
    )


def run_ffmpeg(args: list[str], *, timeout: int = 3600, log_path: Path | None = None) -> None:
    s = get_settings()
    cmd = [s.ffmpeg_bin, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
    log.debug("ffmpeg %s", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ProcessingFailed("ffmpeg timed out") from exc
    if log_path is not None:
        log_path.write_text(" ".join(cmd) + "\n\n" + proc.stderr)
    if proc.returncode != 0:
        raise ProcessingFailed(f"ffmpeg failed: {proc.stderr.strip()[-800:]}")


def extract_analysis_audio(src: Path, dest: Path, sample_rate: int = 16000) -> Path:
    """Mono 16 kHz PCM used for both transcription and energy analysis."""
    run_ffmpeg(["-i", str(src), "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dest)],
               timeout=1800)
    return dest


def extract_thumbnail(src: Path, dest: Path, at: float, width: int = 640) -> Path:
    run_ffmpeg(["-ss", f"{max(at, 0):.3f}", "-i", str(src), "-frames:v", "1",
                "-vf", f"scale={width}:-2", "-q:v", "3", str(dest)], timeout=120)
    return dest
