"""Test configuration: SQLite, eager Celery, local storage, local-file ingestion, tiny whisper model."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(tempfile.mkdtemp(prefix="clips-tests-"))
os.environ.update({
    "ENVIRONMENT": "test",
    "DATABASE_URL": f"sqlite:///{_ROOT}/test.db",
    "CELERY_TASK_ALWAYS_EAGER": "true",
    "LOCAL_FILE_INGEST_ENABLED": "true",
    "LOCAL_MEDIA_DIR": str(_ROOT / "media"),
    "WORK_DIR": str(_ROOT / "work"),
    "LOCAL_STORAGE_DIR": str(_ROOT / "storage"),
    "UPLOAD_DIR": str(_ROOT / "uploads"),
    "WHISPER_CACHE_DIR": str(_ROOT / "models"),
    "WHISPER_MODEL": "tiny",
    "LOG_LEVEL": "WARNING",
    "RATE_LIMIT_ANALYZE_PER_HOUR": "1000",
    "MIN_FREE_DISK_BYTES": "1",
    "ANTHROPIC_API_KEY": "",
})
(_ROOT / "media").mkdir(parents=True, exist_ok=True)


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def sample_video() -> Path:
    """75 s, 1280x720, three visually distinct scenes, a tone with loud bursts at 30-40 s and 55-58 s."""
    media = _ROOT / "media"
    out = media / "sample.mp4"
    if out.exists():
        return out
    segs = []
    for i, src in enumerate([
        "testsrc2=size=1280x720:rate=30",
        "color=c=0x202020:size=1280x720:rate=30",
        "rgbtestsrc=size=1280x720:rate=30",
    ]):
        seg = media / f"seg{i}.mp4"
        vf = ["-vf", "drawbox=x='mod(t*220,1180)':y='300+100*sin(t*2)':w=120:h=120:color=white:t=fill"] if i == 1 else []
        _ffmpeg("-f", "lavfi", "-i", src, "-t", "25", *vf, "-pix_fmt", "yuv420p", "-c:v", "libx264",
                "-preset", "veryfast", str(seg))
        segs.append(seg)
    lst = media / "list.txt"
    lst.write_text("".join(f"file '{s}'\n" for s in segs))
    _ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst), "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "75", "-filter_complex",
            "[1:a]volume='if(between(t,30,40),1.0,if(between(t,55,58),0.9,0.04))':eval=frame,"
            "aformat=channel_layouts=stereo[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
            str(out))
    for s in segs:
        s.unlink()
    lst.unlink()
    return out


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
