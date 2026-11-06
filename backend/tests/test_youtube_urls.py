import pytest

from app.services.errors import InvalidURL
from app.services.netguard import validate_public_http_url
from app.services.youtube import canonical_url, extract_video_id, is_youtube_url, parse_iso8601_duration


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=PL123",
    "https://m.youtube.com/watch?feature=share&v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ?si=abc",
    "https://www.youtube.com/live/dQw4w9WgXcQ?feature=share",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    "https://www.youtube.com/embed/dQw4w9WgXcQ",
    "youtube.com/watch?v=dQw4w9WgXcQ",
    "  https://www.youtube.com/watch?v=dQw4w9WgXcQ  ",
])
def test_extract_video_id(url):
    assert extract_video_id(url) == "dQw4w9WgXcQ"
    assert canonical_url("dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.parametrize("url", [
    "", "not a url", "https://vimeo.com/12345", "https://www.youtube.com/", "https://www.youtube.com/watch?v=short",
    "https://www.youtube.com/channel/UC123", "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
    "https://evil.com/youtube.com/watch?v=dQw4w9WgXcQ",
])
def test_invalid_youtube_urls(url):
    with pytest.raises(InvalidURL):
        extract_video_id(url)


def test_is_youtube_url():
    assert is_youtube_url("https://youtu.be/dQw4w9WgXcQ")
    assert not is_youtube_url("https://example.com/video.mp4")


def test_iso_duration():
    assert parse_iso8601_duration("PT1H2M3S") == 3723
    assert parse_iso8601_duration("PT45S") == 45
    assert parse_iso8601_duration("P1DT1H") == 90000


@pytest.mark.parametrize("url", [
    "http://localhost/video.mp4", "http://127.0.0.1/video.mp4", "http://10.0.0.5/v.mp4", "http://192.168.1.1/v.mp4",
    "http://169.254.169.254/latest/meta-data", "http://[::1]/v.mp4", "http://metadata.internal/v.mp4",
    "https://example.com:8443/v.mp4", "https://user:pw@example.com/v.mp4", "file:///etc/passwd",
    "http://0.0.0.0/v.mp4", "http://[::ffff:127.0.0.1]/v.mp4",
])
def test_netguard_rejects_internal(url):
    with pytest.raises(InvalidURL):
        validate_public_http_url(url, resolve=False)


def test_netguard_accepts_public():
    assert validate_public_http_url("https://cdn.example.com/path/video.mp4", resolve=False)
    assert validate_public_http_url("http://93.184.216.34/video.mp4", resolve=False)
