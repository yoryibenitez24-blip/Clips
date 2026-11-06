export type ClipDuration = "under_30" | "30_60" | "60_90" | "auto";
export type ClipStyle = "gaming" | "funny" | "best" | "reactions" | "cinematic" | "talking";
export type Orientation = "9:16" | "16:9" | "1:1";
export type ReframeMode = "auto" | "fit" | "fill";
export type CaptionAnimation = "word_pop" | "karaoke" | "bounce" | "fade" | "scale" | "minimal" | "gaming_bold";
export type CaptionPosition = "top" | "middle" | "bottom";

export interface AccessInfo { allowed: boolean; adapter: string; reason: string }

export interface VideoInfo {
  source_kind: string;
  video_id: string | null;
  canonical_url: string;
  title: string | null;
  author: string | null;
  thumbnail_url: string | null;
  duration_seconds: number | null;
  access: AccessInfo;
}

export interface StageEntry { stage: string; label: string; status: "running" | "done" | "failed"; detail?: string; started_at?: string; finished_at?: string }

export interface Job {
  id: string; project_id: string; clip_id: string | null; kind: "analyze" | "render" | "zip";
  status: "queued" | "running" | "completed" | "failed";
  stage: string | null; stage_label: string | null; progress: number; stage_history: StageEntry[];
  result: Record<string, any> | null; error: string | null; warnings: string[]; metrics: Record<string, any>;
  attempts: number; created_at: string; updated_at: string;
}

export interface CaptionSettings {
  enabled: boolean; animation: CaptionAnimation; font_family: string; font_size: number; color: string;
  highlight_color: string; outline_color: string; outline_width: number; shadow: number; position: CaptionPosition;
  max_words: number; uppercase: boolean; bold: boolean;
}
export interface AudioSettings { normalize: boolean; source_volume: number; music_track: string | null; music_volume: number }
export interface ExportSettings { resolution: "720p" | "1080p"; fps: 24 | 30 | 60; bitrate_kbps: number | null; crf: number; preset: string }
export interface ClipSettings {
  orientation: Orientation; reframe: ReframeMode; crop_offset_x: number; crop_offset_y: number; facecam_layout: boolean;
  remove_silence: boolean; captions: CaptionSettings; audio: AudioSettings; export: ExportSettings;
}
export interface CaptionWord { start: number; end: number; text: string }

export interface Clip {
  id: string; project_id: string; index: number; title: string; reason: string; score: number | null;
  signals: Record<string, number>; start_time: number; end_time: number; detected_start: number; detected_end: number;
  duration: number; settings: ClipSettings; caption_words: CaptionWord[]; layout: { facecam?: { detected: boolean; corner?: string } };
  preview_url: string | null; preview_status: string; export_url: string | null; export_status: string;
  export_info: Record<string, any>; render_error: string | null; created_at: string; updated_at: string;
}

export interface Project {
  id: string; source_url: string; source_kind: string; video_id: string | null; title: string | null; author: string | null;
  thumbnail_url: string | null; duration_seconds: number | null; width: number | null; height: number | null; fps: number | null;
  language: string | null; status: "queued" | "processing" | "completed" | "failed"; error: string | null;
  settings: Record<string, any>; analysis_summary: Record<string, any>; source_available: boolean; clips: Clip[];
  active_job: Job | null; created_at: string; updated_at: string;
}

export interface ProjectSummary { id: string; title: string | null; thumbnail_url: string | null; duration_seconds: number | null; status: string; clip_count: number; created_at: string }

export interface Options {
  clip_durations: { id: ClipDuration; min: number; max: number; target: number }[];
  clip_styles: ClipStyle[]; orientations: Orientation[]; reframe_modes: ReframeMode[];
  caption_presets: Record<CaptionAnimation, Partial<CaptionSettings>>; caption_positions: CaptionPosition[];
  fonts: string[]; music_tracks: { id: string; name: string }[]; resolutions: string[]; fps_options: number[];
  languages: string[]; limits: Record<string, number>;
  ingest: { youtube_metadata: boolean; youtube_media_retrieval: boolean; direct_url: boolean; upload: boolean };
  transcription_model: string; llm_ranking: boolean;
}
