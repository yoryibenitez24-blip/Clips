"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion } from "motion/react";
import { api, waitForJob } from "@/lib/api";
import { ANIMATION_LABELS, ORIENTATION_LABELS, REFRAME_LABELS, fmtBytes, fmtTime } from "@/lib/format";
import type { CaptionAnimation, CaptionWord, Clip, ClipSettings, Options, Orientation, Project, ReframeMode } from "@/lib/types";
import { Spinner } from "./Spinner";
import { VideoPlayer } from "./VideoPlayer";

const ANIMATIONS: CaptionAnimation[] = ["word_pop", "karaoke", "bounce", "fade", "scale", "minimal", "gaming_bold"];
const ORIENTATIONS: Orientation[] = ["9:16", "16:9", "1:1"];
const REFRAMES: ReframeMode[] = ["auto", "fill", "fit"];

type Draft = { title: string; start_time: number; end_time: number; settings: ClipSettings; caption_words: CaptionWord[]; caption_preset: CaptionAnimation | null };

function chunk(words: CaptionWord[], maxWords: number): CaptionWord[][] {
  const out: CaptionWord[][] = [];
  let cur: CaptionWord[] = [];
  for (const w of words) {
    if (cur.length && (cur.length >= maxWords || w.start - cur[cur.length - 1].end > 0.7 || /[.!?…]$/.test(cur[cur.length - 1].text))) { out.push(cur); cur = []; }
    cur.push(w);
  }
  if (cur.length) out.push(cur);
  return out;
}

function redistribute(words: CaptionWord[], text: string): CaptionWord[] {
  const tokens = text.split(/\s+/).filter(Boolean);
  if (!words.length || !tokens.length) return [];
  if (tokens.length === words.length) return words.map((w, i) => ({ ...w, text: tokens[i] }));
  const s = words[0].start, e = words[words.length - 1].end;
  const total = tokens.reduce((a, t) => a + t.length + 1, 0);
  let cursor = s;
  return tokens.map((t, i) => {
    const end = i === tokens.length - 1 ? e : Math.min(e, cursor + ((t.length + 1) / total) * (e - s));
    const w = { start: +cursor.toFixed(3), end: +Math.max(end, cursor + 0.05).toFixed(3), text: t };
    cursor = end;
    return w;
  });
}

