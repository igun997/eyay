"use client";
import { cn } from "@/lib/utils";
import type { Frame } from "@/lib/types";

/** 01 / 02 / 03 picker over the follow-up photos. Offsets show how long after the event each one was taken. */
export function FrameStrip({ frames, active, onSelect }: { frames: Frame[]; active: number; onSelect: (idx: number) => void }) {
  if (frames.length < 2) return null;
  return (
    <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${frames.length}, minmax(0, 1fr))` }} role="tablist" aria-label="Follow-up photos">
      {frames.map((f, i) => (
        <button key={f.idx} role="tab" aria-selected={f.idx === active} onClick={() => onSelect(f.idx)}
          className={cn("h-10 rounded-sm border font-mono text-xs transition-colors", f.idx === active ? "border-brand bg-surface-2 text-foreground" : "border-border text-text-2 hover:bg-surface-2")}>
          {String(i + 1).padStart(2, "0")} <span className="text-text-3">+{(f.offset_ms / 1000).toFixed(1)}s</span>
        </button>
      ))}
    </div>
  );
}
