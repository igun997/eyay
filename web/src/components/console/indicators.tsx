import { AlertOctagon, Eye, Gauge, MoveRight, Users, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Flag, IndicationKind } from "@/lib/types";
import { SeverityPill } from "./severity-pill";
import { formatSpeed } from "@/lib/format";
import { sensitivityText } from "@/lib/plain";

const KIND: Record<IndicationKind, { icon: LucideIcon; label: string }> = {
  appeared: { icon: Eye, label: "APPEARED" }, moving: { icon: MoveRight, label: "MOVING" },
  lingering: { icon: Eye, label: "LINGERING" }, crowd: { icon: Users, label: "CROWD" },
};

/** Estimated speed (km/h) of the followed object; an estimate for review, scaled by the real distance measured in the camera's speed setup. */
export function SpeedChip({ kmh, className }: { kmh: number | null | undefined; className?: string }) {
  if (kmh == null) return null;
  return (
    <span title="Estimated speed, for review" className={cn("inline-flex h-5 items-center gap-1.5 rounded-sm border border-border-strong px-2 font-mono text-[11px] tracking-wider whitespace-nowrap text-text-2", className)}>
      <Gauge className="size-3" aria-hidden />{formatSpeed(kmh)}
    </span>
  );
}

/** The activity pattern the follow-up photos show (how the object moved). */
export function IndicationLine({ kind, text, burstMs, done, speedKmh }: { kind: IndicationKind | null; text: string | null; burstMs: number | null; done: boolean; speedKmh?: number | null }) {
  if (!kind && done && speedKmh == null) return null;
  const k = kind ? KIND[kind] : null;
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-sm border border-border bg-surface px-3 py-2 text-sm">
      {k ? <span className="inline-flex items-center gap-1.5 font-mono text-[11px] tracking-wider text-brand"><k.icon className="size-3.5" aria-hidden />{k.label}</span>
        : <span className="font-mono text-[11px] tracking-wider text-text-3">TAKING PHOTOS…</span>}
      <span>{text ?? "Taking follow-up photos"}</span>
      {burstMs != null && <span className="font-mono text-xs text-text-3">{(burstMs / 1000).toFixed(1)} s of follow-up photos</span>}
      <SpeedChip kmh={speedKmh} />
    </p>
  );
}

/** Warning signs: for a person to check, never a verdict. */
export function BehaviorIndicators({ flags }: { flags: Flag[] }) {
  if (!flags.length) return null;
  const hits = flags.filter((f) => f.triggered);
  return (
    <div>
      <p className="caps border-b border-border pb-2">Warning signs</p>
      {hits.length > 0 && (
        <p role="note" className="mt-3 flex gap-2 border-l-2 border-sev-high bg-sev-high/10 p-3 text-sm">
          <AlertOctagon className="mt-0.5 size-4 shrink-0 text-sev-high" aria-hidden />
          <span><span className="font-semibold">{hits.length} warning sign{hits.length > 1 ? "s" : ""} appeared.</span> These are the AI’s best guesses from still pictures, not findings. Check the photos before acting.</span>
        </p>
      )}
      <ul className="divide-y divide-border">
        {flags.map((f) => (
          <li key={f.rule} className="grid grid-cols-[1fr_120px_auto] items-center gap-3 py-2.5 text-sm">
            <span className={cn(f.triggered ? "font-medium" : "text-text-2")}>{f.title}</span>
            {f.prob == null ? <span className="font-mono text-xs text-text-3">from the photos</span> : (
              <span className="flex items-center gap-2" title={`Sensitivity ${sensitivityText(f.threshold)} (shows from ${f.threshold.toFixed(2)})`}>
                <span className="relative h-1.5 flex-1 rounded-sm bg-surface-2"><span className={cn("absolute inset-y-0 left-0 rounded-sm", f.triggered ? "bg-sev-high" : "bg-border-strong")} style={{ width: `${Math.round(f.prob * 100)}%` }} />
                  <span className="absolute -top-0.5 h-2.5 w-px bg-text-3" style={{ left: `${f.threshold * 100}%` }} aria-hidden /></span>
                <span className="w-9 text-right font-mono text-xs">{f.prob.toFixed(2)}</span>
              </span>
            )}
            {f.triggered ? <SeverityPill severity={f.severity} /> : <span className="font-mono text-[11px] text-text-3">clear</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
