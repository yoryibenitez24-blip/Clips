"""Builds and runs the FFmpeg render for one clip (preview or export).

The render reads the source-quality working copy, applies the output timeline (silence cuts),
reframing (auto / fill / fit / facecam split), burned-in ASS captions, loudness normalisation
and optional licensed background music, and encodes H.264/AAC MP4.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.schemas import ClipSettings
from app.services import captions as cap
from app.services.errors import ProcessingFailed
from app.services.ffmpeg import probe
from app.services.reframe import (
    build_crop_path,
    crop_size,
    facecam_regions,
    output_dims,
    path_expr,
    preview_dims,
)
from app.services.timeline import Timeline, build_timeline

log = logging.getLogger(__name__)

_ASPECT = {"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1.0}


@dataclass
class RenderSpec:
    source_path: Path
    start: float
    end: float
    settings: ClipSettings
    caption_words: list[dict]
    vision: dict | None
    facecam: dict | None
    src_w: int
    src_h: int
    src_fps: float
    has_audio: bool
    purpose: str  # preview | export


def render_hash(spec_like: dict) -> str:
    return hashlib.sha256(json.dumps(spec_like, sort_keys=True, default=str).encode()).hexdigest()[:40]


def _select_expr(segments: list[tuple[float, float]]) -> str:
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in segments)


def _music_path(name: str | None) -> Path | None:
    s = get_settings()
    if not name or not s.music_library_dir:
        return None
    p = (Path(s.music_library_dir) / Path(name).name).resolve()
    if Path(s.music_library_dir).resolve() not in p.parents or not p.is_file():
        return None
    return p


def build_render(spec: RenderSpec, work_dir: Path, out_path: Path) -> dict:
    """Write the filter script + ASS file, run ffmpeg, return info about the produced file."""
    s = get_settings()
    st = spec.settings
    work_dir.mkdir(parents=True, exist_ok=True)
    if spec.purpose == "preview":
        out_w, out_h = preview_dims(st.orientation, s.preview_height)
        fps = min(30, int(round(spec.src_fps)) or 30)
        v_args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-tune", "fastdecode"]
        a_bitrate = "128k"
    else:
        out_w, out_h = output_dims(st.orientation, st.export.resolution)
        fps = min(int(st.export.fps), max(int(round(spec.src_fps)), 1))
        v_args = ["-c:v", "libx264", "-preset", st.export.preset, "-profile:v", "high", "-level", "4.2"]
        if st.export.bitrate_kbps:
            v_args += ["-b:v", f"{st.export.bitrate_kbps}k", "-maxrate", f"{int(st.export.bitrate_kbps * 1.3)}k",
                       "-bufsize", f"{st.export.bitrate_kbps * 2}k"]
        else:
            v_args += ["-crf", str(st.export.crf)]
        a_bitrate = "192k"

    timeline: Timeline = build_timeline(spec.start, spec.end, spec.caption_words, remove_silence=st.remove_silence)
    rel_segments = timeline.relative_segments()
    clip_len = spec.end - spec.start

    # ------------------------------------------------------------------ video chain
    chain: list[str] = [f"[0:v]fps={fps},setpts=PTS-STARTPTS"]
    if not timeline.is_trivial:
        chain.append(f"select='{_select_expr(rel_segments)}',setpts=N/({fps}*TB)")
    video_pre = ",".join(chain) + "[v0]"

    facecam_used = False
    native: tuple[int, int]
    use_facecam = (st.orientation == "9:16" and st.reframe == "auto" and st.facecam_layout
                   and bool(spec.facecam and spec.facecam.get("detected")))
    if use_facecam:
        reg = facecam_regions(spec.facecam, spec.src_w, spec.src_h, out_w, out_h)
        cam_w, cam_h, cam_x, cam_y = reg["cam"]
        game_w, game_h = reg["game"]
        path = build_crop_path(vision=spec.vision, start=spec.start, end=spec.end, src_w=spec.src_w,
                               src_h=spec.src_h, crop_w=game_w, crop_h=game_h, timeline=timeline,
                               offset_x=st.crop_offset_x, offset_y=st.crop_offset_y, mode="auto",
                               exclude_box=spec.facecam.get("box"))
        xe, ye = path_expr(path, 1), path_expr(path, 2)
        layout = (
            f"[v0]split=2[cam][game];"
            f"[cam]crop={cam_w}:{cam_h}:{cam_x}:{cam_y},scale={out_w}:{reg['top_h']}:flags=lanczos[camS];"
            f"[game]crop={game_w}:{game_h}:x='{xe}':y='{ye}',scale={out_w}:{reg['bot_h']}:flags=lanczos[gameS];"
            f"[camS][gameS]vstack=inputs=2[v1]"
        )
        facecam_used = True
        facecam_top_h = reg["top_h"]
        native = (game_w, game_h)
    elif st.reframe == "fit":
        layout = (
            f"[v0]split=2[bg][fg];"
            f"[bg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},"
            f"gblur=sigma=28,eq=brightness=-0.08:saturation=0.9[bgb];"
            f"[fg]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2[v1]"
        )
        facecam_top_h = None
        native = (spec.src_w, spec.src_h)
    else:
        cw, ch = crop_size(spec.src_w, spec.src_h, _ASPECT[st.orientation])
        path = build_crop_path(vision=spec.vision, start=spec.start, end=spec.end, src_w=spec.src_w,
                               src_h=spec.src_h, crop_w=cw, crop_h=ch, timeline=timeline,
                               offset_x=st.crop_offset_x, offset_y=st.crop_offset_y,
                               mode="auto" if st.reframe == "auto" else "fill")
        xe, ye = path_expr(path, 1), path_expr(path, 2)
        layout = f"[v0]crop={cw}:{ch}:x='{xe}':y='{ye}',scale={out_w}:{out_h}:flags=lanczos[v1]"
        facecam_top_h = None
        native = (cw, ch)

    # captions
    captions_burned = False
    post = "[v1]"
    if st.captions.enabled and spec.caption_words:
        out_words = cap.map_words_to_output(spec.caption_words, timeline, spec.start, spec.end)
        if out_words:
            ass_text = cap.build_ass(out_words, st.captions, out_w, out_h, facecam_top_h=facecam_top_h)
            ass_path = work_dir / "captions.ass"
            ass_path.write_text(ass_text, encoding="utf-8")
            post = "[v1]ass=filename=captions.ass[v2]"
            captions_burned = True
    if captions_burned:
        video_tail = f"{post};[v2]format=yuv420p[vout]"
    else:
        video_tail = "[v1]format=yuv420p[vout]"

    # ------------------------------------------------------------------ audio chain
    inputs = ["-ss", f"{spec.start:.3f}", "-t", f"{clip_len:.3f}", "-i", str(spec.source_path)]
    music = _music_path(st.audio.music_track)
    out_len = timeline.duration
    if spec.has_audio:
        a_chain = ["[0:a]asetpts=PTS-STARTPTS"]
        if not timeline.is_trivial:
            a_chain.append(f"aselect='{_select_expr(rel_segments)}',asetpts=N/SR/TB")
        if abs(st.audio.source_volume - 1.0) > 1e-3:
            a_chain.append(f"volume={st.audio.source_volume:.3f}")
        if st.audio.normalize:
            a_chain.append("loudnorm=I=-14:TP=-1.5:LRA=11")
        a_chain.append("aresample=48000")
        audio_pre = ",".join(a_chain) + "[a0]"
    else:
        audio_pre = f"anullsrc=r=48000:cl=stereo,atrim=0:{out_len:.3f}[a0]"
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        audio_tail = (f";[1:a]volume={st.audio.music_volume:.3f},atrim=0:{out_len:.3f},asetpts=PTS-STARTPTS,"
                      f"aresample=48000[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]")
    else:
        audio_tail = ";[a0]anull[aout]"

    script = ";\n".join([video_pre, layout, video_tail, audio_pre]) + audio_tail + "\n"
    script_path = work_dir / "filters.txt"
    script_path.write_text(script, encoding="utf-8")

    args = [*inputs, "-filter_complex_script", str(script_path), "-map", "[vout]", "-map", "[aout]",
            *v_args, "-pix_fmt", "yuv420p", "-r", str(fps), "-g", str(fps * 2),
            "-c:a", "aac", "-b:a", a_bitrate, "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", "-shortest"]
    if s.ffmpeg_threads:
        args += ["-threads", str(s.ffmpeg_threads)]
    args.append(str(out_path))
    # run with cwd = work_dir so the ASS filename needs no escaping
    import subprocess

    cmd = [s.ffmpeg_bin, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(work_dir), timeout=3600)
    (work_dir / "ffmpeg.log").write_text(" ".join(cmd) + "\n\n" + proc.stderr)
    if proc.returncode != 0 or not out_path.exists():
        raise ProcessingFailed(f"Render failed: {proc.stderr.strip()[-700:]}")
    info = probe(out_path)
    return {
        "width": info.width, "height": info.height, "fps": info.fps, "duration": round(info.duration, 3),
        "bytes": info.size_bytes, "captions_burned": captions_burned, "facecam_layout": facecam_used,
        "silence_cuts": max(len(timeline.segments) - 1, 0), "native_crop": {"width": native[0], "height": native[1]},
        "upscaled": bool(out_w > native[0] or out_h > native[1]), "purpose": spec.purpose,
        "orientation": st.orientation, "reframe": st.reframe,
        "video_codec": "h264", "audio_codec": "aac", "music": bool(music),
    }
