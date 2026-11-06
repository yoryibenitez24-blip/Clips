"""Output geometry and auto-reframe crop paths."""
from __future__ import annotations

import math

import numpy as np

from app.services.timeline import Timeline

_ASPECT = {"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1.0}


def output_dims(orientation: str, resolution: str) -> tuple[int, int]:
    short = 720 if resolution == "720p" else 1080
    if orientation == "9:16":
        return short, int(short * 16 / 9)
    if orientation == "16:9":
        return int(short * 16 / 9), short
    return short, short


def preview_dims(orientation: str, preview_height: int = 960) -> tuple[int, int]:
    if orientation == "9:16":
        return int(preview_height * 9 / 16) // 2 * 2, preview_height
    if orientation == "16:9":
        return preview_height, int(preview_height * 9 / 16) // 2 * 2
    side = int(preview_height * 0.75) // 2 * 2
    return side, side


def _even(v: float) -> int:
    return max(2, int(round(v / 2.0)) * 2)


def crop_size(src_w: int, src_h: int, aspect: float) -> tuple[int, int]:
    """Largest crop of the given aspect that fits inside the source frame."""
    if src_w / src_h > aspect:
        ch = src_h // 2 * 2
        cw = _even(ch * aspect)
    else:
        cw = src_w // 2 * 2
        ch = _even(cw / aspect)
    return min(cw, src_w // 2 * 2), min(ch, src_h // 2 * 2)


def _smooth_path(values: np.ndarray, times: np.ndarray, free_range: float, dead_zone: float,
                 max_speed: float) -> np.ndarray:
    """Dead-zone + speed-limited follower, then light Gaussian smoothing; values in pixels."""
    if len(values) == 0:
        return values
    out = np.empty_like(values)
    cur = float(values[0])
    out[0] = cur
    for i in range(1, len(values)):
        dt = max(float(times[i] - times[i - 1]), 1e-3)
        target = float(values[i])
        if abs(target - cur) > dead_zone:
            step = np.clip(target - cur, -max_speed * dt, max_speed * dt)
            cur += step
        out[i] = cur
    if len(out) >= 5:
        kernel = np.array([0.1, 0.2, 0.4, 0.2, 0.1])
        padded = np.pad(out, 2, mode="edge")
        out = np.convolve(padded, kernel, mode="valid")
    return np.clip(out, 0.0, max(free_range, 0.0))


def subject_centers(vision: dict | None, start: float, end: float, exclude_box: list[float] | None = None
                    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-sample (time, cx, cy) in normalised source coords for the clip range."""
    if not vision or not vision.get("times"):
        return np.array([start, end]), np.array([0.5, 0.5]), np.array([0.5, 0.5])
    times = np.array(vision["times"], dtype=float)
    mask = (times >= start - 1.0) & (times <= end + 1.0)
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return np.array([start, end]), np.array([0.5, 0.5]), np.array([0.5, 0.5])
    cxs, cys = [], []
    last = (0.5, 0.5)
    for i in idx:
        faces = vision["faces"][i] if i < len(vision["faces"]) else []
        if exclude_box:
            ex, ey, ew, eh = exclude_box
            faces = [f for f in faces if not (ex <= f[0] + f[2] / 2 <= ex + ew and ey <= f[1] + f[3] / 2 <= ey + eh)]
        if faces:
            f = max(faces, key=lambda b: b[2] * b[3])
            last = (f[0] + f[2] / 2, f[1] + f[3] * 0.45)
        elif vision["motion"][i] > 0.01:
            last = (vision["centroid_x"][i], vision["centroid_y"][i])
        cxs.append(last[0])
        cys.append(last[1])
    return times[idx], np.array(cxs), np.array(cys)


def build_crop_path(*, vision: dict | None, start: float, end: float, src_w: int, src_h: int, crop_w: int,
                    crop_h: int, timeline: Timeline, offset_x: float = 0.0, offset_y: float = 0.0,
                    mode: str = "auto", exclude_box: list[float] | None = None) -> list[tuple[float, int, int]]:
    """Return [(t_out, x, y)] crop origins. Static for 'fill', subject-following for 'auto'."""
    free_x, free_y = max(src_w - crop_w, 0), max(src_h - crop_h, 0)
    base_x, base_y = free_x / 2 + offset_x * free_x / 2, free_y / 2 + offset_y * free_y / 2
    if mode != "auto" or (free_x == 0 and free_y == 0):
        return [(0.0, int(round(np.clip(base_x, 0, free_x))), int(round(np.clip(base_y, 0, free_y))))]
    times, cx, cy = subject_centers(vision, start, end, exclude_box)
    xs = np.clip(cx * src_w - crop_w / 2 + offset_x * free_x / 2, 0, free_x)
    ys = np.clip(cy * src_h - crop_h / 2 + offset_y * free_y / 2, 0, free_y)
    xs = _smooth_path(xs, times, free_x, dead_zone=crop_w * 0.08, max_speed=crop_w * 0.5)
    ys = _smooth_path(ys, times, free_y, dead_zone=crop_h * 0.08, max_speed=crop_h * 0.5) if free_y else ys
    path: list[tuple[float, int, int]] = []
    for t, x, y in zip(times, xs, ys):
        if t < start - 1.0 or t > end + 1.0:
            continue
        t_out = timeline.map(min(max(t, start), end))
        path.append((round(float(t_out), 3), int(round(x)), int(round(y))))
    # collapse duplicates on the output timeline (gaps removed by silence cutting map to one instant)
    dedup: list[tuple[float, int, int]] = []
    for p in path:
        if dedup and abs(dedup[-1][0] - p[0]) < 1e-6:
            dedup[-1] = p
        else:
            dedup.append(p)
    return dedup or [(0.0, int(base_x), int(base_y))]


def path_expr(path: list[tuple[float, int, int]], axis: int) -> str:
    """Piecewise-linear ffmpeg expression over `t` for the x (axis=1) or y (axis=2) crop origin."""
    vals = [p[axis] for p in path]
    if len(path) == 1 or all(v == vals[0] for v in vals):
        return str(vals[0])
    # collapse runs of identical values to keep the expression compact
    pts: list[tuple[float, int]] = []
    for t, v in zip((p[0] for p in path), vals):
        if len(pts) >= 2 and pts[-1][1] == v and pts[-2][1] == v:
            pts[-1] = (t, v)
        else:
            pts.append((t, v))
    terms = [f"{pts[0][1]}*lt(t,{pts[0][0]:.3f})"]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t1 - t0 <= 1e-6:
            continue
        slope = (v1 - v0) / (t1 - t0)
        terms.append(f"gte(t,{t0:.3f})*lt(t,{t1:.3f})*({v0}+({slope:.4f})*(t-{t0:.3f}))")
    terms.append(f"{pts[-1][1]}*gte(t,{pts[-1][0]:.3f})")
    return "+".join(terms)


def facecam_regions(facecam: dict, src_w: int, src_h: int, out_w: int, out_h: int,
                    top_fraction: float = 0.34) -> dict:
    """Crop boxes for a stacked facecam (top) + gameplay (bottom) vertical layout."""
    top_h = _even(out_h * top_fraction)
    bot_h = out_h - top_h
    bx, by, bw, bh = facecam["box"]
    # expand the facecam box to the top panel aspect
    aspect = out_w / top_h
    cam_w, cam_h = bw * src_w, bh * src_h
    if cam_w / cam_h < aspect:
        cam_w = cam_h * aspect
    else:
        cam_h = cam_w / aspect
    cam_w, cam_h = min(cam_w, src_w), min(cam_h, src_h)
    cam_x = min(max((bx + bw / 2) * src_w - cam_w / 2, 0), src_w - cam_w)
    cam_y = min(max((by + bh / 2) * src_h - cam_h / 2, 0), src_h - cam_h)
    game_w, game_h = crop_size(src_w, src_h, out_w / bot_h)
    return {"top_h": top_h, "bot_h": bot_h,
            "cam": (_even(cam_w) if cam_w >= 2 else 2, _even(cam_h), int(cam_x), int(cam_y)),
            "game": (game_w, game_h)}


def ceil_even(v: float) -> int:
    return int(math.ceil(v / 2.0)) * 2
