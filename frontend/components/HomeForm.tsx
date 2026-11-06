"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import { api, ApiError } from "@/lib/api";
import { ANIMATION_LABELS, DURATION_LABELS, ORIENTATION_LABELS, STYLE_LABELS, fmtTime } from "@/lib/format";
import type { CaptionAnimation, ClipDuration, ClipStyle, Options, Orientation, VideoInfo } from "@/lib/types";
import { Spinner } from "./Spinner";

const DURATIONS: ClipDuration[] = ["under_30", "30_60", "60_90", "auto"];
const STYLES: ClipStyle[] = ["gaming", "funny", "best", "reactions", "cinematic", "talking"];
const ORIENTATIONS: Orientation[] = ["9:16", "16:9", "1:1"];
const ANIMATIONS: CaptionAnimation[] = ["word_pop", "karaoke", "bounce", "fade", "scale", "minimal", "gaming_bold"];

export function HomeForm() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [options, setOptions] = useState<Options | null>(null);
  const [info, setInfo] = useState<VideoInfo | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [duration, setDuration] = useState<ClipDuration>("auto");
  const [style, setStyle] = useState<ClipStyle>("best");
  const [orientation, setOrientation] = useState<Orientation>("9:16");
  const [animation, setAnimation] = useState<CaptionAnimation>("word_pop");
  const [captions, setCaptions] = useState(true);
  const [language, setLanguage] = useState("auto");
  const [showAlt, setShowAlt] = useState(false);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inspectSeq = useRef(0);

  useEffect(() => { api.options().then(setOptions).catch(() => setOptions(null)); }, []);

  useEffect(() => {
    setInfo(null);
    setError(null);
    const trimmed = url.trim();
    if (trimmed.length < 8) return;
    if (debounce.current) clearTimeout(debounce.current);
    const seq = ++inspectSeq.current;
    debounce.current = setTimeout(async () => {
      setInspecting(true);
      try {
        const result = await api.inspect(trimmed);
        if (seq === inspectSeq.current) setInfo(result); // ignore responses for an older URL
      } catch (e: any) {
        if (seq === inspectSeq.current) setError(e instanceof ApiError ? e.message : "Could not check that link");
      } finally {
        if (seq === inspectSeq.current) setInspecting(false);
      }
    }, 500);
    return () => { if (debounce.current) clearTimeout(debounce.current); };
  }, [url]);

  async function generate() {
    if (!info?.access.allowed) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.analyze({ youtubeUrl: url.trim(), clipDuration: duration, clipStyle: style, orientation, captionStyle: animation, captionsEnabled: captions, language });
      router.push(`/projects/${res.project_id}`);
    } catch (e: any) {
      setError(e instanceof ApiError ? e.message : "Something went wrong");
      setSubmitting(false);
    }
  }

  async function onFile(file: File | undefined) {
    if (!file) return;
    setError(null);
    setUploadPct(0);
    try {
      const res = await api.upload(file, (f) => setUploadPct(Math.round(f * 100)));
      setUrl(res.url);
    } catch (e: any) { setError(e.message); }
    finally { setUploadPct(null); }
  }

  const ready = !!info?.access.allowed && !inspecting && !submitting;

  return (
    <div className="mx-auto max-w-3xl">
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="text-center">
        <span className="mb-4 inline-flex items-center gap-2 rounded-full border border-accent-500/30 bg-accent-500/10 px-3 py-1 text-xs text-accent-300">
          <span className="h-1.5 w-1.5 rounded-full bg-accent-400" /> Automatic clipping · animated captions · auto reframe
        </span>
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">Long video in. <span className="text-accent-300">Short clips out.</span></h1>
        <p className="mx-auto mt-4 max-w-xl text-ink-300">Paste a link, pick a style, and get a set of intelligently selected, captioned clips ready to download.</p>
      </motion.div>

      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.08 }} className="card mt-10 p-4 sm:p-6">
        <label className="label" htmlFor="url">Paste your YouTube video link</label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="relative flex-1">
            <input id="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://www.youtube.com/watch?v=…" className="input !py-3 !pr-10 text-base" autoComplete="off" spellCheck={false} />
            {inspecting && <span className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-400"><Spinner /></span>}
          </div>
          <button onClick={generate} disabled={!ready} className="btn-primary !px-6 !py-3 text-base">
            {submitting ? <><Spinner /> Starting…</> : "Generate Clips"}
          </button>
        </div>

        <AnimatePresence>
          {info && (
            <motion.div key="info" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
              <div className="mt-4 flex gap-4 rounded-xl border border-white/[0.06] bg-ink-850 p-3">
                {info.thumbnail_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={info.thumbnail_url} alt="" className="h-20 w-36 shrink-0 rounded-lg object-cover" />
                ) : <div className="grid h-20 w-36 shrink-0 place-items-center rounded-lg bg-ink-700 text-xs text-ink-400">{info.source_kind}</div>}
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{info.title || "Untitled video"}</div>
                  <div className="mt-0.5 text-xs text-ink-400">{info.author ? `${info.author} · ` : ""}{info.duration_seconds != null ? fmtTime(info.duration_seconds) : "duration unknown"} · {info.source_kind.replace("_", " ")}</div>
                  <div className={`mt-2 inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs ${info.access.allowed ? "bg-emerald-500/10 text-emerald-300" : "bg-red-500/10 text-red-200"}`}>
                    <span className={`h-1.5 w-1.5 rounded-full ${info.access.allowed ? "bg-emerald-400" : "bg-red-400"}`} />
                    {info.access.allowed ? "Media retrieval available" : "Media retrieval not available"}
                  </div>
                  {!info.access.allowed && <p className="mt-2 text-xs leading-relaxed text-ink-300">{info.access.reason}</p>}
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
        {error && <div className="mt-3 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">{error}</div>}

        <div className="mt-6 grid gap-5 sm:grid-cols-2">
          <Field label="Clip duration">{DURATIONS.map((d) => <Pill key={d} on={duration === d} onClick={() => setDuration(d)}>{DURATION_LABELS[d]}</Pill>)}</Field>
          <Field label="Orientation">{ORIENTATIONS.map((o) => <Pill key={o} on={orientation === o} onClick={() => setOrientation(o)}>{ORIENTATION_LABELS[o]}</Pill>)}</Field>
          <Field label="Clip style" className="sm:col-span-2">{STYLES.map((s) => <Pill key={s} on={style === s} onClick={() => setStyle(s)}>{STYLE_LABELS[s]}</Pill>)}</Field>
          <Field label={<span className="flex items-center justify-between">Captions <label className="flex items-center gap-1.5 normal-case tracking-normal text-ink-300"><input type="checkbox" checked={captions} onChange={(e) => setCaptions(e.target.checked)} className="accent-[#8b5cf6]" /> enabled</label></span>} className="sm:col-span-2">
            {ANIMATIONS.map((a) => <Pill key={a} on={animation === a} onClick={() => setAnimation(a)} disabled={!captions}>{ANIMATION_LABELS[a]}</Pill>)}
          </Field>
          <Field label="Speech language">
            {(options?.languages ?? ["auto", "en", "es"]).map((l) => <Pill key={l} on={language === l} onClick={() => setLanguage(l)}>{l === "auto" ? "Auto-detect" : l === "en" ? "English" : l === "es" ? "Español" : l}</Pill>)}
          </Field>
        </div>
      </motion.div>

      <div className="mt-4 text-center text-xs text-ink-400">
        {options && !options.ingest.youtube_media_retrieval && (
          <p className="mx-auto max-w-xl">This deployment shows YouTube metadata but is not authorized to retrieve YouTube media. The operator can enable it for owned or licensed content.</p>
        )}
        <button onClick={() => setShowAlt((v) => !v)} className="mt-2 text-accent-300 hover:underline">
          {showAlt ? "Hide other sources" : "Have a licensed file? Use a direct media URL or upload"}
        </button>
      </div>
      <AnimatePresence>
        {showAlt && (
          <motion.div key="alt" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} className="card mt-3 grid gap-4 p-4 sm:grid-cols-2">
            <div>
              <div className="label">Direct media URL</div>
              <p className="text-xs text-ink-400">Paste a public https link to an MP4/MOV/WebM you hold the rights to into the box above. It is validated and fetched server-side.</p>
            </div>
            <div>
              <div className="label">Upload a file</div>
              <input ref={fileRef} type="file" accept="video/mp4,video/quicktime,video/webm,video/x-matroska,.mp4,.mov,.webm,.mkv" className="hidden" onChange={(e) => onFile(e.target.files?.[0])} />
              <button onClick={() => fileRef.current?.click()} disabled={uploadPct !== null || !options?.ingest.upload} className="btn-ghost w-full">
                {uploadPct !== null ? <><Spinner /> Uploading {uploadPct}%</> : "Choose video file"}
              </button>
              {options && !options.ingest.upload && <p className="mt-1 text-xs text-ink-400">Uploads are disabled on this deployment.</p>}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function Field({ label, children, className = "" }: { label: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <div className={className}>
      <div className="label">{label}</div>
      <div className="flex flex-wrap gap-2">{children}</div>
    </div>
  );
}

function Pill({ on, onClick, children, disabled }: { on: boolean; onClick: () => void; children: React.ReactNode; disabled?: boolean }) {
  return <button type="button" onClick={onClick} disabled={disabled} className={`${on ? "pill-on" : "pill-off"} disabled:opacity-40`}>{children}</button>;
}
