from app.schemas import CaptionSettings
from app.services.captions import ass_color, build_ass, chunk_words, map_words_to_output, redistribute_text
from app.services.timeline import Timeline, build_timeline

WORDS = [{"start": 0.0 + i * 0.5, "end": 0.4 + i * 0.5, "text": w} for i, w in
         enumerate("hello there this is a caption test okay.".split())]


def test_ass_color_is_bgr():
    assert ass_color("#FF8000") == "&H000080FF"


def test_chunking_respects_max_words_and_punctuation():
    chunks = chunk_words(WORDS, max_words=3)
    assert [len(c) for c in chunks] == [3, 3, 2]
    assert chunks[-1][-1]["text"].endswith(".")


def test_karaoke_uses_k_tags_and_highlight_primary():
    cfg = CaptionSettings.from_preset("karaoke")
    ass = build_ass(WORDS, cfg, 1080, 1920)
    assert "PlayResX: 1080" in ass
    assert "{\\k" in ass
    assert ass_color(cfg.highlight_color) in ass.split("[Events]")[0]  # primary colour = highlight


def test_word_pop_emits_one_event_per_word_and_stable_layout():
    cfg = CaptionSettings.from_preset("word_pop")
    ass = build_ass(WORDS, cfg, 1080, 1920)
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == len(WORDS)
    assert "\\alpha&HFF&" in ass  # future words invisible but occupying space
    assert "\\fscx115" in ass


def test_gaming_bold_uppercases_and_minimal_has_no_tags():
    ass = build_ass(WORDS, CaptionSettings.from_preset("gaming_bold"), 1080, 1920)
    assert "HELLO" in ass
    ass_min = build_ass(WORDS, CaptionSettings.from_preset("minimal"), 1080, 1920)
    dialog = [line for line in ass_min.splitlines() if line.startswith("Dialogue:")][0]
    assert "{" not in dialog.split(",,", 1)[-1]


def test_position_and_facecam_offset():
    cfg = CaptionSettings.from_preset("fade")
    cfg.position = "top"
    top = build_ass(WORDS, cfg, 1080, 1920)
    assert ",8,64,64," in top  # alignment 8 (top centre)
    cfg.position = "middle"
    split = build_ass(WORDS, cfg, 1080, 1920, facecam_top_h=650)
    assert ",8,64,64,678," in split  # placed just under the facecam panel


def test_timeline_cuts_long_gaps_and_maps_words():
    words = [{"start": 10.0, "end": 10.5, "text": "a"}, {"start": 10.6, "end": 11.0, "text": "b"},
             {"start": 14.0, "end": 14.5, "text": "c"}]
    tl = build_timeline(10.0, 15.0, words, remove_silence=True)
    assert len(tl.segments) == 2
    assert tl.segments[0][1] < tl.segments[1][0]
    assert abs(tl.duration - (5.0 - (14.0 - 0.22 - (11.0 + 0.22)))) < 1e-6
    out = map_words_to_output(words, tl, 10.0, 15.0)
    assert out[2]["start"] < 2.0  # the 3 s gap collapsed
    assert tl.map(10.0) == 0.0 and abs(tl.map(15.0) - tl.duration) < 1e-6
    assert build_timeline(10.0, 15.0, words, remove_silence=False).is_trivial


def test_redistribute_text():
    out = redistribute_text(WORDS[:3], "hola que tal")
    assert [w["text"] for w in out] == ["hola", "que", "tal"]
    out2 = redistribute_text(WORDS[:3], "one two three four")
    assert len(out2) == 4 and out2[0]["start"] == WORDS[0]["start"] and out2[-1]["end"] == WORDS[2]["end"]
