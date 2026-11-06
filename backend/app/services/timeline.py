"""Output timeline for a clip: which source ranges are kept (silence removal) and time mapping."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Timeline:
    start: float  # clip start in source time
    segments: list[tuple[float, float]]  # kept source ranges, ascending, within [start, end]

    @property
    def duration(self) -> float:
        return sum(b - a for a, b in self.segments)

    @property
    def is_trivial(self) -> bool:
        return len(self.segments) == 1

    def map(self, t: float) -> float:
        """Source time -> output time (times inside removed gaps collapse to the gap edge)."""
        out = 0.0
        for a, b in self.segments:
            if t < a:
                return out
            if t <= b:
                return out + (t - a)
            out += b - a
        return out

    def relative_segments(self) -> list[tuple[float, float]]:
        return [(a - self.start, b - self.start) for a, b in self.segments]


def build_timeline(start: float, end: float, words: list[dict] | None, *, remove_silence: bool,
                   max_gap: float = 1.0, keep_pad: float = 0.22, min_total_cut: float = 0.5) -> Timeline:
    if not remove_silence or not words:
        return Timeline(start, [(start, end)])
    inside = sorted((w for w in words if w["end"] > start and w["start"] < end), key=lambda w: w["start"])
    if len(inside) < 2:
        return Timeline(start, [(start, end)])
    cuts: list[tuple[float, float]] = []
    for prev, nxt in zip(inside, inside[1:]):
        gap = nxt["start"] - prev["end"]
        if gap > max_gap:
            a, b = prev["end"] + keep_pad, nxt["start"] - keep_pad
            if b - a >= 0.25:
                cuts.append((a, b))
    if not cuts or sum(b - a for a, b in cuts) < min_total_cut:
        return Timeline(start, [(start, end)])
    segments: list[tuple[float, float]] = []
    cursor = start
    for a, b in cuts:
        if a > cursor:
            segments.append((cursor, a))
        cursor = b
    if end > cursor:
        segments.append((cursor, end))
    return Timeline(start, segments)
