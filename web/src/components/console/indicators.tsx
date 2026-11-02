"use client";
import { AlertOctagon, Eye, Gauge, MoveRight, Users, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Flag, IndicationKind } from "@/lib/types";
import { SeverityPill } from "./severity-pill";
import { sensitivityKey } from "@/lib/plain";
import { useFmt, useT } from "@/lib/i18n/client";
import { K, type MessageKey } from "@/lib/i18n";

const KIND: Record<IndicationKind, { icon: LucideIcon; label: MessageKey }> = {
  appeared: { icon: Eye, label: K("indicators.appeared") }, moving: { icon: MoveRight, label: K("indicators.moving") },
  lingering: { icon: Eye, label: K("indicators.lingering") }, crowd: { icon: Users, label: K("indicators.crowd") },
};

/** Estimated speed (km/h) of the followed object; an estimate for review, scaled by the real distance measured in the camera's speed setup. */
export function SpeedChip({ kmh, className }: { kmh: number | null | undefined; className?: string }) {
  const t = useT();
  const fmt = useFmt();
  if (kmh == null) return null;
  return (
    <span title={t("indicators.estimatedSpeedForReview")} className={cn("inline-flex h-5 items-center gap-1.5 rounded-sm border border-border-strong px-2 font-mono text-[11px] tracking-wider whitespace-nowrap text-text-2", className)}>
      <Gauge className="size-3" aria-hidden />{fmt.num(kmh, 0)} {t("unit.kmh")}
    </span>
  );
}

/** The activity pattern the follow-up photos show (how the object moved). */
export function IndicationLine({ kind, text, burstMs, done, speedKmh }: { kind: IndicationKind | null; text: string | null; burstMs: number | null; done: boolean; speedKmh?: number | null }) {
  const t = useT();
  const fmt = useFmt();
  if (!kind && done && speedKmh == null) return null;
  const k = kind ? KIND[kind] : null;
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-sm border border-border bg-surface px-3 py-2 text-sm">
      {k ? <span className="inline-flex items-center gap-1.5 font-mono text-[11px] tracking-wider text-brand"><k.icon className="size-3.5" aria-hidden />{t(k.label)}</span>
        : <span className="font-mono text-[11px] tracking-wider text-text-3">{t("indicators.takingPhotos")}</span>}
      <span>{text ?? t("indicators.takingFollowUpPhotos")}</span>
      {burstMs != null && <span className="font-mono text-xs text-text-3">{fmt.num(burstMs / 1000, 1)} {t("indicators.sOfFollowUpPhotos")}</span>}
      <SpeedChip kmh={speedKmh} />
    </p>
  );
}

/** Warning signs: for a person to check, never a verdict. */
export function BehaviorIndicators({ flags }: { flags: Flag[] }) {
  const t = useT();
  const fmt = useFmt();
  if (!flags.length) return null;
  const hits = flags.filter((f) => f.triggered);
  return (
    <div>
      <p className="caps border-b border-border pb-2">{t("indicators.warningSigns")}</p>
      {hits.length > 0 && (
        <p role="note" className="mt-3 flex gap-2 border-l-2 border-sev-high bg-sev-high/10 p-3 text-sm">
          <AlertOctagon className="mt-0.5 size-4 shrink-0 text-sev-high" aria-hidden />
          <span><span className="font-semibold">{hits.length} {t("indicators.warningSign")}{hits.length > 1 ? "s" : ""} {t("indicators.appeared2")}</span> {t("indicators.theseAreTheAiS")}</span>
        </p>
      )}
      <ul className="divide-y divide-border">
        {flags.map((f) => (
          <li key={f.rule} className="grid grid-cols-[1fr_120px_auto] items-center gap-3 py-2.5 text-sm">
            <span className={cn(f.triggered ? "font-medium" : "text-text-2")}>{f.title}</span>
            {f.prob == null ? <span className="font-mono text-xs text-text-3">{t("indicators.fromThePhotos")}</span> : (
              <span className="flex items-center gap-2" title={t("indicators.sensitivityTip", { level: t(sensitivityKey(f.threshold)), n: fmt.num(f.threshold) })}>
                <span className="relative h-1.5 flex-1 rounded-sm bg-surface-2"><span className={cn("absolute inset-y-0 left-0 rounded-sm", f.triggered ? "bg-sev-high" : "bg-border-strong")} style={{ width: `${Math.round(f.prob * 100)}%` }} />
                  <span className="absolute -top-0.5 h-2.5 w-px bg-text-3" style={{ left: `${f.threshold * 100}%` }} aria-hidden /></span>
                <span className="w-9 text-right font-mono text-xs">{fmt.num(f.prob)}</span>
              </span>
            )}
            {f.triggered ? <SeverityPill severity={f.severity} /> : <span className="font-mono text-[11px] text-text-3">{t("indicators.clear")}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
