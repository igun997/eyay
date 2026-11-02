"use client";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n/client";
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
export function StatusChip({ children, tone = "neutral", icon: Icon, dot, className, title }: {
  children: React.ReactNode; tone?: Tone; icon?: LucideIcon; dot?: boolean; className?: string; title?: string;
}) {
  return (
    <span title={title} className={cn("inline-flex h-5 items-center gap-1.5 rounded-sm border px-2 font-mono text-[11px] uppercase tracking-wider whitespace-nowrap", tones[tone], className)}>
      {dot && <span className="size-1.5 rounded-full bg-current" aria-hidden />}
      {Icon && <Icon className="size-3" aria-hidden />}
      {children}
    </span>
  );
}

const incident = { open: "accent", acknowledged: "neutral", resolved: "ok" } as const;
export const IncidentStatusChip = ({ status }: { status: keyof typeof incident }) => {
  const t = useT();
  return <StatusChip tone={incident[status]} dot={status === "open"}>{t(`incidentStatus.${status}`)}</StatusChip>;
};

export type PipelineKey = keyof typeof pipeline;
const pipeline = {
  received: [Inbox, "neutral"], queued: [Hourglass, "neutral"], classifying: [Loader2, "neutral"], ready_for_triage: [CheckCircle2, "accent"], ready: [CheckCircle2, "accent"],
  confirmed: [CheckCircle2, "ok"], dismissed: [CheckCircle2, "neutral"], failed: [AlertTriangle, "warn"], skipped: [MinusCircle, "neutral"],
} as const;
export function PipelineStatusChip({ status }: { status: PipelineKey }) {
  const t = useT();
  const [Icon, tone] = pipeline[status];
  return <StatusChip tone={tone} icon={Icon} className={status === "classifying" ? "[&_svg]:animate-spin" : undefined}>{t(`pipeline.${status}`)}</StatusChip>;
}

const health = { online: "ok", connecting: "warn", pending: "warn", offline: "danger", error: "danger" } as const;
export const HealthChip = ({ status }: { status: keyof typeof health }) => {
  const t = useT();
  return <StatusChip tone={health[status]} dot className="border-transparent bg-transparent">{t(`health.${status}`)}</StatusChip>;
};
