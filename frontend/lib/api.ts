import type { Clip, ClipDuration, ClipStyle, CaptionAnimation, ExportSettings, Job, Options, Orientation, Project, ProjectSummary, VideoInfo } from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  code?: string;
  payload?: any;
  constructor(status: number, message: string, code?: string, payload?: any) {
    super(message);
    this.status = status;
    this.code = code;
    this.payload = payload;
  }
}

function ownerToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    let t = window.localStorage.getItem("clips_owner");
    if (!t) {
      t = (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2)).replace(/-/g, "");
      window.localStorage.setItem("clips_owner", t);
    }
    return t;
  } catch {
    return null;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string> | undefined) };
  if (!(init.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const owner = ownerToken();
  if (owner) headers["X-Owner-Token"] = owner;
  const res = await fetch(`${API_URL}${path}`, { ...init, headers, cache: "no-store" });
  if (res.status === 204) return undefined as T;
  let data: any = null;
  const text = await res.text();
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail : detail?.message || (Array.isArray(detail) ? detail.map((d: any) => d.msg).join(", ") : `Request failed (${res.status})`);
    throw new ApiError(res.status, message, typeof detail === "object" ? detail?.code : undefined, detail);
  }
  return data as T;
}

export const api = {
  options: () => request<Options>("/api/options"),
  inspect: (url: string) => request<VideoInfo>("/api/videos/inspect", { method: "POST", body: JSON.stringify({ url }) }),
  analyze: (body: { youtubeUrl: string; clipDuration: ClipDuration; clipStyle: ClipStyle; orientation: Orientation; captionStyle: CaptionAnimation; captionsEnabled: boolean; language: string; maxClips?: number }) =>
    request<{ project_id: string; job_id: string; video: VideoInfo }>("/api/videos/analyze", { method: "POST", body: JSON.stringify(body) }),
  upload: async (file: File, onProgress?: (frac: number) => void) =>
    new Promise<{ url: string; filename: string; bytes: number; duration_seconds: number }>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_URL}/api/videos/upload`);
      xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
      xhr.onload = () => {
        try {
          const data = JSON.parse(xhr.responseText);
          if (xhr.status >= 400) reject(new ApiError(xhr.status, data?.detail?.message || data?.detail || "Upload failed"));
          else resolve(data);
        } catch { reject(new ApiError(xhr.status, "Upload failed")); }
      };
      xhr.onerror = () => reject(new ApiError(0, "Upload failed: network error"));
      const form = new FormData();
      form.append("file", file);
      xhr.send(form);
    }),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  projects: () => request<ProjectSummary[]>("/api/projects"),
  project: (id: string) => request<Project>(`/api/projects/${id}`),
  retryProject: (id: string) => request<Job>(`/api/projects/${id}/retry`, { method: "POST" }),
  deleteProject: (id: string) => request<void>(`/api/projects/${id}`, { method: "DELETE" }),
  downloadAll: (id: string, clipIds: string[], exportSettings?: Partial<ExportSettings>) =>
    request<Job>(`/api/projects/${id}/download-all`, { method: "POST", body: JSON.stringify({ clipIds, export: exportSettings ?? null }) }),
  clip: (id: string) => request<Clip>(`/api/clips/${id}`),
  patchClip: (id: string, patch: Record<string, any>) => request<Clip>(`/api/clips/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  renderClip: (id: string, body: { purpose: "preview" | "export"; export?: Partial<ExportSettings> | null; force?: boolean }) =>
    request<Job>(`/api/clips/${id}/render`, { method: "POST", body: JSON.stringify(body) }),
  deleteClip: (id: string) => request<void>(`/api/clips/${id}`, { method: "DELETE" }),
};

/** Poll a job until it finishes. Resolves with the final job (completed or failed). */
export async function waitForJob(jobId: string, onUpdate?: (job: Job) => void, intervalMs = 1500, signal?: AbortSignal): Promise<Job> {
  for (;;) {
    if (signal?.aborted) throw new ApiError(0, "cancelled");
    const job = await api.job(jobId);
    onUpdate?.(job);
    if (job.status === "completed" || job.status === "failed") return job;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
