"""Scene boundary detection with PySceneDetect (content detector, frame-skipped)."""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import get_settings

log = logging.getLogger(__name__)


def detect_scenes(video_path: Path, duration: float, fps: float, progress=None) -> dict:
    from scenedetect import ContentDetector, SceneManager, open_video

    s = get_settings()
    video = open_video(str(video_path), backend="opencv")
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
    return {"scenes": scenes, "cuts": cuts, "threshold": s.scene_threshold, "frame_skip": frame_skip}
