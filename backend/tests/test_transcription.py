"""Transcription formatting with a stand-in model (the real Whisper weights need network access)."""
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from app.services import transcription
from app.services.ffmpeg import extract_analysis_audio


class FakeModel:
    def __init__(self):
        self.received = None

    def transcribe(self, audio, **kwargs):
        self.received = (audio, kwargs)
        words = [SimpleNamespace(start=1.0, end=1.4, word=" Hello", probability=0.9),
                 SimpleNamespace(start=1.5, end=1.9, word=" world.", probability=0.8)]
        segs = [SimpleNamespace(start=1.0, end=1.9, text=" Hello world.", words=words),
                SimpleNamespace(start=3.0, end=3.5, text="   ", words=[])]
        info = SimpleNamespace(language="fr", language_probability=0.7, duration=75.0)
        return iter(segs), info


def test_transcribe_passes_pcm_array_and_formats_output(sample_video, tmp_path, monkeypatch):
    wav = extract_analysis_audio(sample_video, tmp_path / "a.wav")
    fake = FakeModel()
    monkeypatch.setattr(transcription, "_load_model", lambda: fake)
    out = transcription.transcribe(Path(wav), "auto")
    audio, kwargs = fake.received
    assert isinstance(audio, np.ndarray) and audio.dtype == np.float32
    assert abs(len(audio) / 16000 - 75.0) < 0.5
    assert kwargs["word_timestamps"] is True and kwargs["language"] is None
    assert out["language"] == "fr" and len(out["segments"]) == 1
    assert out["segments"][0]["words"][1]["text"] == "world."
    assert out["text"] == "Hello world."
    assert out["warnings"] and "fr" in out["warnings"][0]  # outside en/es -> warning, not failure
