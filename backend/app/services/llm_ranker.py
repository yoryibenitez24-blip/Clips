"""Optional LLM-assisted highlight selection using the Anthropic SDK.

Runs only when ANTHROPIC_API_KEY is configured. Claude receives the heuristic shortlist with
transcript text and signal summaries, and returns an ordered pick list with titles and reasons.
Every returned timestamp is validated against the candidate it refers to; nothing is invented.
"""
from __future__ import annotations

import json
import logging

from app.config import get_settings
from app.services.highlights import Candidate

log = logging.getLogger(__name__)

_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "reason": {"type": "string"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                },
                "required": ["candidate_id", "title", "reason", "start", "end"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["picks"],
    "additionalProperties": False,
}

_STYLE_BRIEF = {
    "gaming": "gameplay highlights: real action, clutch plays, big reactions",
    "funny": "funny moments: jokes, laughter, absurd exchanges",
    "best": "the most compelling, self-contained moments of the video",
    "reactions": "strong emotional reactions and surprises",
    "cinematic": "visually striking, atmospheric moments (dialogue optional)",
    "talking": "insightful, quotable, self-contained talking points",
}


def _fmt(c: Candidate, idx: int) -> dict:
    sig = {k: round(v, 2) for k, v in c.signals.items() if k in
           ("peaks", "motion", "excite", "laugh", "speech", "novelty", "hook", "cuts", "faces")}
    units = [{"start": round(u[0], 2), "end": round(u[1], 2), "text": u[2]} for u in c.units] if c.units else []
    return {"id": idx, "start": round(c.start, 2), "end": round(c.end, 2), "duration": round(c.duration, 1),
            "heuristic_score": round(c.score, 3), "signals": sig, "text": c.text[:1500], "sentences": units[:40]}


def refine_with_llm(shortlist: list[Candidate], *, style: str, max_clips: int, language: str,
                    min_len: float, max_len: float, video_title: str | None) -> tuple[list[Candidate], list[str]]:
    """Return (ordered picks, warnings). Falls back to the input order on any failure."""
    s = get_settings()
    warnings: list[str] = []
    if not s.anthropic_api_key or not s.llm_highlight_selection or not shortlist:
        return [], ["llm_skipped"]
    if not any(c.text for c in shortlist):
        return [], ["llm_skipped_no_transcript"]
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return [], ["anthropic sdk not installed"]

    client = anthropic.Anthropic(api_key=s.anthropic_api_key)
    payload = {"video_title": video_title, "style": _STYLE_BRIEF.get(style, _STYLE_BRIEF["best"]),
               "language": language, "max_clips": max_clips, "min_len": min_len, "max_len": max_len,
               "candidates": [_fmt(c, i) for i, c in enumerate(shortlist[:30])]}
    prompt = (
        "You help pick short-form clips from a long video. Below are candidate segments (timestamps in "
        "seconds, transcript text, and measured signals from audio/vision analysis).\n\n"
        f"Choose up to {max_clips} candidates that best match the style, ordered best first. Prefer moments that "
        "are coherent and self-contained, start cleanly, avoid near-duplicates of each other, and don't "
        "overlap. You may tighten or extend a pick ONLY to timestamps that appear in that candidate's "
        "'sentences' list (use a sentence 'start' for start and a sentence 'end' for end) while keeping the "
        f"duration between {min_len:.0f} and {max_len:.0f} seconds. Write a short, specific title (max 60 "
        "characters, no clickbait, no emojis) and a one-sentence reason grounded in the transcript. Write the "
        f"title and reason in the transcript language ({language}).\n\n"
        f"Candidates JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        resp = client.beta.messages.create(
            model=s.llm_model,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}, "effort": "medium"},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.RateLimitError as exc:
        return [], [f"llm rate limited: {exc}"]
    except anthropic.APIStatusError as exc:
        return [], [f"llm api error: {exc.status_code}"]
    except anthropic.APIConnectionError as exc:
        return [], [f"llm connection error: {exc}"]
    if resp.stop_reason == "refusal":
        return [], ["llm refused"]
    text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [], ["llm returned invalid json"]

    picks: list[Candidate] = []
    for rank, p in enumerate(data.get("picks", [])):
        cid = p.get("candidate_id")
        if not isinstance(cid, int) or cid < 0 or cid >= len(shortlist):
            continue
        c = shortlist[cid]
        start, end = float(p.get("start", c.start)), float(p.get("end", c.end))
        allowed_starts = {round(u[0], 2) for u in c.units} | {round(c.start, 2)}
        allowed_ends = {round(u[1], 2) for u in c.units} | {round(c.end, 2)}
        if round(start, 2) not in allowed_starts or round(end, 2) not in allowed_ends or end <= start:
            start, end = c.start, c.end
        if not (min_len * 0.8 <= end - start <= max_len * 1.15):
            start, end = c.start, c.end
        if any(not (end <= q.start or start >= q.end) for q in picks):
            continue
        new = Candidate(start=start, end=end, score=c.score, text=c.text, signals=c.signals,
                        title=(p.get("title") or c.title)[:80].strip(), reason=(p.get("reason") or c.reason)[:240].strip(),
                        units=[u for u in c.units if u[0] >= start - 0.01 and u[1] <= end + 0.01] or c.units,
                        llm_rank=rank)
        picks.append(new)
    if not picks:
        return [], ["llm returned no valid picks"]
    return picks, warnings
