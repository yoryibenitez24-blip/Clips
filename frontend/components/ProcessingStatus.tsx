"use client";

import { motion } from "motion/react";
import type { Job } from "@/lib/types";
import { Spinner } from "./Spinner";

const ANALYZE_STAGES = [
  ["checking", "Checking video"], ["retrieving", "Retrieving media"], ["preparing", "Preparing video"],
  ["transcribing", "Transcribing"], ["scenes", "Analyzing scenes"], ["highlights", "Identifying highlights"],
  ["clips", "Generating clips"], ["rendering", "Rendering"], ["complete", "Complete"],
] as const;

export function ProcessingStatus({ job }: { job: Job }) {
  const history = new Map(job.stage_history.map((h) => [h.stage, h]));
  const currentIdx = ANALYZE_STAGES.findIndex(([k]) => k === job.stage);
  const failed = job.status === "failed";
  return (
    <div className="card p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <div className="text-sm text-ink-400">{failed ? "Processing failed" : job.status === "completed" ? "Done" : "Processing"}</div>
          <div className="text-lg font-semibold">{failed ? (job.stage_label || "Error") : (job.stage_label || "Queued")}</div>
        </div>
        <div className="text-2xl font-semibold tabular-nums text-accent-300">{Math.round(job.progress)}%</div>
      </div>
      <div className="mb-6 h-2 w-full overflow-hidden rounded-full bg-ink-700">
        <motion.div className={`h-full rounded-full ${failed ? "bg-red-500" : "bg-accent-500"}`}
          initial={{ width: 0 }} animate={{ width: `${Math.max(2, job.progress)}%` }} transition={{ type: "spring", stiffness: 60, damping: 20 }} />
      </div>
      <ol className="grid gap-2 sm:grid-cols-3">
        {ANALYZE_STAGES.map(([key, label], i) => {
          const entry = history.get(key);
          const state = entry?.status === "failed" ? "failed" : entry?.status === "done" || (job.status === "completed") ? "done"
            : key === job.stage && job.status === "running" ? "running" : i < currentIdx ? "done" : "pending";
          return (
            <li key={key} className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${state === "running" ? "bg-accent-500/10 text-accent-200" : state === "done" ? "text-ink-300" : state === "failed" ? "bg-red-500/10 text-red-200" : "text-ink-400/70"}`}>
              <span className="grid h-5 w-5 shrink-0 place-items-center">
                {state === "running" ? <Spinner className="h-4 w-4" /> : state === "done" ? (
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
                ) : state === "failed" ? "✕" : <span className="h-1.5 w-1.5 rounded-full bg-current" />}
              </span>
              <span className="truncate">{label}</span>
              {entry?.detail && state === "running" && <span className="ml-auto truncate text-xs text-ink-400">{entry.detail}</span>}
            </li>
          );
        })}
      </ol>
      {job.warnings.length > 0 && (
        <ul className="mt-4 space-y-1 text-xs text-amber-200/90">
          {job.warnings.map((w, i) => <li key={i}>⚠ {w}</li>)}
        </ul>
      )}
      {failed && job.error && <div className="mt-4 rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">{job.error}</div>}
      <p className="mt-4 text-xs text-ink-400">Processing continues on the server. You can leave this page and come back from <span className="text-ink-300">Projects</span>.</p>
    </div>
  );
}
