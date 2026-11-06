"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { api, waitForJob } from "@/lib/api";
import { DURATION_LABELS, ORIENTATION_LABELS, STYLE_LABELS, fmtTime } from "@/lib/format";
import type { Clip, Job, Project } from "@/lib/types";
import { ClipCard } from "./ClipCard";
import { ProcessingStatus } from "./ProcessingStatus";
import { Spinner } from "./Spinner";

export function ProjectView({ projectId }: { projectId: string }) {
  const [project, setProject] = useState<Project | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [zip, setZip] = useState<{ progress: number; label: string } | null>(null);
  const [zipError, setZipError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      const p = await api.project(projectId);
      setProject(p);
      setJob(p.active_job);
      setError(null);
      return p;
    } catch (e: any) { setError(e.message); return null; }
  }, [projectId]);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      const p = await load();
      if (cancelled) return;
      const active = p && (p.status === "queued" || p.status === "processing");
      if (active) timer.current = setTimeout(tick, 2000);
    };
    tick();
    return () => { cancelled = true; if (timer.current) clearTimeout(timer.current); };
  }, [load]);

  function onClipChanged(c: Clip) {
    setProject((p) => p ? { ...p, clips: p.clips.map((x) => (x.id === c.id ? c : x)) } : p);
  }

  async function downloadSelected() {
    if (!project) return;
    const ids = selected.size ? [...selected] : project.clips.map((c) => c.id);
    setZipError(null);
    setZip({ progress: 0, label: "Preparing" });
    try {
      const j = await api.downloadAll(project.id, ids);
      const done = await waitForJob(j.id, (u) => setZip({ progress: u.progress, label: u.stage_label || "Working" }));
      if (done.status === "failed") throw new Error(done.error || "ZIP export failed");
      await load();
      if (done.result?.url) window.open(done.result.url, "_blank");
    } catch (e: any) { setZipError(e.message); }
    finally { setZip(null); }
  }

  async function retry() {
    if (!project) return;
    try { await api.retryProject(project.id); await load(); const p = await load(); if (p) { timer.current = setTimeout(async function tick() { const q = await load(); if (q && (q.status === "queued" || q.status === "processing")) timer.current = setTimeout(tick, 2000); }, 2000); } }
    catch (e: any) { setError(e.message); }
  }

  if (error && !project) return <div className="card p-6 text-red-200">{error}</div>;
  if (!project) return <div className="flex items-center gap-2 text-ink-300"><Spinner /> Loading project…</div>;

  const processing = project.status === "queued" || project.status === "processing";
  const s = project.settings;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
        {project.thumbnail_url && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={project.thumbnail_url} alt="" className="h-20 w-36 rounded-xl object-cover" />
        )}
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-xl font-semibold">{project.title || "Untitled video"}</h1>
          <div className="mt-1 text-sm text-ink-400">
            {project.author ? `${project.author} · ` : ""}{fmtTime(project.duration_seconds)}{project.width ? ` · ${project.width}×${project.height} @ ${Math.round(project.fps || 0)}fps` : ""}
            {project.language ? ` · ${project.language.toUpperCase()}` : ""}
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
            {[STYLE_LABELS[s.clip_style], DURATION_LABELS[s.clip_duration], ORIENTATION_LABELS[s.orientation]].filter(Boolean).map((t) => <span key={t} className="rounded-md bg-white/5 px-2 py-0.5 text-ink-300">{t}</span>)}
          </div>
        </div>
        <Link href="/" className="btn-ghost">New video</Link>
      </div>

      <AnimatePresence mode="wait">
        {(processing || project.status === "failed") && job && (
          <motion.div key="status" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <ProcessingStatus job={job} />
            {project.status === "failed" && (
              <div className="mt-3 flex gap-2">
                <button onClick={retry} className="btn-ghost">Retry processing</button>
                <Link href="/" className="btn-primary">Try another link</Link>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {project.clips.length > 0 && (
        <section>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold">{project.clips.length} generated clip{project.clips.length === 1 ? "" : "s"}</h2>
              <p className="text-xs text-ink-400">
                {project.analysis_summary.has_transcript ? `${project.analysis_summary.transcript_words} transcribed words · ` : "No speech transcript · "}
                {project.analysis_summary.scene_count} scenes · {project.analysis_summary.audio_peaks} audio peaks
                {project.analysis_summary.facecam?.detected ? " · facecam detected" : ""}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button onClick={() => setSelected(selected.size === project.clips.length ? new Set() : new Set(project.clips.map((c) => c.id)))} className="btn-ghost">
                {selected.size === project.clips.length ? "Clear selection" : "Select all"}
              </button>
              <button onClick={downloadSelected} disabled={!!zip || project.status !== "completed"} className="btn-primary">
                {zip ? <><Spinner /> {zip.label} {Math.round(zip.progress)}%</> : `Download ${selected.size ? selected.size : "all"} as ZIP`}
              </button>
            </div>
          </div>
          {zipError && <div className="mb-3 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">{zipError}</div>}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {project.clips.map((c) => (
              <ClipCard key={c.id} clip={c} selected={selected.has(c.id)} onChanged={onClipChanged}
                onToggle={() => setSelected((prev) => { const n = new Set(prev); n.has(c.id) ? n.delete(c.id) : n.add(c.id); return n; })} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
