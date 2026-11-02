"use client";
import Link from "next/link";
import { use, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { PageHeader, Panel } from "@/components/console/page-header";
import { SeverityPill } from "@/components/console/severity-pill";
import { IncidentStatusChip } from "@/components/console/status-chip";
import { Timeline } from "@/components/console/timeline";
import { ClassificationBadge } from "@/components/console/classification-badge";
import { DetectionImage } from "@/components/console/detection-image";
import { ErrorState, LoadingList } from "@/components/console/states";
import { api, revalidate, useApi } from "@/hooks/use-api";
import type { EventRow, Frame, IncidentRow, TimelineRow } from "@/lib/types";
import { IndicationLine } from "@/components/console/indicators";
import { tOr } from "@/lib/i18n";
import { useFmt, useT } from "@/lib/i18n/client";

type Detail = { incident: IncidentRow; timeline: TimelineRow[]; events: EventRow[]; frames: Frame[]; assignees: { id: string; name: string; role: string }[] };

export default function IncidentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const t = useT();
  const fmt = useFmt();
  const { id } = use(params);
  const { data, error, loading, refresh } = useApi<Detail>(`/api/incidents/${id}`, 4000);
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState<{ eventId: string; idx: number } | null>(null);
  if (loading) return <LoadingList />;
  if (!data) return <ErrorState title={t("incident.incidentNotFound")} message={error?.message} onRetry={() => refresh()} />;
  const { incident: i, timeline, events, frames, assignees } = data;
  const evById = new Map(events.map((e) => [e.id, e]));
  const firstFrame = frames[0];
  const pick = preview ?? (firstFrame ? { eventId: firstFrame.event_id, idx: firstFrame.idx } : null);
  const shown = pick ? events.find((e) => e.id === pick.eventId) ?? null : null;
  const shownFrame = pick ? frames.find((f) => f.event_id === pick.eventId && f.idx === pick.idx) : undefined;

  const act = async (json: object, ok: string) => {
    try { await api(`/api/incidents/${i.id}/action`, { method: "POST", json }); toast.success(ok); await revalidate("/api/"); } catch (e) { toast.error((e as Error).message); }
  };
  const addNote = async () => {
    if (!note.trim()) return;
    try { await api(`/api/incidents/${i.id}/notes`, { method: "POST", json: { text: note } }); setNote(""); await revalidate("/api/incidents"); } catch (e) { toast.error((e as Error).message); }
  };

  return (
    <>
      <div className="px-8 pt-6"><Link href="/incidents" className="inline-flex items-center gap-2 text-sm text-text-2 hover:text-foreground"><ArrowLeft className="size-4" />{t("incident.incidents")} <span className="text-text-3">/</span> <span className="font-mono">{i.code}</span></Link></div>
      <PageHeader className="pt-3" title={i.title}
        subtitle={<span className="flex flex-wrap items-center gap-3"><SeverityPill severity={i.severity} /><IncidentStatusChip status={i.status} /><span>{t("incident.opened")} {fmt.time(i.opened_at)} · {i.source_code} {i.source_name} · {i.assignee_name ? t("incident.assignedTo", { name: i.assignee_name }) : t("incident.unassigned")} · {i.events} {t("incident.eventsGrouped")}</span></span>}
        actions={<>
          <DropdownMenu>
            <DropdownMenuTrigger asChild><Button variant="ghost">{t("incident.assign")}</Button></DropdownMenuTrigger>
            <DropdownMenuContent className="border-border-strong bg-surface-2">
              {assignees.map((a) => <DropdownMenuItem key={a.id} onSelect={() => act({ action: "assign", userId: a.id }, t("incident.toastAssigned", { name: a.name }))}>{a.name} <span className="ml-2 font-mono text-[11px] text-text-3">{t(a.role === "admin" ? "users.admin" : "users.security")}</span></DropdownMenuItem>)}
              <DropdownMenuItem onSelect={() => act({ action: "assign", userId: null }, t("incident.toastUnassigned"))}>{t("incident.unassign")}</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          {i.status === "open" && <Button variant="outline" onClick={() => act({ action: "acknowledge" }, t("incident.toastAcknowledged"))}>{t("incident.acknowledge")}</Button>}
          {i.status === "resolved" ? <Button variant="outline" onClick={() => act({ action: "reopen" }, t("incident.toastReopened"))}>{t("incident.reopen")}</Button> : <Button onClick={() => act({ action: "resolve" }, t("incident.toastResolved"))}>{t("incident.resolveIncident")}</Button>}
        </>} />
      <div className="grid border-t border-border xl:grid-cols-[1fr_380px]">
        <div className="px-8 py-6">
          <div className="mb-5 flex items-baseline justify-between"><h2 className="caps">{t("incident.timeline")}</h2><span className="font-mono text-xs text-text-3">{timeline.length} {t("incident.entries")}</span></div>
          <Timeline rows={timeline} renderExtra={(r) => {
            const e = r.event_id ? evById.get(r.event_id) : null;
            if (!e || r.kind !== "camera" || e.snapshots === 0) return null;
            return (
              <button type="button" onClick={() => setPreview({ eventId: e.id, idx: 0 })} className="mt-3 flex items-center gap-3 text-left">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={`/api/events/${e.id}/snapshot/0`} alt="" className="h-16 w-24 rounded-sm border border-border-strong object-cover" />
                <ClassificationBadge label={e.label} confidence={e.confidence} source={t("source.camera")} />
              </button>
            );
          }} />
          <div className="mt-2 flex gap-3">
            <Input aria-label={t("incident.addANote")} value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addNote()} placeholder={t("incident.addANoteToThe")} className="h-10 border-border-strong bg-surface" />
            <Button variant="outline" onClick={addNote}>{t("incident.addNote")}</Button>
          </div>
        </div>
        <aside className="space-y-5 border-l border-border bg-surface/40 px-6 py-6">
          <Panel title={t("incident.details")} bodyClassName="p-5">
            <dl className="grid grid-cols-[90px_1fr] gap-y-2 text-sm">
              {[[t("incident.rowIncident"), i.code], [t("incident.rowCamera"), `${i.source_code ?? "–"} · ${i.source_name ?? ""}`], [t("incident.rowCategory"), i.label ? tOr(t, `label.${i.label}`, i.label) : "–"], [t("incident.rowEvents"), t("incident.eventsN", { n: i.events })]].map(([k, v]) => (<><dt key={k} className="font-mono text-xs text-text-3">{k}</dt><dd key={k + "v"}>{v}</dd></>))}
            </dl>
          </Panel>
          <Panel title={t("incident.evidence")} bodyClassName="space-y-3 p-5">
            {shown && pick ? <DetectionImage src={`/api/events/${shown.id}/snapshot/${pick.idx}`} bbox={shownFrame?.bbox ?? null} label={shown.label} confidence={pick.idx === 0 ? shown.confidence : undefined} /> : <p className="text-sm text-text-2">{t("incident.noSnapshots")}</p>}
            {shown && <IndicationLine kind={shown.indication_kind} text={shown.indication} burstMs={shown.burst_ms} done={shown.burst_done} speedKmh={shown.speed_kmh} />}
            <div className="grid grid-cols-4 gap-2">
              {frames.slice(0, 12).map((f) => (
                <button key={`${f.event_id}-${f.idx}`} onClick={() => setPreview({ eventId: f.event_id, idx: f.idx })} aria-label={t("incident.showPhoto", { n: f.idx + 1, code: events.find((e) => e.id === f.event_id)?.code ?? "" })}
                  className={`overflow-hidden rounded-sm border ${pick?.eventId === f.event_id && pick.idx === f.idx ? "border-brand" : "border-border"}`}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={`/api/events/${f.event_id}/snapshot/${f.idx}`} alt="" className="aspect-video w-full object-cover" loading="lazy" />
                </button>
              ))}
            </div>
          </Panel>
          <p className="font-mono text-[11px] text-text-3">{t("incident.alertsOnlyEyeaiSendsJust")}</p>
        </aside>
      </div>
    </>
  );
}
