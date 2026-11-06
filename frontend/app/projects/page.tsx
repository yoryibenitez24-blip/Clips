"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import type { ProjectSummary } from "@/lib/types";
import { Spinner } from "@/components/Spinner";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.projects().then(setProjects).catch((e) => setError(e.message)); }, []);

  async function remove(id: string) {
    if (!confirm("Delete this project and its clips?")) return;
    try { await api.deleteProject(id); setProjects((p) => p?.filter((x) => x.id !== id) ?? null); }
    catch (e: any) { setError(e.message); }
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Your projects</h1>
        <Link href="/" className="btn-primary">New video</Link>
      </div>
      {error && <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">{error}</div>}
      {!projects ? <div className="flex items-center gap-2 text-ink-300"><Spinner /> Loading…</div> : projects.length === 0 ? (
        <div className="card p-10 text-center text-ink-400">No projects yet. Paste a link on the home page to create one.</div>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((p) => (
            <li key={p.id} className="card flex gap-3 p-3">
              <Link href={`/projects/${p.id}`} className="shrink-0">
                {p.thumbnail_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={p.thumbnail_url} alt="" className="h-16 w-28 rounded-lg object-cover" />
                ) : <div className="h-16 w-28 rounded-lg bg-ink-700" />}
              </Link>
              <div className="min-w-0 flex-1">
                <Link href={`/projects/${p.id}`} className="line-clamp-2 text-sm font-medium hover:text-accent-300">{p.title || "Untitled video"}</Link>
                <div className="mt-1 text-xs text-ink-400">{fmtTime(p.duration_seconds)} · {p.clip_count} clips · <span className={p.status === "failed" ? "text-red-300" : p.status === "completed" ? "text-emerald-300" : "text-accent-300"}>{p.status}</span></div>
                <div className="mt-1 text-[11px] text-ink-400">{new Date(p.created_at).toLocaleString()}</div>
              </div>
              <button onClick={() => remove(p.id)} className="self-start text-xs text-ink-400 hover:text-red-300">Delete</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
