import { cn } from "@/lib/utils";
import { AlertTriangle, CheckCircle2, Hourglass, Inbox, Loader2, MinusCircle, type LucideIcon } from "lucide-react";

type Tone = "neutral" | "accent" | "ok" | "warn" | "danger";
const tones: Record<Tone, string> = {
  neutral: "border-border-strong text-text-2",
  accent: "border-brand text-brand",
  ok: "border-ok/40 text-ok bg-ok/10",
  warn: "border-sev-high/40 text-sev-high bg-sev-high/10",
  danger: "border-sev-critical/40 text-sev-critical bg-sev-critical/10",
};

/** Outlined mono chip. Used for pipeline status, incident status, health. */
export function StatusChip({ children, tone = "neutral", icon: Icon, dot, className }: {
  children: React.ReactNode; tone?: Tone; icon?: LucideIcon; dot?: boolean; className?: string;
}) {
  return (
    <span className={cn("inline-flex h-5 items-center gap-1.5 rounded-sm border px-2 font-mono text-[11px] uppercase tracking-wider whitespace-nowrap", tones[tone], className)}>
      {dot && <span className="size-1.5 rounded-full bg-current" aria-hidden />}
      {Icon && <Icon className="size-3" aria-hidden />}
      {children}
    </span>
  );
}

const incident = { open: ["accent", "OPEN"], acknowledged: ["neutral", "ACKNOWLEDGED"], resolved: ["ok", "RESOLVED"] } as const;
export const IncidentStatusChip = ({ status }: { status: keyof typeof incident }) => (
  <StatusChip tone={incident[status][0]} dot={status === "open"}>{incident[status][1]}</StatusChip>
);

export type PipelineKey = keyof typeof pipeline;
const pipeline = {
  received: [Inbox, "RECEIVED", "neutral"], queued: [Hourglass, "QUEUED", "neutral"],
  classifying: [Loader2, "CLASSIFYING", "neutral"], ready_for_triage: [CheckCircle2, "READY FOR TRIAGE", "accent"], ready: [CheckCircle2, "READY FOR TRIAGE", "accent"],
  confirmed: [CheckCircle2, "CONFIRMED", "ok"], dismissed: [CheckCircle2, "DISMISSED", "neutral"],
  failed: [AlertTriangle, "CLASSIFIER FAILED", "warn"], skipped: [MinusCircle, "NOT CLASSIFIED", "neutral"],
} as const;
export function PipelineStatusChip({ status }: { status: PipelineKey }) {
  const [Icon, label, tone] = pipeline[status];
  return <StatusChip tone={tone} icon={Icon} className={status === "classifying" ? "[&_svg]:animate-spin" : undefined}>{label}</StatusChip>;
}

const health = { online: ["ok", "ONLINE"], connecting: ["warn", "CONNECTING"], pending: ["warn", "PENDING"], offline: ["danger", "OFFLINE"], error: ["danger", "ERROR"] } as const;
export const HealthChip = ({ status }: { status: keyof typeof health }) => (
  <StatusChip tone={health[status][0]} dot className="border-transparent bg-transparent">{health[status][1]}</StatusChip>
);
