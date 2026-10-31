import { cn } from "@/lib/utils";
import type { Severity } from "@/lib/types";

const tone: Record<Severity, string> = {
  CRITICAL: "text-sev-critical bg-sev-critical/12",
  HIGH: "text-sev-high bg-sev-high/12",
  MEDIUM: "text-sev-medium bg-sev-medium/12",
  LOW: "text-sev-low bg-sev-low/12",
};

/** Severity is never colour-only: dot + text label (design-system rule). */
export function SeverityPill({ severity, className }: { severity: Severity; className?: string }) {
  return (
    <span className={cn("inline-flex h-5 items-center gap-1.5 rounded-sm px-2 font-mono text-[11px] font-semibold tracking-wider", tone[severity], className)}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {severity}
    </span>
  );
}
