"use client";
import { useEffect, useMemo, useState } from "react";
import { Search } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/console/page-header";
import { FilterSelect } from "@/components/console/filter-select";
import { EventCard } from "@/components/console/event-card";
import { DetectionImage } from "@/components/console/detection-image";
import { ClassificationBadge } from "@/components/console/classification-badge";
import { SeverityPill } from "@/components/console/severity-pill";
import { PipelineStatusChip } from "@/components/console/status-chip";
import { ConfirmDialog } from "@/components/console/confirm-dialog";
import { EmptyState, ErrorState, LoadingList, OfflineBanner } from "@/components/console/states";
import { api, revalidate, useApi } from "@/hooks/use-api";
import { agreement, clsLabelKey, isSecondCheck } from "@/lib/classification";
import { tOr, type MessageKey } from "@/lib/i18n";
import { dismissReasonKey, dismissReasons } from "@/lib/schemas";
import type { EventRow, Frame, SourceRow } from "@/lib/types";
import { FrameStrip } from "@/components/console/frame-strip";
import { BehaviorIndicators, IndicationLine } from "@/components/console/indicators";
import { cn } from "@/lib/utils";
import { useFmt, useT } from "@/lib/i18n/client";

type Tab = "needs_review" | "in_pipeline" | "resolved";

