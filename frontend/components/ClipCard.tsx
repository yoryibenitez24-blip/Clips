"use client";

import Link from "next/link";
import { useState } from "react";
import { motion } from "motion/react";
import { api, waitForJob } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import type { Clip } from "@/lib/types";
import { Spinner } from "./Spinner";
import { VideoPlayer } from "./VideoPlayer";

export function ClipCard({ clip, selected, onToggle, onChanged }: { clip: Clip; selected: boolean; onToggle: () => void; onChanged: (c: Clip) => void }) {
  const [busy, setBusy] = useState<"export" | "preview" | null>(null);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setError(null);
    if (clip.export_url && clip.export_status === "ready") { window.open(clip.export_url, "_blank"); return; }
    setBusy("export");
    try {
      const job = await api.renderClip(clip.id, { purpose: "export" });
      const done = await waitForJob(job.id, (j) => setProgress(j.progress));
      if (done.status === "failed") throw new Error(done.error || "Export failed");
      const fresh = await api.clip(clip.id);
      onChanged(fresh);
      if (fresh.export_url) window.open(fresh.export_url, "_blank");
    } catch (e: any) { setError(e.message); } finally { setBusy(null); }
  }

  async function regenerate() {
    setError(null);
    setBusy("preview");
    try {
      const job = await api.renderClip(clip.id, { purpose: "preview", force: true });
      const done = await waitForJob(job.id, (j) => setProgress(j.progress));
      if (done.status === "failed") throw new Error(done.error || "Render failed");
      onChanged(await api.clip(clip.id));
    } catch (e: any) { setError(e.message); } finally { setBusy(null); }
  }

  const orientation = clip.settings.orientation;
  return (
    <motion.div layout initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className={`card relative flex flex-col overflow-hidden transition-shadow ${selected ? "ring-2 ring-accent-500/70" : ""}`}>
      <label className="absolute left-3 top-3 z-10 flex cursor-pointer items-center gap-2 rounded-lg bg-black/50 px-2 py-1 text-xs backdrop-blur">
        <input type="checkbox" checked={selected} onChange={onToggle} className="accent-[#8b5cf6]" /> Select
      </label>
      <div className={`p-3 ${orientation === "9:16" ? "mx-auto w-full max-w-[260px]" : ""}`}>
        {clip.preview_status === "ready" ? <VideoPlayer src={clip.preview_url} orientation={orientation} />
          : clip.preview_status === "failed" ? <div className="grid aspect-[9/16] place-items-center rounded-xl bg-ink-850 p-4 text-center text-xs text-red-300">Preview failed: {clip.render_error}</div>
          : <div className="grid aspect-[9/16] place-items-center rounded-xl bg-ink-850 text-xs text-ink-400"><Spinner /></div>}
      </div>
      <div className="flex flex-1 flex-col gap-2 px-4 pb-4">
        <div className="flex items-start justify-between gap-2">
          <h3 className="line-clamp-2 font-medium leading-snug">{clip.title}</h3>
          <span className="shrink-0 rounded-md bg-white/5 px-2 py-0.5 text-xs tabular-nums text-ink-300">{fmtTime(clip.duration)}</span>
        </div>
        <div className="text-xs text-ink-400">Source {fmtTime(clip.start_time)} → {fmtTime(clip.end_time)}{clip.layout?.facecam?.detected ? " · facecam" : ""}</div>
        <p className="line-clamp-2 text-xs text-ink-300/80">{clip.reason}</p>
        {error && <div className="rounded-lg bg-red-500/10 px-2 py-1 text-xs text-red-200">{error}</div>}
        <div className="mt-auto flex items-center gap-2 pt-2">
          <Link href={`/clips/${clip.id}`} className="btn-ghost flex-1 !py-2">Edit</Link>
          <button onClick={download} disabled={busy !== null} className="btn-primary flex-1 !py-2">
            {busy === "export" ? <><Spinner /> {Math.round(progress)}%</> : clip.export_status === "ready" ? "Download" : "Export"}
          </button>
          <button onClick={regenerate} disabled={busy !== null} title="Regenerate preview" className="btn-ghost !px-3 !py-2">
            {busy === "preview" ? <Spinner /> : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7" /><path d="M21 3v6h-6" /></svg>
            )}
          </button>
        </div>
      </div>
    </motion.div>
  );
}
