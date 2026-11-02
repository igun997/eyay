"use client";

/** label | certainty | source: a classification always shows all three. */
import { useFmt } from "@/lib/i18n/client";

export function ClassificationBadge({ label, confidence, source = "CAMERA AI" }: { label: string; confidence: number; source?: "CAMERA AI" | "SECOND AI CHECK" | string }) {
  const fmt = useFmt();
  return (
    <span className="inline-flex h-6 items-stretch overflow-hidden rounded-sm border border-border-strong font-mono text-[11px] tracking-wide">
      <span className="flex items-center px-2 font-semibold uppercase text-foreground">{label}</span>
      <span className="flex items-center border-l border-border-strong px-2 text-brand">{fmt.num(confidence)}</span>
      <span className="flex items-center border-l border-border-strong px-2 text-text-3">{source}</span>
    </span>
  );
}