export default function TriagePage() {
  const t = useT();
  const fmt = useFmt();
  const [tab, setTab] = useState<Tab>("needs_review");
  const [source, setSource] = useState("all");
  const [label, setLabel] = useState("all");
  const [severity, setSeverity] = useState("all");
  const [sort, setSort] = useState("newest");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const [dialog, setDialog] = useState<null | "confirm" | "dismiss">(null);
  const [note, setNote] = useState("");
  const [reason, setReason] = useState<string>(dismissReasons[0]);

  const params = new URLSearchParams({ tab, sort });
  if (source !== "all") params.set("source", source);
  if (label !== "all") params.set("label", label);
  if (severity !== "all") params.set("severity", severity);
  if (q) params.set("q", q);
  const { data, error, loading, refresh, offline } = useApi<{ events: EventRow[]; counts: { needs_review: number; in_pipeline: number; resolved: number } }>(`/api/events?${params}`, 4000);
  const { data: sources } = useApi<SourceRow[]>("/api/sources", 15000);

  const events = data?.events ?? [];
  const [frameIdx, setFrameIdx] = useState(0);
  const current = useMemo(() => events.find((e) => e.id === sel) ?? events[0] ?? null, [events, sel]);
  useEffect(() => { if (current && current.id !== sel) setSel(current.id); }, [current, sel]);
  const { data: frames } = useApi<Frame[]>(current ? `/api/events/${current.id}/frames` : null, current && !current.burst_done ? 2500 : 60000);
  useEffect(() => { setFrameIdx(0); }, [current?.id]);
  const frame = frames?.find((f) => f.idx === frameIdx) ?? frames?.[0];

  async function review(decision: "confirm" | "dismiss" | "reopen") {
    if (!current) return;
    try {
      const json = decision === "confirm" ? { decision, note: note || undefined } : decision === "dismiss" ? { decision, reason } : { decision };
      await api(`/api/events/${current.id}/review`, { method: "POST", json });
      toast.success(decision === "confirm" ? t("triage.toastConfirmed", { what: current.incident_code ?? t("triage.event") }) : decision === "dismiss" ? t("triage.eventDismissedAsAFalse") : t("triage.eventReopened"), {
        description: current.incident_code ? t("triage.toastIncidentUpdated", { code: current.incident_code }) : undefined });
      setNote(""); setReason(dismissReasons[0]);
      await revalidate("/api/");
    } catch (e) { toast.error((e as Error).message); throw e; }
  }

  return (
    <div className="flex h-screen flex-col">
      <PageHeader className="pb-3" title={t("triage.triage")} subtitle={sort === "newest" ? t("triage.newestFirst") : t("triage.oldestFirst")}
        actions={<div className="relative w-80"><Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-text-3" aria-hidden /><Input aria-label={t("triage.searchEvents")} value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("triage.searchEventsCamerasIds")} className="h-10 border-border-strong bg-surface pl-9" /></div>} />
      <div className="flex flex-wrap items-center gap-2 px-8 pb-4">
        <FilterSelect label={t("triage.camera")} value={source} onChange={setSource} options={[{ value: "all", label: t("triage.allCameras") }, ...(sources ?? []).map((s) => ({ value: s.id, label: s.code }))]} />
        <FilterSelect label={t("triage.kind")} value={label} onChange={setLabel} options={["all", "person", "car", "truck", "bus", "motorcycle", "bicycle"].map((v) => ({ value: v, label: v === "all" ? t("triage.all") : tOr(t, `label.${v}`, v) }))} />
        <FilterSelect label={t("triage.severity")} value={severity} onChange={setSeverity} options={["all", "CRITICAL", "HIGH", "MEDIUM", "LOW"].map((v) => ({ value: v, label: v === "all" ? t("triage.all") : t(`sev.${v}` as MessageKey) }))} />
        <div className="ml-auto"><FilterSelect label={t("triage.sort")} value={sort} onChange={setSort} options={[{ value: "newest", label: t("triage.newest") }, { value: "oldest", label: t("triage.oldest") }]} /></div>
      </div>
      <div role="tablist" className="flex gap-6 border-b border-border px-8">
        {([["needs_review", t("triage.tabNeeds"), data?.counts.needs_review], ["in_pipeline", t("triage.tabChecking"), data?.counts.in_pipeline], ["resolved", t("triage.tabResolved"), data?.counts.resolved]] as const).map(([k, l, n]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => { setTab(k); setSel(null); }}
            className={cn("-mb-px border-b-2 pb-3 text-sm", tab === k ? "border-brand font-medium" : "border-transparent text-text-2")}>
            {l} <span className={cn("ml-1 font-mono text-xs", tab === k ? "text-brand" : "text-text-3")}>{n ?? ""}</span>
          </button>
        ))}
      </div>
      {offline && <div className="pt-3"><OfflineBanner /></div>}
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(380px,520px)_1fr]">
        <div className="overflow-y-auto border-r border-border p-4">
          {loading ? <LoadingList /> : error && !data ? <ErrorState onRetry={() => refresh()} message={error.message} /> :
            events.length === 0 ? <EmptyState title={tab === "needs_review" ? t("triage.noEventsToReview") : tab === "in_pipeline" ? t("triage.nothingIsBeingChecked") : t("triage.nothingResolvedYet")}>{t("triage.newEventsAppearHereAs")}</EmptyState> :
              <div className="space-y-3">{events.map((e) => <EventCard key={e.id} event={e} selected={e.id === current?.id} onSelect={() => setSel(e.id)} />)}</div>}
        </div>
        <section className="overflow-y-auto px-8 py-6" aria-label={t("triage.eventDetail")}>
          {current ? (
            <div className="space-y-6">
              <div>
                <h2 className="text-2xl font-semibold tracking-tight">{current.title}</h2>
                <p className="mt-1 font-mono text-xs text-text-2">{current.source_code} · {current.source_name} · {fmt.time(current.detected_at)} · {current.code}</p>
                <div className="mt-3 flex items-center gap-2"><SeverityPill severity={current.severity} /><PipelineStatusChip status={current.status === "ready_for_triage" ? current.pipeline : current.status} /></div>
              </div>
              {current.snapshots > 0 && <DetectionImage src={`/api/events/${current.id}/snapshot/${frame?.idx ?? 0}`} bbox={frame ? frame.bbox : current.bbox} label={current.label} confidence={frame?.idx ? undefined : current.confidence} />}
              {frames && <FrameStrip frames={frames} active={frame?.idx ?? 0} onSelect={setFrameIdx} />}
              <IndicationLine kind={current.indication_kind} text={current.indication} burstMs={current.burst_ms} done={current.burst_done} speedKmh={current.speed_kmh} />
              <div>
                <p className="caps border-b border-border pb-2">{t("triage.whatTheAiSays")}</p>
                <div className="flex items-center justify-between border-b border-border py-3">
                  <div><p className="text-sm font-medium">{t("triage.cameraAi")}</p><p className="font-mono text-xs text-text-3">{t("triage.looksAtTheLivePicture")} {current.track_id ?? "–"}</p></div>
                  <ClassificationBadge label={current.cls_label === "other" ? t("triage.otherObject", { label: current.label }) : tOr(t, `label.${current.label}`, current.label)} confidence={current.confidence} source={t("source.camera")} />
                </div>
                {(isSecondCheck(current.cls_model) || ["queued", "classifying", "failed"].includes(current.pipeline)) && (
                  <div className="flex items-center justify-between border-b border-border py-3">
                    <div><p className="text-sm font-medium">{t("triage.secondAiCheck")}</p>
                      <p className="font-mono text-xs text-text-3">{isSecondCheck(current.cls_model) ? t("triage.secondCheckLooks", { time: current.cls_ms ? ` · ${fmt.num(current.cls_ms / 1000, 1)} ${t("unit.seconds")}` : "" }) : t("triage.looksAtTheSavedPhoto")}</p></div>
                    {isSecondCheck(current.cls_model) && current.cls_label && current.cls_confidence != null
                      ? <ClassificationBadge label={t(clsLabelKey(current.cls_label))} confidence={current.cls_confidence} source={t("source.second")} />
                      : <PipelineStatusChip status={current.pipeline} />}
                  </div>
                )}
                {current.pipeline === "failed" && current.cls_error && <p className="mt-2 font-mono text-xs text-sev-high">{t("triage.theSecondAiCheckCould")} {current.cls_error}</p>}
                {isSecondCheck(current.cls_model) && agreement(current.label, current.cls_label) === "disagree" && (
                  <p role="note" className="mt-3 border-l-2 border-sev-high bg-sev-high/10 p-3 text-sm">
                    <span className="font-semibold">{t("triage.theSecondAiCheckDisagrees")}</span> {t("triage.theCameraAiSaid")} {current.label}{t("triage.theSecondCheckSays")} {t(clsLabelKey(current.cls_label!))}{t("triage.checkThePhotoBeforeConfirming")}
                  </p>
                )}
              </div>
              <BehaviorIndicators flags={current.cls_flags ?? []} />
              <div>
                <p className="caps border-b border-border pb-2">{t("triage.context")}</p>
                <dl className="grid grid-cols-[100px_1fr_100px_1fr] gap-y-2 py-3 text-sm">
                  <dt className="font-mono text-xs text-text-3">{t("triage.camera")}</dt><dd>{current.source_code} · {current.source_name}</dd>
                  <dt className="font-mono text-xs text-text-3">{t("triage.detected")}</dt><dd>{fmt.dateTime(current.detected_at)}</dd>
                  <dt className="font-mono text-xs text-text-3">{t("triage.incident")}</dt><dd className="font-mono">{current.incident_code ?? t("triage.noneCategoryDoesNotOpen")}</dd>
                  {current.dismiss_reason && <><dt className="font-mono text-xs text-text-3">{t("triage.dismissed")}</dt><dd>{tOr(t, dismissReasonKey[current.dismiss_reason as keyof typeof dismissReasonKey] ?? "", current.dismiss_reason)}</dd></>}
                  {current.note && <><dt className="font-mono text-xs text-text-3">{t("triage.note")}</dt><dd>{current.note}</dd></>}
                </dl>
              </div>
              <div className="flex items-center gap-3 border-t border-border pt-5">
                {current.status === "ready_for_triage" ? (<>
                  <Button onClick={() => setDialog("confirm")}>{t("triage.confirmIncident")}</Button>
                  <Button variant="outline" onClick={() => setDialog("dismiss")}>{t("triage.dismissAsFalseAlarm")}</Button>
                </>) : <Button variant="outline" onClick={() => review("reopen")}>{t("triage.reopenForReview")}</Button>}
              </div>
            </div>
          ) : <EmptyState title={t("triage.selectAnEvent")}>{t("triage.pickAnEventFromThe")}</EmptyState>}
        </section>
      </div>

      <ConfirmDialog open={dialog === "confirm"} onOpenChange={(o) => !o && setDialog(null)} title={t("triage.confirmIncident2")} confirmLabel={t("triage.confirmIncident")} onConfirm={() => review("confirm")}
        description={current?.incident_code ? t("triage.confirmsOn", { title: current.title, code: current.incident_code }) : t("triage.confirms", { title: current?.title ?? "" })}>
        <Textarea aria-label={t("triage.note")} placeholder={t("triage.addANoteOptional")} value={note} onChange={(e) => setNote(e.target.value)} className="border-border-strong bg-background" />
      </ConfirmDialog>
      <ConfirmDialog open={dialog === "dismiss"} onOpenChange={(o) => !o && setDialog(null)} title={t("triage.dismissAsFalseAlarm2")} confirmLabel={t("triage.dismissEvent")} variant="outline" onConfirm={() => review("dismiss")}
        description={t("triage.pickAReasonItHelps")}>
        <RadioGroup value={reason} onValueChange={setReason} className="gap-0">
          {dismissReasons.map((r) => (
            <Label key={r} htmlFor={`r-${r}`} className="flex cursor-pointer items-center gap-3 border-b border-border py-2.5 font-normal"><RadioGroupItem id={`r-${r}`} value={r} />{t(dismissReasonKey[r])}</Label>
          ))}
        </RadioGroup>
      </ConfirmDialog>
    </div>
  );
}
