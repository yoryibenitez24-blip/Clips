"use client";

import type { Orientation } from "@/lib/types";

const ASPECT: Record<Orientation, string> = { "9:16": "9 / 16", "16:9": "16 / 9", "1:1": "1 / 1" };

export function VideoPlayer({ src, orientation, poster, className = "", autoPlay = false, muted = false, videoRef }:
  { src: string | null; orientation: Orientation; poster?: string | null; className?: string; autoPlay?: boolean; muted?: boolean; videoRef?: React.Ref<HTMLVideoElement> }) {
  return (
    <div className={`relative w-full overflow-hidden rounded-xl bg-black ${className}`} style={{ aspectRatio: ASPECT[orientation] }}>
      {src ? (
        <video key={src} ref={videoRef} src={src} controls playsInline preload="metadata" autoPlay={autoPlay} muted={muted}
          className="absolute inset-0 h-full w-full object-contain" poster={poster ?? undefined} />
      ) : (
        <div className="absolute inset-0 grid place-items-center text-sm text-ink-400">No preview yet</div>
      )}
    </div>
  );
}
