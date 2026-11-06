"""Highlight detection.

Combines transcript (speech, excitement cues, topic novelty), scene cuts, audio energy peaks
and sampled-frame motion into per-second feature tracks, generates sentence-aligned candidate
windows, scores them with style-specific weights, suppresses overlapping / repetitive picks and
snaps the final boundaries to word timings so clips don't cut mid-sentence.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field

import numpy as np

from app.schemas import DURATION_RANGES

# ----------------------------------------------------------------------------- lexicons
_EXCITE = {
    "en": ["wow", "omg", "oh my god", "no way", "insane", "crazy", "let's go", "lets go", "unbelievable",
           "holy", "what the", "are you kidding", "dude", "bro", "yes!", "nice", "clutch", "ace", "headshot",
           "finally", "whoa", "wait what", "amazing", "incredible", "ridiculous", "what?!", "come on"],
    "es": ["no manches", "no mames", "increíble", "increible", "dios mío", "dios mio", "vamos", "qué locura",
           "que locura", "no puede ser", "wow", "madre mía", "madre mia", "brutal", "qué", "ostia", "hostia",
           "joder", "por fin", "venga", "espera", "qué pasó", "que paso", "ay", "uy"],
}
_LAUGH = ["haha", "hahaha", "lol", "lmao", "jaja", "jajaja", "jeje", "[laughter]", "(laughs)", "(laughter)",
          "[laughs]", "ha ha", "risas"]
_HOOK = {
    "en": ["secret", "never", "why", "how", "best", "worst", "actually", "truth", "mistake", "nobody",
           "everyone", "the problem", "here's", "listen", "the reason", "you need", "stop", "biggest", "first time"],
    "es": ["secreto", "nunca", "por qué", "cómo", "mejor", "peor", "la verdad", "error", "nadie", "todos",
           "el problema", "escucha", "la razón", "necesitas", "primera vez", "lo más"],
}
_STOP = set("the a an and or but of to in on at for with is are was were be it this that i you he she we they "
            "my your so just like um uh yeah okay ok el la los las de del y o pero que es en un una por para con "
            "se lo le su mi tu no sí si ya muy más como".split())

STYLE_WEIGHTS: dict[str, dict[str, float]] = {
    "gaming":    {"motion": 0.22, "action": 0.14, "peaks": 0.22, "onsets": 0.08, "excite": 0.14, "laugh": 0.05,
                  "cuts": 0.05, "speech": 0.03, "novelty": 0.02, "hook": 0.02, "faces": 0.03},
    "funny":     {"laugh": 0.32, "excite": 0.18, "peaks": 0.15, "speech": 0.10, "motion": 0.06, "novelty": 0.07,
                  "hook": 0.07, "faces": 0.05},
    "best":      {"peaks": 0.16, "excite": 0.14, "laugh": 0.10, "speech": 0.12, "motion": 0.10, "novelty": 0.12,
                  "hook": 0.12, "cuts": 0.04, "wps": 0.05, "faces": 0.05},
    "reactions": {"peaks": 0.24, "excite": 0.22, "laugh": 0.12, "faces": 0.14, "speech": 0.10, "motion": 0.08,
                  "onsets": 0.10},
    "cinematic": {"motion": 0.20, "cuts": 0.22, "dynamics": 0.26, "quiet_speech": 0.12, "novelty": 0.08,
                  "brightness_var": 0.12},
    "talking":   {"speech": 0.22, "wps": 0.10, "novelty": 0.20, "hook": 0.20, "excite": 0.10, "peaks": 0.08,
                  "faces": 0.10},
}

FEATURE_LABELS = {
    "motion": "sustained on-screen motion", "action": "high-action footage", "peaks": "loud audio spikes",
    "onsets": "sudden sound bursts", "excite": "excited reactions in speech", "laugh": "laughter cues",
    "cuts": "rapid scene changes", "speech": "continuous dialogue", "novelty": "a new topic begins",
    "hook": "a strong opening line", "faces": "people on camera", "wps": "fast-paced speaking",
    "dynamics": "wide audio dynamics", "quiet_speech": "little dialogue (visual moment)",
    "brightness_var": "visual variety",
}


@dataclass
class Unit:
    start: float
    end: float
    text: str


@dataclass
class Candidate:
    start: float
    end: float
    score: float
    text: str
    signals: dict = field(default_factory=dict)
    title: str = ""
    reason: str = ""
    units: list = field(default_factory=list)  # [(start,end,text)] for boundary adjustments
    llm_rank: int | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration"] = round(self.duration, 3)
        return d


# ----------------------------------------------------------------------------- helpers
def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[\wáéíóúñü']+", text.lower()) if t not in _STOP and len(t) > 2]


def _lexicon_hits(text: str, lexicon: list[str]) -> int:
    low = " " + text.lower() + " "
    return sum(low.count(" " + term + " ") + (1 if term in low and " " in term else 0) for term in lexicon)


def _cosine_novelty(before: list[str], window: list[str]) -> float:
    if not before or not window:
        return 0.5
    a, b = {}, {}
    for t in before:
        a[t] = a.get(t, 0) + 1
    for t in window:
        b[t] = b.get(t, 0) + 1
    num = sum(a[t] * b.get(t, 0) for t in a)
    den = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    if den == 0:
        return 0.5
    return float(min(1.0, max(0.0, 1.0 - num / den)))


def build_units(transcript: dict | None) -> list[Unit]:
    """Whisper segments as sentence-ish units; merge fragments shorter than 1.2 s."""
    units: list[Unit] = []
    for seg in (transcript or {}).get("segments", []):
        text = seg.get("text", "").strip()
        if not text:
            continue
        u = Unit(float(seg["start"]), float(seg["end"]), text)
        if units and (u.end - u.start) < 1.2 and not re.search(r"[.!?…]$", units[-1].text):
            units[-1] = Unit(units[-1].start, u.end, units[-1].text + " " + u.text)
        else:
            units.append(u)
    return units


# ----------------------------------------------------------------------------- features
class FeatureTracks:
    def __init__(self, duration: float, transcript: dict | None, scenes: dict | None, audio: dict | None,
                 vision: dict | None, language: str):
        self.n = max(int(math.ceil(duration)), 1)
        self.duration = duration
        n = self.n
        self.energy = np.zeros(n)
        self.peak = np.zeros(n)
        self.onset = np.zeros(n)
        self.motion = np.zeros(n)
        self.faces = np.zeros(n)
        self.brightness = np.zeros(n)
        self.speech = np.zeros(n)
        self.words = np.zeros(n)
        self.excite = np.zeros(n)
        self.laugh = np.zeros(n)
        self.silence = np.zeros(n)
        self.cut = np.zeros(n)
        lang = "es" if (language or "").startswith("es") else "en"
        self.lang = lang

        if audio and audio.get("energy"):
            hop = float(audio.get("hop", 0.5))
            e = np.array(audio["energy"], dtype=float)
            for i in range(n):
                a, b = int(i / hop), int((i + 1) / hop)
                seg = e[a:b]
                self.energy[i] = seg.mean() if len(seg) else 0.0
            for p in audio.get("peaks", []):
                i = int(p["time"])
                if 0 <= i < n:
                    self.peak[i] = max(self.peak[i], float(p.get("z", 1.0)))
            for t in audio.get("onsets", []):
                i = int(t)
                if 0 <= i < n:
                    self.onset[i] = 1.0
            for sl in audio.get("silences", []):
                for i in range(int(sl["start"]), min(int(math.ceil(sl["end"])), n)):
                    self.silence[i] = 1.0

        if vision and vision.get("times"):
            times = np.array(vision["times"], dtype=float)
            mot = np.array(vision["motion"], dtype=float)
            br = np.array(vision.get("brightness", [0] * len(times)), dtype=float)
            face_counts = np.array([len(f) for f in vision.get("faces", [])] or [0] * len(times), dtype=float)
            idx = np.clip(times.astype(int), 0, n - 1)
            counts = np.bincount(idx, minlength=n).astype(float)
            counts[counts == 0] = 1
            self.motion = np.bincount(idx, weights=mot, minlength=n) / counts
            self.brightness = np.bincount(idx, weights=br, minlength=n) / counts
            self.faces = np.minimum(np.bincount(idx, weights=face_counts, minlength=n) / counts, 1.0)
            self.motion_p75 = float(np.percentile(mot, 75)) if len(mot) else 0.0
            self.motion_p90 = float(np.percentile(mot, 90)) or 1e-6 if len(mot) else 1e-6
        else:
            self.motion_p75 = 0.0
            self.motion_p90 = 1e-6

        for t in (scenes or {}).get("cuts", []):
            i = int(t)
            if 0 <= i < n:
                self.cut[i] = 1.0

        self.has_transcript = bool(transcript and transcript.get("segments"))
        self.unit_tokens: list[list[str]] = []
        for seg in (transcript or {}).get("segments", []):
            words = seg.get("words") or []
            text = seg.get("text", "")
            a, b = int(seg["start"]), min(int(math.ceil(seg["end"])), n)
            if b <= a:
                b = min(a + 1, n)
            self.speech[a:b] = 1.0
            wcount = len(words) or len(text.split())
            if b > a:
                self.words[a:b] += wcount / (b - a)
            ex = _lexicon_hits(text, _EXCITE[lang]) + text.count("!") * 0.5
            la = _lexicon_hits(text, _LAUGH)
            if ex and b > a:
                self.excite[a:b] += ex / (b - a)
            if la and b > a:
                self.laugh[a:b] += la / (b - a)

    def window(self, start: float, end: float, text: str, before_text: str) -> dict[str, float]:
        a, b = max(int(start), 0), min(int(math.ceil(end)), self.n)
        if b <= a:
            b = min(a + 1, self.n)
        length = max(end - start, 1.0)
        sl = slice(a, b)
        f: dict[str, float] = {}
        f["energy"] = float(self.energy[sl].mean())
        f["peaks"] = min(1.0, float(self.peak[sl].sum()) / (length / 30.0) / 4.0)
        f["onsets"] = min(1.0, float(self.onset[sl].sum()) / (length / 30.0) / 4.0)
        f["motion"] = min(1.0, float(self.motion[sl].mean()) / self.motion_p90) if self.motion_p90 > 0 else 0.0
        f["action"] = float((self.motion[sl] > self.motion_p75).mean()) if self.motion_p75 > 0 else 0.0
        f["faces"] = float(self.faces[sl].mean())
        f["speech"] = float(self.speech[sl].mean())
        f["wps"] = min(1.0, float(self.words[sl].mean()) / 3.0)
        f["excite"] = min(1.0, float(self.excite[sl].sum()) / (length / 15.0))
        f["laugh"] = min(1.0, float(self.laugh[sl].sum()) / (length / 15.0))
        f["cuts"] = min(1.0, float(self.cut[sl].sum()) / (length / 30.0) / 4.0)
        f["silence"] = float(self.silence[sl].mean())
        f["dynamics"] = float(self.energy[sl].max() - self.energy[sl].mean()) if b > a else 0.0
        f["quiet_speech"] = 1.0 - f["speech"]
        f["brightness_var"] = min(1.0, float(self.brightness[sl].std()) * 4.0)
        f["novelty"] = _cosine_novelty(_tokens(before_text), _tokens(text)) if self.has_transcript else 0.0
        first_sentence = text.split(".")[0][:160].lower() if text else ""
        hook = 0.0
        if first_sentence:
            hook += 0.5 if "?" in first_sentence else 0.0
            hook += min(0.5, 0.25 * _lexicon_hits(first_sentence, _HOOK[self.lang]))
            hook += 0.2 if re.search(r"\d", first_sentence) else 0.0
        f["hook"] = min(1.0, hook)
        return f


# ----------------------------------------------------------------------------- generation
def generate_candidates(tracks: FeatureTracks, units: list[Unit], scenes: dict | None, min_len: float,
                        max_len: float, duration: float) -> list[Candidate]:
    cands: list[Candidate] = []
    if units:
        for i, u in enumerate(units):
            before = " ".join(x.text for x in units[max(0, i - 12):i])
            acc: list[Unit] = []
            for j in range(i, len(units)):
                if units[j].end - u.start > max_len * 1.05:
                    break
                acc.append(units[j])
                length = units[j].end - u.start
                if length >= min_len:
                    text = " ".join(x.text for x in acc)
                    cands.append(Candidate(start=u.start, end=units[j].end, score=0.0, text=text,
                                           signals=tracks.window(u.start, units[j].end, text, before),
                                           units=[(x.start, x.end, x.text) for x in acc]))
            # also allow a slightly short window if speech ends (short video / sparse speech)
            if acc and (acc[-1].end - u.start) < min_len and (acc[-1].end - u.start) >= min_len * 0.6:
                text = " ".join(x.text for x in acc)
                cands.append(Candidate(start=u.start, end=acc[-1].end, score=0.0, text=text,
                                       signals=tracks.window(u.start, acc[-1].end, text, before),
                                       units=[(x.start, x.end, x.text) for x in acc]))
    if not cands:
        # no usable speech: windows anchored on scene cuts and a coarse grid
        anchors = sorted({0.0, *[float(c) for c in (scenes or {}).get("cuts", [])],
                          *[float(t) for t in np.arange(0, max(duration - min_len, 0) + 1e-6, 3.0)]})
        lengths = sorted({min_len, (min_len + max_len) / 2, max_len})
        for a in anchors:
            for L in lengths:
                b = a + L
                if b > duration + 0.5:
                    continue
                b = min(b, duration)
                if b - a < min_len * 0.6:
                    continue
                cands.append(Candidate(start=a, end=b, score=0.0, text="", signals=tracks.window(a, b, "", "")))
    return cands


def score_candidates(cands: list[Candidate], style: str, target: float, min_len: float, max_len: float,
                     tracks: FeatureTracks) -> None:
    weights = STYLE_WEIGHTS.get(style, STYLE_WEIGHTS["best"])
    sigma = max((max_len - min_len) / 2.0, 5.0)
    for c in cands:
        f = c.signals
        base = sum(w * f.get(k, 0.0) for k, w in weights.items())
        # coherence & shape bonuses
        bonus = 0.0
        if c.units:
            last_text = c.units[-1][2].strip()
            if re.search(r"[.!?…\"”]$", last_text):
                bonus += 0.04
            first_text = c.units[0][2].strip()
            if first_text and first_text[0].isupper():
                bonus += 0.01
        # cuts right at the edges make a clip feel chopped
        for edge in (c.start, c.end):
            i = int(edge)
            if 0 <= i < tracks.n and tracks.cut[max(i - 1, 0): i + 2].any() and tracks.has_transcript:
                bonus -= 0.03
        penalty = 0.25 * f.get("silence", 0.0)
        if not tracks.has_transcript and style != "cinematic":
            # without dialogue to anchor on, a hard cut in the middle of a window usually means two moments
            a, b = int(c.start) + 1, max(int(c.end) - 1, int(c.start) + 1)
            penalty += min(0.12, 0.04 * float(tracks.cut[a:b].sum()))
        length_pref = math.exp(-((c.duration - target) ** 2) / (2 * sigma**2))
        c.score = max(0.0, (base + bonus - penalty) * (0.8 + 0.2 * length_pref))


def _overlap(a: Candidate, b: Candidate) -> float:
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    return inter / max(min(a.duration, b.duration), 1e-6)


def _text_similarity(a: str, b: str) -> float:
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def select_diverse(cands: list[Candidate], max_clips: int, overlap_threshold: float = 0.15) -> list[Candidate]:
    picked: list[Candidate] = []
    for c in sorted(cands, key=lambda x: x.score, reverse=True):
        if len(picked) >= max_clips:
            break
        if any(_overlap(c, p) > overlap_threshold for p in picked):
            continue
        if c.text and any(_text_similarity(c.text, p.text) > 0.6 for p in picked):
            continue
        if any(abs(c.start - p.end) < 2.0 or abs(p.start - c.end) < 2.0 for p in picked):
            continue
        picked.append(c)
    return picked


def target_clip_count(duration: float, max_clips: int, target_len: float) -> int:
    by_length = int(duration // max(target_len * 1.6, 30))
    return int(max(1, min(max_clips, by_length if by_length >= 1 else 1)))


# ----------------------------------------------------------------------------- boundaries
def snap_bounds(start: float, end: float, words: list[dict], scenes: dict | None, audio: dict | None,
                duration: float, min_len: float, max_len: float) -> tuple[float, float]:
    """Move boundaries to natural points: word edges with breathing room, else cuts / silences."""
    if words:
        starts = [w["start"] for w in words]
        ends = [w["end"] for w in words]
        # first word still being spoken at (or after) the start, last word that began before the end
        i = int(np.searchsorted(ends, start - 0.05, side="right"))
        if i < len(words):
            prev_end = ends[i - 1] if i > 0 else 0.0
            new_start = max(words[i]["start"] - 0.25, prev_end + 0.02, 0.0)
        else:
            new_start = start
        j = int(np.searchsorted(starts, end + 0.05, side="left")) - 1
        if j >= 0:
            next_start = starts[j + 1] if j + 1 < len(words) else duration
            new_end = min(words[j]["end"] + 0.45, next_start - 0.02, duration)
        else:
            new_end = end
        if new_end - new_start >= min_len * 0.6:
            start, end = new_start, new_end
    else:
        cuts = [float(c) for c in (scenes or {}).get("cuts", [])]
        for cut in cuts:
            if abs(cut - start) <= 1.0:
                start = cut
            if abs(cut - end) <= 1.0:
                end = cut
        for sl in (audio or {}).get("silences", []):
            if sl["start"] <= start <= sl["end"]:
                start = sl["end"] - 0.1
            if sl["start"] <= end <= sl["end"]:
                end = sl["start"] + 0.1
    start = max(0.0, start)
    end = min(duration, end)
    if end - start > max_len * 1.15:
        end = start + max_len * 1.15
    return round(start, 3), round(end, 3)


# ----------------------------------------------------------------------------- titles
def heuristic_title(c: Candidate, style: str, lang: str) -> str:
    text = c.text.strip()
    if text:
        first = re.split(r"(?<=[.!?])\s+", text)[0]
        first = re.sub(r"\s+", " ", first).strip(" ,;:-")
        if len(first) > 64:
            cut = first[:64].rsplit(" ", 1)[0]
            first = cut + "…"
        if first:
            return first[0].upper() + first[1:]
    labels = {"gaming": ("Gameplay highlight", "Momento de juego"), "funny": ("Funny moment", "Momento gracioso"),
              "best": ("Best moment", "Mejor momento"), "reactions": ("Big reaction", "Gran reacción"),
              "cinematic": ("Cinematic moment", "Momento cinemático"), "talking": ("Key moment", "Momento clave")}
    label = labels.get(style, labels["best"])[1 if lang == "es" else 0]
    m, s = divmod(int(c.start), 60)
    return f"{label} · {m}:{s:02d}"


def heuristic_reason(c: Candidate, style: str) -> str:
    weights = STYLE_WEIGHTS.get(style, STYLE_WEIGHTS["best"])
    contributions = sorted(((w * c.signals.get(k, 0.0), k) for k, w in weights.items()), reverse=True)
    top = [FEATURE_LABELS[k] for v, k in contributions[:3] if v > 0.01]
    if not top:
        return "Selected as the strongest available segment for this style."
    return "Selected for " + ", ".join(top) + "."


# ----------------------------------------------------------------------------- entry point
def select_highlights(*, duration: float, transcript: dict | None, scenes: dict | None, audio: dict | None,
                      vision: dict | None, style: str, clip_duration: str, max_clips: int,
                      language: str | None) -> dict:
    min_len, max_len, target = DURATION_RANGES.get(clip_duration, DURATION_RANGES["auto"])
    if duration < min_len * 1.2:
        min_len, max_len = max(5.0, duration * 0.3), max(duration, 6.0)
        target = min(target, duration * 0.6)
    lang = (language or (transcript or {}).get("language") or "en")[:2]
    tracks = FeatureTracks(duration, transcript, scenes, audio, vision, lang)
    units = build_units(transcript)
    cands = generate_candidates(tracks, units, scenes, min_len, max_len, duration)
    score_candidates(cands, style, target, min_len, max_len, tracks)
    n_target = target_clip_count(duration, max_clips, target)
    shortlist = sorted(cands, key=lambda c: c.score, reverse=True)[:60]
    picked = select_diverse(shortlist, n_target)
    for c in picked:
        c.title = heuristic_title(c, style, lang)
        c.reason = heuristic_reason(c, style)
    picked.sort(key=lambda c: c.score, reverse=True)
    return {
        "candidates": picked,
        "shortlist": shortlist,
        "units": units,
        "tracks": tracks,
        "bounds": (min_len, max_len, target),
        "language": lang,
        "candidate_count": len(cands),
    }
