"""Scene boundary detection with PySceneDetect (content detector, frame-skipped)."""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import get_settings
from app.services.errors import ProcessingFailed

log = logging.getLogger(__name__)


_PYAV_FIRST_CODECS = {"av1", "vp9"}  # OpenCV wheels ship an ffmpeg without dav1d; PyAV decodes these


def detect_scenes(video_path: Path, duration: float, fps: float, progress=None, codec: str | None = None) -> dict:
    backends = ["pyav", "opencv"] if (codec or "").lower() in _PYAV_FIRST_CODECS else ["opencv", "pyav"]
    last_error: Exception | None = None
    for backend in backends:
        try:
            result = _detect_with_backend(video_path, duration, fps, progress, backend)
        except Exception as exc:  # noqa: BLE001 - try the other decoder
            log.warning("scene detection with %s backend failed: %s", backend, exc)
            last_error = exc
            continue
        if result["frames_read"] > 0:
            return result
        log.warning("scene detection with %s backend decoded no frames (codec %s)", backend, codec)
    if last_error:
        raise ProcessingFailed(f"Scene detection failed: {last_error}") from last_error
    return {"scenes": [{"start": 0.0, "end": round(duration, 3)}], "cuts": [], "threshold": get_settings().scene_threshold,
            "frame_skip": 0, "backend": None, "frames_read": 0}


def _detect_with_backend(video_path: Path, duration: float, fps: float, progress, backend: str) -> dict:
    from scenedetect import ContentDetector, SceneManager, open_video

    s = get_settings()
    video = open_video(str(video_path), backend=backend)
    manager = SceneManager()
    manager.auto_downscale = True
    manager.add_detector(ContentDetector(threshold=s.scene_threshold, min_scene_len=int(max(fps, 1) * 0.6)))
    # analyse ~4 frames/second instead of every frame; long videos are still linear in decode cost
    frame_skip = max(0, int(round(fps / 4.0)) - 1) if fps > 8 else 0

    def cb(frame_img, position):  # noqa: ANN001
        if progress and duration:
            seconds = position.get_seconds() if hasattr(position, "get_seconds") else float(position) / max(fps, 1)
            progress(min(0.99, seconds / duration))

    manager.detect_scenes(video=video, frame_skip=frame_skip, callback=cb if progress else None,
                          show_progress=False)
    scene_list = manager.get_scene_list()
    scenes = [{"start": round(a.get_seconds(), 3), "end": round(b.get_seconds(), 3)} for a, b in scene_list]
    if not scenes:
        scenes = [{"start": 0.0, "end": round(duration, 3)}]
    cuts = [sc["start"] for sc in scenes[1:]]
    return {"scenes": scenes, "cuts": cuts, "threshold": s.scene_threshold, "frame_skip": frame_skip,
            "backend": backend, "frames_read": int(video.frame_number)}
