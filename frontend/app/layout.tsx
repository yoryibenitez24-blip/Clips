import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Clips — turn long videos into short-form clips",
  description: "Paste a video link, get automatically edited vertical clips with animated captions.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="sticky top-0 z-30 border-b border-white/[0.06] bg-ink-950/70 backdrop-blur">
          <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
            <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
              <span className="grid h-7 w-7 place-items-center rounded-lg bg-accent-500 text-white">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M6 3l12 9-12 9z" /></svg>
              </span>
              Clips
            </Link>
            <nav className="flex items-center gap-1 text-sm text-ink-300">
              <Link href="/" className="rounded-lg px-3 py-1.5 hover:bg-white/5 hover:text-ink-100">New</Link>
              <Link href="/projects" className="rounded-lg px-3 py-1.5 hover:bg-white/5 hover:text-ink-100">Projects</Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 pb-24 pt-8">{children}</main>
      </body>
    </html>
  );
}
