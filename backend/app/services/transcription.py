"""Speech recognition with faster-whisper (word-level timestamps)."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services.errors import ProcessingFailed

log = logging.getLogger(__name__)

_model_lock = threading.Lock()
_model_cache: dict[str, Any] = {}


class TranscriptionUnavailable(ProcessingFailed):
    code = "transcription_unavailable"


def _load_model():
    s = get_settings()
    key = f"{s.whisper_model}:{s.whisper_device}:{s.whisper_compute_type}"
    with _model_lock:
        if key in _model_cache:
            return _model_cache[key]
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover
            raise TranscriptionUnavailable("faster-whisper is not installed") from exc
        kwargs = dict(device=s.whisper_device, compute_type=s.whisper_compute_type,
                      download_root=str(s.whisper_cache_dir))
        try:
            model = WhisperModel(s.whisper_model, local_files_only=True, **kwargs)
        except Exception:
            try:
                model = WhisperModel(s.whisper_model, **kwargs)
            except Exception as exc:
                raise TranscriptionUnavailable(
                    f"Whisper model '{s.whisper_model}' is not available locally and could not be downloaded: "
                    f"{str(exc)[:200]}"
                ) from exc
        _model_cache[key] = model
        return model


def transcribe(audio_path: Path, language: str | None = None, progress=None) -> dict:
    """Return {language, segments:[{start,end,text,words:[{start,end,text,prob}]}], text}."""
    s = get_settings()
    model = _load_model()
    lang = None if (language in (None, "auto")) else language
    try:
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=lang,
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False,
        )
    except Exception as exc:
        raise ProcessingFailed(f"Transcription failed: {str(exc)[:300]}") from exc

    total = float(getattr(info, "duration", 0.0) or 0.0)
    segments: list[dict] = []
    for seg in segments_iter:
        words = []
        for w in seg.words or []:
            text = (w.word or "").strip()
            if not text:
                continue
            words.append({"start": round(float(w.start), 3), "end": round(float(w.end), 3), "text": text,
                          "prob": round(float(w.probability), 3)})
        text = (seg.text or "").strip()
        if not text:
            continue
        segments.append({"start": round(float(seg.start), 3), "end": round(float(seg.end), 3), "text": text,
                         "words": words})
        if progress and total:
            progress(min(0.99, float(seg.end) / total))

    detected = getattr(info, "language", None) or lang or "unknown"
    warnings: list[str] = []
    if detected not in s.transcription_language_list and detected != "unknown":
        warnings.append(f"Detected language '{detected}' is outside the supported set "
                        f"({', '.join(s.transcription_language_list)}); captions may be less accurate.")
    return {
        "language": detected,
        "language_probability": round(float(getattr(info, "language_probability", 0.0) or 0.0), 3),
        "model": s.whisper_model,
        "segments": segments,
        "text": " ".join(seg["text"] for seg in segments),
        "warnings": warnings,
    }


def all_words(transcript: dict | None) -> list[dict]:
    if not transcript:
        return []
    out: list[dict] = []
    for seg in transcript.get("segments", []):
        if seg.get("words"):
            out.extend(seg["words"])
        else:  # fall back to evenly spread words when the model gave no word timing
            tokens = seg["text"].split()
            if not tokens:
                continue
            span = max(seg["end"] - seg["start"], 0.1) / len(tokens)
            for i, tok in enumerate(tokens):
                out.append({"start": seg["start"] + i * span, "end": seg["start"] + (i + 1) * span, "text": tok,
                            "prob": 0.0})
    return out
