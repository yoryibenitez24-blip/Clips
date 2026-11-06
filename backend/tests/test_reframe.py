import numpy as np

from app.services.reframe import build_crop_path, crop_size, facecam_regions, output_dims, path_expr, preview_dims
from app.services.timeline import Timeline
from app.services.vision import detect_facecam


def test_dims():
    assert output_dims("9:16", "1080p") == (1080, 1920)
    assert output_dims("16:9", "720p") == (1280, 720)
    assert output_dims("1:1", "1080p") == (1080, 1080)
    assert preview_dims("9:16")[1] == 960
    assert crop_size(1920, 1080, 9 / 16) == (608, 1080)
    assert crop_size(1080, 1920, 16 / 9) == (1080, 608)


def test_static_fill_path_and_offset():
    tl = Timeline(0.0, [(0.0, 10.0)])
    path = build_crop_path(vision=None, start=0, end=10, src_w=1920, src_h=1080, crop_w=608, crop_h=1080,
                           timeline=tl, mode="fill")
    assert path == [(0.0, 656, 0)]
    path = build_crop_path(vision=None, start=0, end=10, src_w=1920, src_h=1080, crop_w=608, crop_h=1080,
                           timeline=tl, mode="fill", offset_x=1.0)
    assert path[0][1] == 1312
    assert path_expr(path, 1) == "1312"


def test_auto_path_follows_subject_smoothly():
    n = 30
    vision = {"times": [float(t) for t in range(n)], "motion": [0.05] * n, "centroid_x": [0.5] * n,
              "centroid_y": [0.5] * n, "brightness": [0.5] * n,
              "faces": [[[0.1 + 0.025 * t, 0.3, 0.08, 0.14]] for t in range(n)]}  # face moves left -> right
    tl = Timeline(0.0, [(0.0, float(n))])
    path = build_crop_path(vision=vision, start=0, end=n, src_w=1920, src_h=1080, crop_w=608, crop_h=1080,
                           timeline=tl, mode="auto")
    xs = np.array([p[1] for p in path])
    assert xs[-1] > xs[0]
    assert np.all(np.diff(xs) >= -1)  # monotone-ish, no jitter
    assert np.max(np.abs(np.diff(xs))) <= 608 * 0.5 + 1  # speed limited
    expr = path_expr(path, 1)
    assert "gte(t," in expr and "lt(t," in expr
    assert path_expr(path, 2) == "0"


def test_facecam_detection_and_layout():
    frames = [[[0.82, 0.72, 0.07, 0.11]] if i % 10 else [] for i in range(100)]  # steady small face, bottom-right
    fc = detect_facecam(frames)
    assert fc["detected"] and fc["corner"] == "bottom-right"
    assert detect_facecam([[[0.4, 0.3, 0.3, 0.45]] for _ in range(50)])["detected"] is False  # big centred face
    assert detect_facecam([[] for _ in range(50)])["detected"] is False
    reg = facecam_regions(fc, 1920, 1080, 1080, 1920)
    assert reg["top_h"] + reg["bot_h"] == 1920
    cam_w, cam_h, cam_x, cam_y = reg["cam"]
    assert 0 <= cam_x <= 1920 - cam_w and 0 <= cam_y <= 1080 - cam_h
    assert abs(cam_w / cam_h - 1080 / reg["top_h"]) < 0.05
