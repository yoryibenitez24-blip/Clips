from app.services.highlights import (
    build_units,
    select_highlights,
    snap_bounds,
    target_clip_count,
)


def _transcript(duration=300.0):
    """Monotone narration with an obviously exciting stretch between 120 and 160 s."""
    segments = []
    t = 0.0
    i = 0
    while t < duration - 4:
        if 120 <= t < 160:
            text = "Oh my god no way, that was insane! Did you see that? Let's go!"
        else:
            text = f"This is sentence number {i} about the general topic of the video."
        words = []
        toks = text.split()
        span = 4.0 / len(toks)
        for k, tok in enumerate(toks):
            words.append({"start": round(t + k * span, 3), "end": round(t + (k + 1) * span - 0.05, 3), "text": tok,
                          "prob": 0.9})
        segments.append({"start": t, "end": t + 4.0, "text": text, "words": words})
        t += 4.5
        i += 1
    return {"language": "en", "segments": segments}


def _audio(duration=300.0):
    hop = 0.5
    n = int(duration / hop)
    energy = [0.3] * n
    for i in range(int(125 / hop), int(150 / hop)):
        energy[i] = 0.95
    peaks = [{"time": 128.0, "z": 2.5}, {"time": 134.0, "z": 2.1}, {"time": 145.0, "z": 2.8}]
    return {"hop": hop, "energy": energy, "peaks": peaks, "onsets": [128.0, 145.0], "silences": []}


def test_units_merge_fragments():
    tr = {"segments": [{"start": 0, "end": 2, "text": "Hello there", "words": []},
                       {"start": 2, "end": 2.5, "text": "and", "words": []},
                       {"start": 2.5, "end": 5, "text": "welcome back.", "words": []}]}
    units = build_units(tr)
    assert len(units) == 2
    assert units[0].text == "Hello there and"


def test_highlights_prefer_exciting_region_and_do_not_overlap():
    res = select_highlights(duration=300.0, transcript=_transcript(), scenes={"cuts": [60.0, 200.0]}, audio=_audio(),
                            vision=None, style="best", clip_duration="30_60", max_clips=5, language="en")
    picks = res["candidates"]
    assert 1 <= len(picks) <= 5
    best = picks[0]
    assert best.start < 160 and best.end > 120, (best.start, best.end)
    assert 30 * 0.6 <= best.duration <= 60 * 1.1
    assert best.title and best.reason
    assert "insane" in best.text.lower()
    for a in picks:
        for b in picks:
            if a is not b:
                assert a.end <= b.start or b.end <= a.start
    # sentence-aligned boundaries
    unit_starts = {u.start for u in res["units"]}
    unit_ends = {u.end for u in res["units"]}
    assert best.start in unit_starts and best.end in unit_ends


def test_highlights_without_transcript_use_audio_and_scene_signals():
    vision = {"times": [float(t) for t in range(300)], "motion": [0.02] * 300, "centroid_x": [0.5] * 300,
              "centroid_y": [0.5] * 300, "faces": [[] for _ in range(300)], "brightness": [0.5] * 300}
    for t in range(125, 150):
        vision["motion"][t] = 0.2
    res = select_highlights(duration=300.0, transcript=None, scenes={"cuts": [60.0, 200.0]}, audio=_audio(),
                            vision=vision, style="gaming", clip_duration="under_30", max_clips=6, language=None)
    picks = res["candidates"]
    assert picks
    best = picks[0]
    assert best.start >= 110 and best.end <= 165, (best.start, best.end)
    assert best.title.startswith("Gameplay highlight")


def test_snap_bounds_aligns_to_words():
    words = [{"start": 10.0, "end": 10.4, "text": "a"}, {"start": 10.5, "end": 10.9, "text": "b"},
             {"start": 20.0, "end": 20.5, "text": "c"}, {"start": 21.0, "end": 21.6, "text": "d"}]
    # boundaries that fall inside words "a" and "d" keep both words (plus breathing room)
    start, end = snap_bounds(10.2, 21.3, words, None, None, 100.0, 5.0, 30.0)
    assert start == 9.75 and end == 22.05
    # a boundary in a gap starts just before the next word, after the previous word ends
    start, end = snap_bounds(10.46, 20.7, words, None, None, 100.0, 5.0, 30.0)
    assert 10.4 <= start <= 10.5 and 20.5 <= end <= 20.98
    # never beyond the media duration
    assert snap_bounds(0.0, 30.0, words, None, None, 21.8, 5.0, 30.0)[1] <= 21.8


def test_target_clip_count_scales_with_duration():
    assert target_clip_count(60, 10, 40) == 1
    assert target_clip_count(1800, 10, 40) == 10
    assert 3 <= target_clip_count(600, 10, 40) <= 10
