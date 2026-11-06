"""Sampled-frame vision analysis: motion, motion centroid and face boxes.

Frames are sampled at `analysis_fps` (default 1 fps) and downscaled to 320 px wide, so even a
3-hour video is ~10k tiny frames. Results are cached per source fingerprint and reused by both
highlight ranking (motion / action) and auto reframing (subject positions).
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import cv2
import numpy as np

from app.config import get_settings
from app.services.errors import ProcessingFailed

log = logging.getLogger(__name__)

_WIDTH = 320
_cascade = None


def _face_cascade():
    """Haar face detector, or None when this OpenCV build has no cascade support (faces are then skipped)."""
    global _cascade
    if _cascade is None:
        try:
            path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
            cascade = cv2.CascadeClassifier(path)
            _cascade = None if cascade.empty() else cascade
        except AttributeError:
            _cascade = None
        if _cascade is None:
            log.warning("OpenCV %s has no usable face cascade; face/facecam detection disabled", cv2.__version__)
            _cascade = False
    return _cascade or None


def analyze_frames(video_path: Path, width: int, height: int, duration: float, progress=None,
                   fps: float | None = None) -> dict:
    s = get_settings()
    sample_fps = fps or s.analysis_fps
    w = _WIDTH
    h = max(2, int(round(w * height / max(width, 1) / 2)) * 2)
    cmd = [s.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(video_path),
           "-vf", f"fps={sample_fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    frame_bytes = w * h
    cascade = _face_cascade()

    times: list[float] = []
    motion: list[float] = []
    cx: list[float] = []
    cy: list[float] = []
    faces: list[list[list[float]]] = []
    brightness: list[float] = []

    prev: np.ndarray | None = None
    ys, xs = np.mgrid[0:h, 0:w]
    idx = 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=frame_bytes * 8)
    assert proc.stdout is not None
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(h, w)
            t = idx / sample_fps
            times.append(round(t, 3))
            brightness.append(round(float(frame.mean()) / 255.0, 3))
            if prev is None:
                motion.append(0.0)
                cx.append(0.5)
                cy.append(0.5)
            else:
                diff = cv2.absdiff(frame, prev).astype(np.float32)
                motion.append(round(float(diff.mean()) / 255.0, 4))
                mask = diff > 25
                total = float(mask.sum())
                if total > 50:
                    cx.append(round(float(xs[mask].mean()) / w, 3))
                    cy.append(round(float(ys[mask].mean()) / h, 3))
                else:
                    cx.append(0.5)
                    cy.append(0.5)
            prev = frame
            detected = cascade.detectMultiScale(frame, scaleFactor=1.15, minNeighbors=5, minSize=(18, 18)) \
                if cascade is not None else []
            boxes = []
            for (fx, fy, fw, fh) in (detected if len(detected) else []):
                boxes.append([round(fx / w, 3), round(fy / h, 3), round(fw / w, 3), round(fh / h, 3)])
            faces.append(boxes)
            idx += 1
            if progress and duration and idx % 20 == 0:
                progress(min(0.99, t / duration))
    finally:
        proc.stdout.close()
        stderr = proc.stderr.read().decode(errors="ignore") if proc.stderr else ""
        proc.wait()
    if proc.returncode not in (0, None) and not times:
        raise ProcessingFailed(f"Frame sampling failed: {stderr[-300:]}")

    return {
        "sample_fps": sample_fps,
        "frame_size": [w, h],
        "times": times,
        "motion": motion,
        "centroid_x": cx,
        "centroid_y": cy,
        "faces": faces,
        "brightness": brightness,
        "facecam": detect_facecam(faces),
        "face_detection": cascade is not None,
    }


def detect_facecam(faces: list[list[list[float]]], min_presence: float = 0.45) -> dict:
    """Detect a stable small face region typical of a gameplay facecam overlay.

    Returns {detected: bool, box: [x,y,w,h] (normalised, with margin), presence: float, corner: str}.
    """
    n = len(faces)
    if n == 0:
        return {"detected": False}
    pts = []
    for boxes in faces:
        for b in boxes:
            if b[2] * b[3] <= 0.12:  # small face (overlay), not a full-screen talking head
                pts.append(b)
    if len(pts) < max(8, int(min_presence * n)):
        return {"detected": False, "presence": round(len(pts) / n, 3)}
    arr = np.array(pts, dtype=np.float32)
    centers = arr[:, :2] + arr[:, 2:] / 2
    med = np.median(centers, axis=0)
    close = np.linalg.norm(centers - med, axis=1) < 0.08
    stable = arr[close]
    presence = len(stable) / n
    if presence < min_presence:
        return {"detected": False, "presence": round(presence, 3)}
    x, y, w, h = np.median(stable, axis=0).tolist()
    ccx, ccy = x + w / 2, y + h / 2
    in_corner = (ccx < 0.38 or ccx > 0.62) and (ccy < 0.42 or ccy > 0.58)
    if not in_corner:
        return {"detected": False, "presence": round(presence, 3), "reason": "face not in a corner region"}
    # expand the face box to a plausible webcam frame (head + shoulders) and clamp
    bw, bh = min(w * 3.2, 0.5), min(h * 3.0, 0.5)
    bx = min(max(ccx - bw / 2, 0.0), 1.0 - bw)
    by = min(max(ccy - bh / 2 + h * 0.4, 0.0), 1.0 - bh)
    corner = ("top" if ccy < 0.5 else "bottom") + "-" + ("left" if ccx < 0.5 else "right")
    return {"detected": True, "box": [round(bx, 3), round(by, 3), round(bw, 3), round(bh, 3)],
            "face": [round(x, 3), round(y, 3), round(w, 3), round(h, 3)], "presence": round(presence, 3),
            "corner": corner}
