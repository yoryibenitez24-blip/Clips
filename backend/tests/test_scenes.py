import shutil
import subprocess

import pytest

from app.services.scenes import detect_scenes


def test_scene_detection_opencv_backend(sample_video):
    res = detect_scenes(sample_video, 75.0, 30.0)
    assert res["backend"] == "opencv" and res["frames_read"] > 0
    assert len(res["cuts"]) == 2


def test_scene_detection_falls_back_to_pyav_for_av1(tmp_path):
    encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    if "libsvtav1" not in encoders and "libaom-av1" not in encoders:
        pytest.skip("no AV1 encoder available")
    enc = "libsvtav1" if "libsvtav1" in encoders else "libaom-av1"
    seg = []
    for i, src in enumerate(["testsrc2=size=640x360:rate=30", "rgbtestsrc=size=640x360:rate=30"]):
        out = tmp_path / f"s{i}.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src, "-t", "4",
                        "-c:v", enc, "-pix_fmt", "yuv420p", str(out)], check=True)
        seg.append(out)
    lst = tmp_path / "l.txt"
    lst.write_text("".join(f"file '{s}'\n" for s in seg))
    av1 = tmp_path / "av1.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(av1)], check=True)
    res = detect_scenes(av1, 8.0, 30.0, codec="av1")
    assert res["backend"] == "pyav" and res["frames_read"] > 0
    assert len(res["cuts"]) == 1 and abs(res["cuts"][0] - 4.0) < 0.3
    # opencv-first order must also end up on a working decoder
    res2 = detect_scenes(av1, 8.0, 30.0, codec=None)
    assert res2["frames_read"] > 0 and len(res2["cuts"]) == 1
    assert shutil.which("ffmpeg")