export function ClipEditor({ clipId }: { clipId: string }) {
  const [clip, setClip] = useState<Clip | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [options, setOptions] = useState<Options | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<{ kind: "preview" | "export"; progress: number; label: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tab, setTab] = useState<"trim" | "framing" | "captions" | "audio">("trim");
  const videoRef = useRef<HTMLVideoElement>(null);

  const reset = useCallback((c: Clip) => {
    setClip(c);
    setDraft({ title: c.title, start_time: c.start_time, end_time: c.end_time, settings: c.settings, caption_words: c.caption_words, caption_preset: null });
    setDirty(false);
  }, []);

  useEffect(() => {
    api.clip(clipId).then((c) => { reset(c); return api.project(c.project_id); }).then(setProject).catch((e) => setError(e.message));
    api.options().then(setOptions).catch(() => null);
  }, [clipId, reset]);

  const update = (fn: (d: Draft) => Draft) => { setDraft((d) => (d ? fn(d) : d)); setDirty(true); };
  const setSettings = (patch: Partial<ClipSettings>) => update((d) => ({ ...d, settings: { ...d.settings, ...patch } }));
  const setCaptions = (patch: Partial<ClipSettings["captions"]>) => update((d) => ({ ...d, settings: { ...d.settings, captions: { ...d.settings.captions, ...patch } } }));
  const setAudio = (patch: Partial<ClipSettings["audio"]>) => update((d) => ({ ...d, settings: { ...d.settings, audio: { ...d.settings.audio, ...patch } } }));
  const setExport = (patch: Partial<ClipSettings["export"]>) => update((d) => ({ ...d, settings: { ...d.settings, export: { ...d.settings.export, ...patch } } }));

  async function save(): Promise<Clip> {
    if (!draft || !clip) throw new Error("not loaded");
    const body: Record<string, any> = { title: draft.title, start_time: draft.start_time, end_time: draft.end_time, settings: draft.settings, caption_words: draft.caption_words };
    if (draft.caption_preset) body.caption_preset = draft.caption_preset;
    const saved = await api.patchClip(clip.id, body);
    setClip(saved);
    setDraft((d) => (d ? { ...d, caption_preset: null, settings: saved.settings, caption_words: saved.caption_words } : d));
    setDirty(false);
    return saved;
  }

  async function run(kind: "preview" | "export") {
    setError(null);
    setNotice(null);
    setBusy({ kind, progress: 0, label: "Saving" });
    try {
      const saved = await save();
      const job = await api.renderClip(saved.id, { purpose: kind, export: kind === "export" ? saved.settings.export : null });
      const done = await waitForJob(job.id, (j) => setBusy({ kind, progress: j.progress, label: j.stage_label || "Working" }));
      if (done.status === "failed") throw new Error(done.error || "Render failed");
      const fresh = await api.clip(saved.id);
      setClip(fresh);
      if (kind === "export") {
        const info = done.result || {};
        setNotice(`Exported ${info.width}×${info.height} @ ${info.fps} fps · ${fmtBytes(info.bytes)}${info.upscaled ? ` · source detail ${info.native_crop?.width}×${info.native_crop?.height} (upscaled)` : ""}`);
        if (fresh.export_url) window.open(fresh.export_url, "_blank");
      }
    } catch (e: any) { setError(e.message); }
    finally { setBusy(null); }
  }

  const phrases = useMemo(() => (draft ? chunk(draft.caption_words, draft.settings.captions.max_words) : []), [draft]);

  if (error && !clip) return <div className="card p-6 text-red-200">{error}</div>;
  if (!clip || !draft) return <div className="flex items-center gap-2 text-ink-300"><Spinner /> Loading clip…</div>;

  const maxDur = project?.duration_seconds ?? clip.detected_end + 30;
  const trimMin = Math.max(0, clip.detected_start - 15);
  const trimMax = Math.min(maxDur, clip.detected_end + 15);
  const cap = draft.settings.captions;
  const facecam = clip.layout?.facecam?.detected;
  const exportReady = clip.export_status === "ready" && clip.export_url && !dirty;

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,420px)_1fr]">
      {/* ------------------------------------------------ preview column */}
      <div className="space-y-4 lg:sticky lg:top-20 lg:self-start">
        <div className="flex items-center justify-between">
          <Link href={`/projects/${clip.project_id}`} className="text-sm text-ink-400 hover:text-ink-100">← Back to clips</Link>
          <span className="text-xs text-ink-400">Source {fmtTime(draft.start_time, true)} → {fmtTime(draft.end_time, true)}</span>
        </div>
        <div className={draft.settings.orientation === "9:16" ? "mx-auto max-w-[360px]" : ""}>
          <VideoPlayer src={clip.preview_url} orientation={clip.settings.orientation} videoRef={videoRef} />
        </div>
        {clip.preview_status === "failed" && <div className="rounded-xl bg-red-500/10 px-3 py-2 text-xs text-red-200">Preview failed: {clip.render_error}</div>}
        {dirty && <div className="rounded-xl border border-amber-400/20 bg-amber-400/10 px-3 py-2 text-xs text-amber-200">Unsaved changes — update the preview to see them.</div>}
        <div className="flex gap-2">
          <button onClick={() => run("preview")} disabled={!!busy} className="btn-ghost flex-1">
            {busy?.kind === "preview" ? <><Spinner /> {busy.label} {Math.round(busy.progress)}%</> : dirty ? "Update preview" : "Regenerate preview"}
          </button>
          {dirty && <button onClick={() => reset(clip)} disabled={!!busy} className="btn-ghost">Discard</button>}
        </div>
        <div className="card p-4">
          <div className="mb-3 text-sm font-medium">Export</div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="label">Resolution</div>
              <div className="flex gap-1.5">{(["720p", "1080p"] as const).map((r) => <button key={r} onClick={() => setExport({ resolution: r })} className={draft.settings.export.resolution === r ? "pill-on" : "pill-off"}>{r}</button>)}</div>
            </div>
            <div>
              <div className="label">Frame rate</div>
              <div className="flex gap-1.5">{([24, 30, 60] as const).map((f) => <button key={f} onClick={() => setExport({ fps: f })} className={draft.settings.export.fps === f ? "pill-on" : "pill-off"}>{f}</button>)}</div>
            </div>
            <div className="col-span-2">
              <div className="label">Quality</div>
              <div className="flex items-center gap-3 text-xs text-ink-300">
                <select className="input !w-auto" value={draft.settings.export.bitrate_kbps ?? "crf"} onChange={(e) => setExport({ bitrate_kbps: e.target.value === "crf" ? null : Number(e.target.value) })}>
                  <option value="crf">High quality (CRF {draft.settings.export.crf})</option>
                  <option value="6000">6 Mbps</option>
                  <option value="10000">10 Mbps</option>
                  <option value="16000">16 Mbps</option>
                  <option value="24000">24 Mbps</option>
                </select>
                <span>H.264 · AAC 192k{project?.fps && draft.settings.export.fps > Math.round(project.fps) ? ` · capped at source ${Math.round(project.fps)} fps` : ""}</span>
              </div>
            </div>
          </div>
          <button onClick={() => (exportReady ? window.open(clip.export_url!, "_blank") : run("export"))} disabled={!!busy} className="btn-primary mt-4 w-full">
            {busy?.kind === "export" ? <><Spinner /> {busy.label} {Math.round(busy.progress)}%</> : exportReady ? "Download export" : "Export clip"}
          </button>
          {notice && <p className="mt-2 text-xs text-emerald-300">{notice}</p>}
          {error && <p className="mt-2 text-xs text-red-300">{error}</p>}
          {clip.export_info?.width && !dirty && <p className="mt-2 text-xs text-ink-400">Last export: {clip.export_info.width}×{clip.export_info.height} @ {clip.export_info.fps} fps · {fmtBytes(clip.export_info.bytes)}{clip.export_info.upscaled ? ` · source detail ${clip.export_info.native_crop?.width}×${clip.export_info.native_crop?.height}` : ""}</p>}
        </div>
      </div>

      {/* ------------------------------------------------ controls column */}
      <div className="space-y-4">
        <input value={draft.title} onChange={(e) => update((d) => ({ ...d, title: e.target.value }))} className="input !text-lg !font-medium" maxLength={200} />
        <p className="text-xs text-ink-400">{clip.reason}</p>
        <div className="flex gap-1 rounded-xl bg-ink-900 p-1">
          {(["trim", "framing", "captions", "audio"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)} className={`flex-1 rounded-lg px-3 py-2 text-sm capitalize transition-colors ${tab === t ? "bg-ink-700 text-ink-100" : "text-ink-400 hover:text-ink-100"}`}>{t}</button>
          ))}
        </div>

        <motion.div key={tab} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="card space-y-5 p-5">
          {tab === "trim" && (
            <>
              <Range label={`Start · ${fmtTime(draft.start_time, true)}`} min={trimMin} max={draft.end_time - 2} step={0.1} value={draft.start_time}
                onChange={(v) => update((d) => ({ ...d, start_time: Math.min(v, d.end_time - 2) }))} />
              <Range label={`End · ${fmtTime(draft.end_time, true)}`} min={draft.start_time + 2} max={trimMax} step={0.1} value={draft.end_time}
                onChange={(v) => update((d) => ({ ...d, end_time: Math.max(v, d.start_time + 2) }))} />
              <div className="flex flex-wrap items-center gap-2 text-xs text-ink-400">
                <span>Duration {fmtTime(draft.end_time - draft.start_time, true)}</span>
                <span>· detected {fmtTime(clip.detected_start, true)} → {fmtTime(clip.detected_end, true)}</span>
                <button onClick={() => update((d) => ({ ...d, start_time: clip.detected_start, end_time: clip.detected_end }))} className="text-accent-300 hover:underline">Reset to detected</button>
              </div>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="accent-[#8b5cf6]" checked={draft.settings.remove_silence} onChange={(e) => setSettings({ remove_silence: e.target.checked })} disabled={!draft.caption_words.length} />
                Remove long pauses between sentences{!draft.caption_words.length && <span className="text-xs text-ink-400">(needs a transcript)</span>}</label>
            </>
          )}

          {tab === "framing" && (
            <>
              <Field label="Orientation"><div className="flex flex-wrap gap-2">{ORIENTATIONS.map((o) => <button key={o} onClick={() => setSettings({ orientation: o })} className={draft.settings.orientation === o ? "pill-on" : "pill-off"}>{ORIENTATION_LABELS[o]}</button>)}</div></Field>
              <Field label="Reframe mode"><div className="flex flex-wrap gap-2">{REFRAMES.map((r) => <button key={r} onClick={() => setSettings({ reframe: r })} className={draft.settings.reframe === r ? "pill-on" : "pill-off"}>{REFRAME_LABELS[r]}</button>)}</div>
                <p className="mt-2 text-xs text-ink-400">{draft.settings.reframe === "auto" ? "Tracks detected faces and motion and pans the crop smoothly." : draft.settings.reframe === "fill" ? "Static centre crop; nudge it with the sliders." : "Whole frame visible with a blurred backdrop."}</p>
              </Field>
              {draft.settings.reframe !== "fit" && (
                <>
                  <Range label={`Horizontal nudge · ${Math.round(draft.settings.crop_offset_x * 100)}%`} min={-1} max={1} step={0.02} value={draft.settings.crop_offset_x} onChange={(v) => setSettings({ crop_offset_x: v })} />
                  <Range label={`Vertical nudge · ${Math.round(draft.settings.crop_offset_y * 100)}%`} min={-1} max={1} step={0.02} value={draft.settings.crop_offset_y} onChange={(v) => setSettings({ crop_offset_y: v })} />
                </>
              )}
              <label className={`flex items-center gap-2 text-sm ${facecam ? "" : "opacity-50"}`}>
                <input type="checkbox" className="accent-[#8b5cf6]" disabled={!facecam} checked={draft.settings.facecam_layout} onChange={(e) => setSettings({ facecam_layout: e.target.checked })} />
                Facecam split layout {facecam ? <span className="text-xs text-ink-400">(facecam detected {clip.layout.facecam?.corner})</span> : <span className="text-xs text-ink-400">(no facecam detected in this video)</span>}
              </label>
            </>
          )}

          {tab === "captions" && (
            <>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="accent-[#8b5cf6]" checked={cap.enabled} onChange={(e) => setCaptions({ enabled: e.target.checked })} /> Burn captions into the clip</label>
              {!draft.caption_words.length && <p className="rounded-lg bg-white/5 px-3 py-2 text-xs text-ink-300">No speech was transcribed in this range, so there are no captions to show.</p>}
              <Field label="Animation preset">
                <div className="flex flex-wrap gap-2">
                  {ANIMATIONS.map((a) => (
                    <button key={a} onClick={() => update((d) => ({ ...d, caption_preset: a, settings: { ...d.settings, captions: { ...d.settings.captions, animation: a, ...(options?.caption_presets[a] ?? {}) } } }))} className={cap.animation === a ? "pill-on" : "pill-off"}>{ANIMATION_LABELS[a]}</button>
                  ))}
                </div>
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Font family">
                  <select className="input" value={cap.font_family} onChange={(e) => setCaptions({ font_family: e.target.value })}>
                    {(options?.fonts ?? [cap.font_family]).map((f) => <option key={f} value={f}>{f}</option>)}
                  </select>
                </Field>
                <Range label={`Font size · ${cap.font_size}`} min={24} max={120} step={2} value={cap.font_size} onChange={(v) => setCaptions({ font_size: v })} />
                <Field label="Position"><div className="flex gap-2">{(["top", "middle", "bottom"] as const).map((p) => <button key={p} onClick={() => setCaptions({ position: p })} className={cap.position === p ? "pill-on" : "pill-off"}>{p}</button>)}</div></Field>
                <Range label={`Words per line · ${cap.max_words}`} min={1} max={8} step={1} value={cap.max_words} onChange={(v) => setCaptions({ max_words: v })} />
                <Color label="Text color" value={cap.color} onChange={(v) => setCaptions({ color: v })} />
                <Color label="Highlight color" value={cap.highlight_color} onChange={(v) => setCaptions({ highlight_color: v })} />
                <Color label="Outline color" value={cap.outline_color} onChange={(v) => setCaptions({ outline_color: v })} />
                <Range label={`Outline width · ${cap.outline_width}`} min={0} max={10} step={1} value={cap.outline_width} onChange={(v) => setCaptions({ outline_width: v })} />
                <Range label={`Shadow · ${cap.shadow}`} min={0} max={6} step={1} value={cap.shadow} onChange={(v) => setCaptions({ shadow: v })} />
                <div className="flex items-end gap-4 pb-2 text-sm">
                  <label className="flex items-center gap-2"><input type="checkbox" className="accent-[#8b5cf6]" checked={cap.bold} onChange={(e) => setCaptions({ bold: e.target.checked })} /> Bold</label>
                  <label className="flex items-center gap-2"><input type="checkbox" className="accent-[#8b5cf6]" checked={cap.uppercase} onChange={(e) => setCaptions({ uppercase: e.target.checked })} /> Uppercase</label>
                </div>
              </div>
              {phrases.length > 0 && (
                <Field label={`Caption text (${draft.caption_words.length} words)`}>
                  <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
                    {phrases.map((ph, i) => (
                      <div key={`${ph[0].start}-${i}`} className="flex items-center gap-2">
                        <button onClick={() => { const v = videoRef.current; if (v) { v.currentTime = Math.max(0, ph[0].start - draft.start_time); v.play().catch(() => null); } }} className="w-14 shrink-0 text-left text-xs tabular-nums text-ink-400 hover:text-accent-300">{fmtTime(ph[0].start - draft.start_time, true)}</button>
                        <input className="input !py-1.5" defaultValue={ph.map((w) => w.text).join(" ")}
                          onBlur={(e) => {
                            const text = e.target.value;
                            if (text === ph.map((w) => w.text).join(" ")) return;
                            update((d) => {
                              const ids = new Set(ph.map((w) => w.start));
                              const replaced = redistribute(ph, text);
                              const rest = d.caption_words.filter((w) => !ids.has(w.start));
                              return { ...d, caption_words: [...rest, ...replaced].sort((a, b) => a.start - b.start) };
                            });
                          }} />
                      </div>
                    ))}
                  </div>
                </Field>
              )}
            </>
          )}

          {tab === "audio" && (
            <>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="accent-[#8b5cf6]" checked={draft.settings.audio.normalize} onChange={(e) => setAudio({ normalize: e.target.checked })} /> Normalize loudness (−14 LUFS)</label>
              <Range label={`Source volume · ${Math.round(draft.settings.audio.source_volume * 100)}%`} min={0} max={2} step={0.05} value={draft.settings.audio.source_volume} onChange={(v) => setAudio({ source_volume: v })} />
              <Field label="Background music">
                {options && options.music_tracks.length > 0 ? (
                  <>
                    <select className="input" value={draft.settings.audio.music_track ?? ""} onChange={(e) => setAudio({ music_track: e.target.value || null })}>
                      <option value="">No music</option>
                      {options.music_tracks.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                    </select>
                    <div className="mt-3"><Range label={`Music volume · ${Math.round(draft.settings.audio.music_volume * 100)}%`} min={0} max={1} step={0.05} value={draft.settings.audio.music_volume} onChange={(v) => setAudio({ music_volume: v })} /></div>
                  </>
                ) : <p className="text-xs text-ink-400">No licensed music library is configured on this server (set MUSIC_LIBRARY_DIR to a folder of tracks you are allowed to use).</p>}
              </Field>
            </>
          )}
        </motion.div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="label">{label}</div>{children}</div>;
}

function Range({ label, min, max, step, value, onChange }: { label: string; min: number; max: number; step: number; value: number; onChange: (v: number) => void }) {
  return (
    <div>
      <div className="label">{label}</div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  );
}

function Color({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className="flex items-center gap-2">
        <input type="color" value={value} onChange={(e) => onChange(e.target.value.toUpperCase())} />
        <input className="input !w-28 font-mono text-xs" value={value} onChange={(e) => /^#[0-9a-fA-F]{6}$/.test(e.target.value) && onChange(e.target.value.toUpperCase())} />
      </div>
    </div>
  );
}
