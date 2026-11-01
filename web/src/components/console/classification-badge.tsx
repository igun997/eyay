import { conf } from "@/lib/format";

/** label | confidence | source — classification always shows all three. */
export function ClassificationBadge({ label, confidence, source = "EDGE" }: { label: string; confidence: number; source?: "EDGE" | "CLASSIFIER" | "VLM" | string }) {
  return (
    <span className="inline-flex h-6 items-stretch overflow-hidden rounded-sm border border-border-strong font-mono text-[11px] tracking-wide">
      <span className="flex items-center px-2 font-semibold uppercase text-foreground">{label}</span>
      <span className="flex items-center border-l border-border-strong px-2 text-brand">{conf(confidence)}</span>
      <span className="flex items-center border-l border-border-strong px-2 text-text-3">{source}</span>
    </span>
  );
}
