"""ASS subtitle generation with animated caption presets (rendered by libass inside FFmpeg)."""
from __future__ import annotations

import re

from app.schemas import CaptionSettings
from app.services.timeline import Timeline


def ass_color(hex_color: str, alpha: int = 0) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", " ")


def chunk_words(words: list[dict], max_words: int, max_gap: float = 0.7) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            gap = w["start"] - cur[-1]["end"]
            if len(cur) >= max_words or gap > max_gap or re.search(r"[.!?…]$", cur[-1]["text"]):
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def map_words_to_output(words: list[dict], timeline: Timeline, start: float, end: float) -> list[dict]:
    out = []
    for w in words:
        if w["end"] <= start or w["start"] >= end:
            continue
        # drop words that fall entirely inside a removed gap
        inside = any(a <= w["start"] <= b or a <= w["end"] <= b for a, b in timeline.segments)
        if not inside:
            continue
        ws, we = timeline.map(max(w["start"], start)), timeline.map(min(w["end"], end))
        if we - ws < 0.04:
            we = ws + 0.04
        out.append({"start": round(ws, 3), "end": round(we, 3), "text": str(w["text"]).strip()})
    return [w for w in out if w["text"]]


def build_ass(words: list[dict], cfg: CaptionSettings, width: int, height: int, *,
              facecam_top_h: int | None = None) -> str:
    """`words` must already be on the OUTPUT timeline (see map_words_to_output)."""
    scale = min(width, height) / 1080.0
    font_size = max(12, int(round(cfg.font_size * scale)))
    outline = round(cfg.outline_width * scale, 2)
    shadow = round(cfg.shadow * scale, 2)
    karaoke = cfg.animation in ("karaoke", "bounce")
    primary = ass_color(cfg.highlight_color if karaoke else cfg.color)
    secondary = ass_color(cfg.color)
    margin_lr = int(width * 0.06)
    if cfg.position == "top":
        align, margin_v = 8, int(height * 0.10)
    elif cfg.position == "middle":
        if facecam_top_h:
            align, margin_v = 8, int(facecam_top_h + height * 0.015)
        else:
            align, margin_v = 2, int(height * 0.40)
    else:
        align, margin_v = 2, int(height * 0.12)
    if facecam_top_h and cfg.position == "top":
        align, margin_v = 8, int(facecam_top_h + height * 0.015)

    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        "Style: Cap,{font},{size},{primary},{secondary},{outline_c},{back_c},{bold},0,0,0,100,100,0,0,1,"
        "{outline},{shadow},{align},{ml},{mr},{mv},1\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    ).format(w=width, h=height, font=cfg.font_family, size=font_size, primary=primary, secondary=secondary,
             outline_c=ass_color(cfg.outline_color), back_c=ass_color(cfg.outline_color, 0x60),
             bold=-1 if cfg.bold else 0, outline=outline, shadow=shadow, align=align, ml=margin_lr,
             mr=margin_lr, mv=margin_v)

    def fmt(word: str) -> str:
        return _esc(word.upper() if cfg.uppercase else word)

    hl = ass_color(cfg.highlight_color)
    base = ass_color(cfg.color)
    events: list[str] = []
    chunks = chunk_words(words, cfg.max_words)
    for ci, chunk in enumerate(chunks):
        c_start = chunk[0]["start"]
        c_end = max(chunk[-1]["end"], c_start + 0.4)
        if ci + 1 < len(chunks):
            c_end = min(c_end, chunks[ci + 1][0]["start"] - 0.02)
        if c_end <= c_start:
            continue
        line = f"Dialogue: 0,{{start}},{{end}},Cap,,0,0,0,,{{text}}"
        if cfg.animation == "minimal":
            events.append(line.format(start=_ts(c_start), end=_ts(c_end), text=" ".join(fmt(w["text"]) for w in chunk)))
        elif cfg.animation == "fade":
            events.append(line.format(start=_ts(c_start), end=_ts(c_end),
                                      text="{\\fad(150,120)}" + " ".join(fmt(w["text"]) for w in chunk)))
        elif cfg.animation == "scale":
            events.append(line.format(start=_ts(c_start), end=_ts(c_end),
                                      text="{\\fscx82\\fscy82\\t(0,160,\\fscx100\\fscy100)}"
                                      + " ".join(fmt(w["text"]) for w in chunk)))
        elif cfg.animation in ("karaoke", "bounce"):
            parts = []
            for i, w in enumerate(chunk):
                nxt = chunk[i + 1]["start"] if i + 1 < len(chunk) else c_end
                dur_cs = max(1, int(round((nxt - w["start"]) * 100)))
                parts.append(f"{{\\k{dur_cs}}}{fmt(w['text'])}")
            prefix = ("{\\fscx72\\fscy72\\t(0,110,\\fscx112\\fscy112)\\t(110,230,\\fscx100\\fscy100)}"
                      if cfg.animation == "bounce" else "")
            events.append(line.format(start=_ts(c_start), end=_ts(c_end), text=prefix + " ".join(parts)))
        else:  # word_pop, gaming_bold: one event per spoken word, positions stay stable
            for i, w in enumerate(chunk):
                w_start = w["start"] if i > 0 else c_start
                w_end = chunk[i + 1]["start"] if i + 1 < len(chunk) else c_end
                if w_end <= w_start:
                    continue
                pieces = []
                for j, other in enumerate(chunk):
                    if j < i:
                        pieces.append(f"{{\\1c{base}\\alpha&H00&}}{fmt(other['text'])}")
                    elif j == i:
                        pop = "\\fscx122\\fscy122\\t(0,90,\\fscx100\\fscy100)" if cfg.animation == "gaming_bold" \
                            else "\\fscx115\\fscy115\\t(0,80,\\fscx100\\fscy100)"
                        pieces.append(f"{{\\1c{hl}\\alpha&H00&{pop}}}{fmt(other['text'])}{{\\fscx100\\fscy100}}")
                    else:
                        future_alpha = "&H00&" if cfg.animation == "gaming_bold" else "&HFF&"
                        pieces.append(f"{{\\1c{base}\\alpha{future_alpha}}}{fmt(other['text'])}")
                events.append(line.format(start=_ts(w_start), end=_ts(w_end), text=" ".join(pieces)))
    return header + "\n".join(events) + "\n"


def redistribute_text(words: list[dict], new_text: str) -> list[dict]:
    """Replace word texts with `new_text`, spreading timing proportionally if the word count changed."""
    tokens = new_text.split()
    if not words or not tokens:
        return []
    if len(tokens) == len(words):
        return [{**w, "text": t} for w, t in zip(words, tokens)]
    span_start, span_end = words[0]["start"], words[-1]["end"]
    total_chars = sum(len(t) for t in tokens) + max(len(tokens) - 1, 0)
    cursor = span_start
    out = []
    for t in tokens:
        frac = (len(t) + 1) / max(total_chars, 1)
        w_end = min(span_end, cursor + frac * (span_end - span_start))
        out.append({"start": round(cursor, 3), "end": round(max(w_end, cursor + 0.05), 3), "text": t})
        cursor = w_end
    out[-1]["end"] = round(span_end, 3)
    return out
