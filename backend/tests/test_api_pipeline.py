"""End-to-end: local sample video -> analysis job -> clips -> edits -> export -> zip.

Also exercises the YouTube adapter policy without network by stubbing metadata retrieval.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.services.media.base import AccessDecision, AcquiredMedia, VideoMetadata
from app.services.media.youtube_adapter import YouTubeAdapter


@pytest.fixture(scope="module")
def project(client, sample_video):
    r = client.post("/api/videos/inspect", json={"url": f"file://{sample_video}"})
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["access"]["allowed"] and info["duration_seconds"] == pytest.approx(75.0, abs=0.5)

    r = client.post("/api/videos/analyze", json={"youtubeUrl": f"file://{sample_video}", "clipDuration": "under_30",
                                                 "clipStyle": "gaming", "orientation": "9:16",
                                                 "captionStyle": "word_pop"})
    assert r.status_code == 202, r.text
    body = r.json()
    job = client.get(f"/api/jobs/{body['job_id']}").json()
    assert job["status"] == "completed", job
    stages = [h["stage"] for h in job["stage_history"]]
    for st in ("checking", "retrieving", "preparing", "transcribing", "scenes", "highlights", "clips", "rendering",
               "complete"):
        assert st in stages
    assert job["progress"] == 100
    assert job["metrics"]["clips_rendered"] >= 1
    return client.get(f"/api/projects/{body['project_id']}").json()


def test_analysis_results_are_real(project, sample_video):
    assert project["status"] == "completed"
    summary = project["analysis_summary"]
    # the synthetic video has hard cuts at 25 s and 50 s
    assert summary["scene_count"] == 3
    assert any(abs(c - 25.0) < 0.5 for c in summary["cuts"]) and any(abs(c - 50.0) < 0.5 for c in summary["cuts"])
    assert summary["audio_peaks"] >= 1
    assert summary["sampled_frames"] >= 70
    clips = project["clips"]
    assert 1 <= len(clips) <= 12
    for c in clips:
        assert 0 <= c["start_time"] < c["end_time"] <= 75.0
        assert 5 <= c["duration"] <= 35
        assert c["preview_status"] == "ready" and c["preview_url"]
        assert c["title"] and c["reason"]
    # the loud burst (30-40 s) must be covered by the top clip
    top = clips[0]
    assert top["start_time"] <= 40 and top["end_time"] >= 30
    # preview file exists and is a vertical mp4
    from app.services.ffmpeg import probe
    from app.services.storage import get_storage

    key = top["preview_url"].split("/api/media/")[1]
    info = probe(get_storage().local_path(key))
    assert info.height > info.width and info.has_audio
    assert abs(info.duration - top["duration"]) < 0.6


def test_project_listing_and_media_range(client, project):
    projects = client.get("/api/projects").json()
    assert any(p["id"] == project["id"] for p in projects)
    key = project["clips"][0]["preview_url"].split("/api/media/")[1]
    r = client.get(f"/api/media/{key}", headers={"Range": "bytes=0-15"})
    assert r.status_code == 206 and len(r.content) == 16
    assert client.get("/api/media/../etc/passwd").status_code in (400, 404)


def test_edit_render_export_download_zip(client, project):
    clip = project["clips"][0]
    cid = clip["id"]
    words = []
    t = clip["start_time"] + 0.5
    for w in "these words are burned into the export with karaoke timing".split():
        words.append({"start": round(t, 2), "end": round(t + 0.35, 2), "text": w})
        t += 0.42
    r = client.patch(f"/api/clips/{cid}", json={
        "title": "Edited title", "start_time": clip["start_time"], "end_time": clip["start_time"] + 8.0,
        "caption_words": words, "caption_preset": "karaoke",
        "settings": {"orientation": "9:16", "reframe": "auto", "captions": {"font_size": 70, "position": "bottom"},
                     "audio": {"normalize": True, "source_volume": 1.2}}})
    assert r.status_code == 200, r.text
    edited = r.json()
    assert edited["title"] == "Edited title" and edited["duration"] == pytest.approx(8.0)
    assert edited["settings"]["captions"]["animation"] == "karaoke"
    assert edited["settings"]["captions"]["font_size"] == 70
    assert edited["settings"]["captions"]["uppercase"] is False

    assert client.get(f"/api/clips/{cid}/download").status_code == 409  # not exported yet

    r = client.post(f"/api/clips/{cid}/render", json={"purpose": "export", "export": {"resolution": "720p", "fps": 60}})
    assert r.status_code == 202
    job = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "completed", job
    res = job["result"]
    assert (res["width"], res["height"]) == (720, 1280)
    assert res["fps"] == 30  # capped at the 30 fps source: no fabricated 60 fps
    assert res["captions_burned"] is True and res["duration"] == pytest.approx(8.0, abs=0.3)
    assert res["native_crop"] == {"width": 404, "height": 720} and res["upscaled"] is True

    r = client.get(f"/api/clips/{cid}/download", follow_redirects=False)
    assert r.status_code == 302 and "/api/media/" in r.headers["location"]

    # second export with identical settings is served from cache (no re-render)
    r = client.post(f"/api/clips/{cid}/render", json={"purpose": "export", "export": {"resolution": "720p", "fps": 60}})
    job2 = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job2["status"] == "completed" and job2["metrics"]["stage_seconds"]["rendering"] < 1.5

    # invalid settings rejected
    assert client.patch(f"/api/clips/{cid}", json={"settings": {"captions": {"color": "red"}}}).status_code == 422
    assert client.patch(f"/api/clips/{cid}", json={"end_time": clip["start_time"] + 0.5}).status_code == 400

    # fit + 16:9 + bounce preview re-render
    client.patch(f"/api/clips/{cid}", json={"settings": {"orientation": "16:9", "reframe": "fit"},
                                            "caption_preset": "bounce"})
    r = client.post(f"/api/clips/{cid}/render", json={"purpose": "preview"})
    job = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "completed", job
    assert job["result"]["width"] > job["result"]["height"]

    # zip of everything, with a different export profile -> renders then packages
    ids = [c["id"] for c in project["clips"]]
    r = client.post(f"/api/projects/{project['id']}/download-all", json={"clipIds": ids,
                                                                          "export": {"resolution": "720p", "fps": 24}})
    assert r.status_code == 202
    job = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "completed", job
    assert job["result"]["count"] == len(ids)
    key = job["result"]["key"]
    r = client.get(f"/api/media/{key}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    import io
    import zipfile

    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert len(names) == len(ids) and names[0].startswith("01 - ")


def test_youtube_link_is_denied_without_authorization(client, monkeypatch):
    monkeypatch.setattr(YouTubeAdapter, "fetch_metadata",
                        lambda self, req: VideoMetadata(title="Demo video", author="Channel", duration_seconds=600,
                                                        thumbnail_url="https://i.ytimg.com/vi/x/hq.jpg"))
    r = client.post("/api/videos/inspect", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert r.status_code == 200
    info = r.json()
    assert info["video_id"] == "dQw4w9WgXcQ" and info["title"] == "Demo video"
    assert info["access"]["allowed"] is False and "not enabled" in info["access"]["reason"]
    r = client.post("/api/videos/analyze", json={"youtubeUrl": "https://youtu.be/dQw4w9WgXcQ"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "media_access_denied"


def test_youtube_authorized_path_runs_pipeline(client, monkeypatch, sample_video):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "youtube_media_retrieval", "authorized")
    monkeypatch.setattr(YouTubeAdapter, "fetch_metadata",
                        lambda self, req: VideoMetadata(title="Owned video", author="Me", duration_seconds=75,
                                                        channel_id="UCowner"))

    def fake_acquire(self, req, dest_dir, progress):
        dest = Path(dest_dir) / "source.mp4"
        shutil.copyfile(sample_video, dest)
        progress(1.0, "done")
        return AcquiredMedia(path=dest, adapter="youtube", details={"format_id": "test"})

    monkeypatch.setattr(YouTubeAdapter, "acquire", fake_acquire)
    r = client.post("/api/videos/analyze", json={"youtubeUrl": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                                                 "clipDuration": "auto", "clipStyle": "best"})
    assert r.status_code == 202, r.text
    job = client.get(f"/api/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "completed", job
    p = client.get(f"/api/projects/{r.json()['project_id']}").json()
    assert p["source_kind"] == "youtube" and p["video_id"] == "dQw4w9WgXcQ" and p["clips"]

    # channel allow-list
    monkeypatch.setattr(settings, "youtube_allowed_channel_ids", "UCsomeoneelse")
    r = client.post("/api/videos/inspect", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    assert r.json()["access"]["allowed"] is False


def test_invalid_and_internal_sources_rejected(client):
    assert client.post("/api/videos/inspect", json={"url": "https://www.youtube.com/watch?v=nope"}).status_code == 400
    r = client.post("/api/videos/inspect", json={"url": "http://127.0.0.1:8000/secret.mp4"})
    assert r.status_code == 400
    assert client.post("/api/videos/inspect", json={"url": "file:///etc/passwd"}).status_code == 400


def test_upload_flow(client, sample_video):
    with sample_video.open("rb") as fh:
        r = client.post("/api/videos/upload", files={"file": ("my clip.mp4", fh, "video/mp4")})
    assert r.status_code == 200, r.text
    assert r.json()["url"].startswith("upload://") and r.json()["duration_seconds"] == pytest.approx(75, abs=0.5)
    r = client.post("/api/videos/inspect", json={"url": r.json()["url"]})
    assert r.status_code == 200 and r.json()["source_kind"] == "upload"
    r = client.post("/api/videos/upload", files={"file": ("evil.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400


def test_options_endpoint(client):
    o = client.get("/api/options").json()
    assert "word_pop" in o["caption_presets"] and o["fonts"] and o["ingest"]["youtube_media_retrieval"] is False
