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
import { time } from "@/lib/format";
import type { EventRow, IncidentRow, TimelineRow } from "@/lib/types";

type Detail = { incident: IncidentRow; timeline: TimelineRow[]; events: EventRow[]; assignees: { id: string; name: string; role: string }[] };

export default function IncidentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, error, loading, refresh } = useApi<Detail>(`/api/incidents/${id}`, 4000);
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState<EventRow | null>(null);
  if (loading) return <LoadingList />;
  if (!data) return <ErrorState title="Incident not found" message={error?.message} onRetry={() => refresh()} />;
  const { incident: i, timeline, events, assignees } = data;
  const evById = new Map(events.map((e) => [e.id, e]));
  const shown = preview ?? events[0] ?? null;

  const act = async (json: object, ok: string) => {
    try { await api(`/api/incidents/${i.id}/action`, { method: "POST", json }); toast.success(ok); await revalidate("/api/"); } catch (e) { toast.error((e as Error).message); }
  };
  const addNote = async () => {
    if (!note.trim()) return;
    try { await api(`/api/incidents/${i.id}/notes`, { method: "POST", json: { text: note } }); setNote(""); await revalidate("/api/incidents"); } catch (e) { toast.error((e as Error).message); }
  };

  return (
    <>
      <div className="px-8 pt-6"><Link href="/incidents" className="inline-flex items-center gap-2 text-sm text-text-2 hover:text-foreground"><ArrowLeft className="size-4" />Incidents <span className="text-text-3">/</span> <span className="font-mono">{i.code}</span></Link></div>
      <PageHeader className="pt-3" title={i.title}
        subtitle={<span className="flex flex-wrap items-center gap-3"><SeverityPill severity={i.severity} /><IncidentStatusChip status={i.status} /><span>Opened {time(i.opened_at)} · {i.source_code} {i.source_name} · {i.assignee_name ? `Assigned to ${i.assignee_name}` : "Unassigned"} · {i.events} events grouped</span></span>}
        actions={<>
          <DropdownMenu>
            <DropdownMenuTrigger asChild><Button variant="ghost">Assign</Button></DropdownMenuTrigger>
            <DropdownMenuContent className="border-border-strong bg-surface-2">
              {assignees.map((a) => <DropdownMenuItem key={a.id} onSelect={() => act({ action: "assign", userId: a.id }, `Assigned to ${a.name}`)}>{a.name} <span className="ml-2 font-mono text-[11px] text-text-3">{a.role}</span></DropdownMenuItem>)}
              <DropdownMenuItem onSelect={() => act({ action: "assign", userId: null }, "Unassigned")}>Unassign</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          {i.status === "open" && <Button variant="outline" onClick={() => act({ action: "acknowledge" }, "Acknowledged")}>Acknowledge</Button>}
          {i.status === "resolved" ? <Button variant="outline" onClick={() => act({ action: "reopen" }, "Reopened")}>Reopen</Button> : <Button onClick={() => act({ action: "resolve" }, "Incident resolved")}>Resolve incident</Button>}
        </>} />
      <div className="grid border-t border-border xl:grid-cols-[1fr_380px]">
        <div className="px-8 py-6">
          <div className="mb-5 flex items-baseline justify-between"><h2 className="caps">Timeline</h2><span className="font-mono text-xs text-text-3">{timeline.length} entries</span></div>
          <Timeline rows={timeline} renderExtra={(r) => {
            const e = r.event_id ? evById.get(r.event_id) : null;
            if (!e || r.kind !== "camera" || e.snapshots === 0) return null;
            return (
              <button type="button" onClick={() => setPreview(e)} className="mt-3 flex items-center gap-3 text-left">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={`/api/events/${e.id}/snapshot/0`} alt="" className="h-16 w-24 rounded-sm border border-border-strong object-cover" />
                <ClassificationBadge label={e.label} confidence={e.confidence} source="EDGE" />
              </button>
            );
          }} />
          <div className="mt-2 flex gap-3">
            <Input aria-label="Add a note" value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addNote()} placeholder="Add a note to the timeline" className="h-10 border-border-strong bg-surface" />
            <Button variant="outline" onClick={addNote}>Add note</Button>
          </div>
        </div>
        <aside className="space-y-5 border-l border-border bg-surface/40 px-6 py-6">
          <Panel title="Details" bodyClassName="p-5">
            <dl className="grid grid-cols-[90px_1fr] gap-y-2 text-sm">
              {[["Incident", i.code], ["Camera", `${i.source_code ?? "–"} · ${i.source_name ?? ""}`], ["Category", i.label ?? "–"], ["Events", `${i.events} grouped`]].map(([k, v]) => (<><dt key={k} className="font-mono text-xs text-text-3">{k}</dt><dd key={k + "v"}>{v}</dd></>))}
            </dl>
          </Panel>
          <Panel title="Evidence" bodyClassName="space-y-3 p-5">
            {shown?.snapshots ? <DetectionImage src={`/api/events/${shown.id}/snapshot/0`} bbox={shown.bbox} label={shown.label} confidence={shown.confidence} /> : <p className="text-sm text-text-2">No snapshots.</p>}
            <div className="grid grid-cols-3 gap-2">
              {events.filter((e) => e.snapshots).slice(0, 6).map((e) => (
                <button key={e.id} onClick={() => setPreview(e)} aria-label={`Show ${e.code}`} className={`overflow-hidden rounded-sm border ${shown?.id === e.id ? "border-brand" : "border-border"}`}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={`/api/events/${e.id}/snapshot/0`} alt="" className="aspect-video w-full object-cover" />
                </button>
              ))}
            </div>
          </Panel>
          <p className="font-mono text-[11px] text-text-3">Read-only. EyeAI cannot open doors or control machines.</p>
        </aside>
      </div>
    </>
  );
}
