export function fmtTime(seconds: number | null | undefined, withMs = false): string {
  if (seconds == null || Number.isNaN(seconds)) return "–";
  const s = Math.max(0, seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  const base = h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
  if (!withMs) return base;
  const ms = Math.round((s - Math.floor(s)) * 10);
  return `${base}.${ms}`;
}

export function fmtBytes(bytes: number | null | undefined): string {
  if (!bytes) return "–";
  const units = ["B", "KB", "MB", "GB"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export const STYLE_LABELS: Record<string, string> = {
  gaming: "Gaming Highlights", funny: "Funny Moments", best: "Best Moments", reactions: "Reactions",
  cinematic: "Cinematic", talking: "Talking Content",
};
export const DURATION_LABELS: Record<string, string> = { under_30: "Under 30s", "30_60": "30–60s", "60_90": "60–90s", auto: "Automatic" };
export const ANIMATION_LABELS: Record<string, string> = {
  word_pop: "Word Pop", karaoke: "Karaoke Highlight", bounce: "Bounce", fade: "Fade", scale: "Scale", minimal: "Minimal", gaming_bold: "Gaming Bold",
};
export const ORIENTATION_LABELS: Record<string, string> = { "9:16": "Vertical 9:16", "16:9": "Horizontal 16:9", "1:1": "Square 1:1" };
export const REFRAME_LABELS: Record<string, string> = { auto: "Auto Reframe", fill: "Fill", fit: "Fit" };
