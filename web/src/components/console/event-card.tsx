"use client";
import { cn } from "@/lib/utils";
import type { EventRow } from "@/lib/types";
import { SeverityPill } from "./severity-pill";
import { ClassificationBadge } from "./classification-badge";
import { PipelineStatusChip, StatusChip } from "./status-chip";
import { clsLabelKey, isSecondCheck } from "@/lib/classification";
import { tOr } from "@/lib/i18n";
import { SpeedChip } from "./indicators";
import { useFmt, useT } from "@/lib/i18n/client";

export function EventCard({ event, selected, onSelect }: { event: EventRow; selected?: boolean; onSelect?: () => void }) {
  const t = useT();
  const fmt = useFmt();
  return (
    <button type="button" onClick={onSelect} aria-pressed={selected}
      className={cn("flex w-full gap-4 rounded-sm border bg-surface p-3 text-left transition-colors hover:bg-surface-2",
        selected ? "border-brand bg-surface-2" : "border-border")}>
      <div className="relative h-[84px] w-[120px] shrink-0 overflow-hidden rounded-sm bg-black">
        {event.snapshots > 0 && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={`/api/events/${event.id}/snapshot/0`} alt="" className="size-full object-cover" loading="lazy" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <p className="truncate text-[15px] font-medium">{event.title}</p>
          <span className="shrink-0 font-mono text-xs text-text-2">{fmt.ago(event.detected_at)}</span>
        </div>
        <p className="mt-1 font-mono text-xs text-text-2">{event.source_code} · {event.source_name} · {fmt.time(event.detected_at)}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <SeverityPill severity={event.severity} />
          <ClassificationBadge label={event.cls_label === "other" ? t("triage.otherObject", { label: event.label }) : tOr(t, `label.${event.label}`, event.label)} confidence={event.confidence} source={t("source.camera")} />
          {isSecondCheck(event.cls_model) && event.cls_label && event.cls_confidence != null && <ClassificationBadge label={t(clsLabelKey(event.cls_label))} confidence={event.cls_confidence} source={t("source.second")} />}
          {event.suspicious && <StatusChip tone="warn" dot title={event.cls_flags.filter((f) => f.triggered).map((f) => f.title).join(", ")}>{t("eventCard.warningSign")} · {event.cls_flags.filter((f) => f.triggered).length}</StatusChip>}
          <SpeedChip kmh={event.speed_kmh} />
          {isSecondCheck(event.cls_model) === false && (event.pipeline === "queued" || event.pipeline === "classifying") && <PipelineStatusChip status={event.pipeline} />}
        </div>
      </div>
    </button>
  );
}
