"""Audio energy analysis on the 16 kHz mono analysis track."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

HOP_SECONDS = 0.5


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        n = wf.getnframes()
        raw = wf.readframes(n)
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return data, sr


def analyze_audio(wav_path: Path) -> dict:
    data, sr = _read_wav(wav_path)
    hop = int(sr * HOP_SECONDS)
    if len(data) < hop:
        return {"hop": HOP_SECONDS, "rms_db": [], "energy": [], "peaks": [], "silences": [], "onsets": []}
    n = len(data) // hop
    frames = data[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)

    # normalised energy 0..1 by rank (robust to overall loudness)
    ranks = db.argsort().argsort().astype(np.float32)
    energy = ranks / max(len(ranks) - 1, 1)

    # peaks: local maxima above mean + 1.25 std, at least 2 s apart
    mean, std = float(db.mean()), float(db.std() + 1e-6)
    z = (db - mean) / std
    peaks: list[dict] = []
    min_gap = int(2.0 / HOP_SECONDS)
    last = -min_gap
    for i in range(1, n - 1):
        if z[i] > 1.25 and db[i] >= db[i - 1] and db[i] >= db[i + 1] and i - last >= min_gap:
            peaks.append({"time": round(i * HOP_SECONDS, 2), "z": round(float(z[i]), 2), "db": round(float(db[i]), 1)})
            last = i

    # onsets: sudden loudness jumps (>= 6 dB over previous hop)
    jumps = np.diff(db, prepend=db[0])
    onsets = [round(i * HOP_SECONDS, 2) for i in np.where(jumps >= 6.0)[0].tolist()]

    # silences: runs below a floor relative to the median for >= 0.5 s
    floor = min(-45.0, float(np.median(db)) - 20.0)
    silences: list[dict] = []
    start = None
    for i in range(n):
        quiet = db[i] < floor
        if quiet and start is None:
            start = i
        elif not quiet and start is not None:
            if (i - start) * HOP_SECONDS >= 0.5:
                silences.append({"start": round(start * HOP_SECONDS, 2), "end": round(i * HOP_SECONDS, 2)})
            start = None
    if start is not None and (n - start) * HOP_SECONDS >= 0.5:
        silences.append({"start": round(start * HOP_SECONDS, 2), "end": round(n * HOP_SECONDS, 2)})

    return {
        "hop": HOP_SECONDS,
        "rms_db": [round(float(v), 1) for v in db],
        "energy": [round(float(v), 3) for v in energy],
        "peaks": peaks,
        "onsets": onsets,
        "silences": silences,
        "mean_db": round(mean, 1),
        "std_db": round(std, 1),
    }
