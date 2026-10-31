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
import { dateTime, time } from "@/lib/format";
import { dismissReasons } from "@/lib/schemas";
import type { EventRow, SourceRow } from "@/lib/types";
import { cn } from "@/lib/utils";

type Tab = "needs_review" | "resolved";

export default function TriagePage() {
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
  const { data, error, loading, refresh, offline } = useApi<{ events: EventRow[]; counts: { needs_review: number; resolved: number } }>(`/api/events?${params}`, 4000);
  const { data: sources } = useApi<SourceRow[]>("/api/sources", 15000);

  const events = data?.events ?? [];
  const current = useMemo(() => events.find((e) => e.id === sel) ?? events[0] ?? null, [events, sel]);
  useEffect(() => { if (current && current.id !== sel) setSel(current.id); }, [current, sel]);

  async function review(decision: "confirm" | "dismiss" | "reopen") {
    if (!current) return;
    try {
      const json = decision === "confirm" ? { decision, note: note || undefined } : decision === "dismiss" ? { decision, reason } : { decision };
      await api(`/api/events/${current.id}/review`, { method: "POST", json });
      toast.success(decision === "confirm" ? `${current.incident_code ?? "Event"} confirmed` : decision === "dismiss" ? "Event dismissed as false alarm" : "Event reopened", {
        description: current.incident_code ? `Incident ${current.incident_code} updated.` : undefined });
      setNote(""); setReason(dismissReasons[0]);
      await revalidate("/api/");
    } catch (e) { toast.error((e as Error).message); throw e; }
  }

  return (
    <div className="flex h-screen flex-col">
      <PageHeader className="pb-3" title="Triage" subtitle={sort === "newest" ? "Newest first" : "Oldest first"}
        actions={<div className="relative w-80"><Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-text-3" aria-hidden /><Input aria-label="Search events" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search events, cameras, IDs" className="h-10 border-border-strong bg-surface pl-9" /></div>} />
      <div className="flex flex-wrap items-center gap-2 px-8 pb-4">
        <FilterSelect label="Camera" value={source} onChange={setSource} options={[{ value: "all", label: "All cameras" }, ...(sources ?? []).map((s) => ({ value: s.id, label: s.code }))]} />
        <FilterSelect label="Class" value={label} onChange={setLabel} options={["all", "person", "car", "truck", "bus", "motorcycle", "bicycle"].map((v) => ({ value: v, label: v === "all" ? "All" : v }))} />
        <FilterSelect label="Severity" value={severity} onChange={setSeverity} options={["all", "CRITICAL", "HIGH", "MEDIUM", "LOW"].map((v) => ({ value: v, label: v === "all" ? "All" : v }))} />
        <div className="ml-auto"><FilterSelect label="Sort" value={sort} onChange={setSort} options={[{ value: "newest", label: "Newest" }, { value: "oldest", label: "Oldest" }]} /></div>
      </div>
      <div role="tablist" className="flex gap-6 border-b border-border px-8">
        {([["needs_review", "Needs review", data?.counts.needs_review], ["resolved", "Resolved", data?.counts.resolved]] as const).map(([k, l, n]) => (
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
            events.length === 0 ? <EmptyState title={tab === "needs_review" ? "No events to review" : "Nothing resolved yet"}>New events appear here as soon as a live stream detects something in a category.</EmptyState> :
              <div className="space-y-3">{events.map((e) => <EventCard key={e.id} event={e} selected={e.id === current?.id} onSelect={() => setSel(e.id)} />)}</div>}
        </div>
        <section className="overflow-y-auto px-8 py-6" aria-label="Event detail">
          {current ? (
            <div className="space-y-6">
              <div>
                <h2 className="text-2xl font-semibold tracking-tight">{current.title}</h2>
                <p className="mt-1 font-mono text-xs text-text-2">{current.source_code} · {current.source_name} · {time(current.detected_at)} · {current.code}</p>
                <div className="mt-3 flex items-center gap-2"><SeverityPill severity={current.severity} /><PipelineStatusChip status={current.status} /></div>
              </div>
              {current.snapshots > 0 && <DetectionImage src={`/api/events/${current.id}/snapshot/0`} bbox={current.bbox} label={current.label} confidence={current.confidence} />}
              <div>
                <p className="caps border-b border-border pb-2">Classification</p>
                <div className="flex items-center justify-between border-b border-border py-3">
                  <div><p className="text-sm font-medium">Edge worker</p><p className="font-mono text-xs text-text-3">YOLOX · live stream · track {current.track_id ?? "–"}</p></div>
                  <ClassificationBadge label={current.label} confidence={current.confidence} source="EDGE" />
                </div>
              </div>
              <div>
                <p className="caps border-b border-border pb-2">Context</p>
                <dl className="grid grid-cols-[100px_1fr_100px_1fr] gap-y-2 py-3 text-sm">
                  <dt className="font-mono text-xs text-text-3">Camera</dt><dd>{current.source_code} · {current.source_name}</dd>
                  <dt className="font-mono text-xs text-text-3">Detected</dt><dd>{dateTime(current.detected_at)}</dd>
                  <dt className="font-mono text-xs text-text-3">Incident</dt><dd className="font-mono">{current.incident_code ?? "None (category does not open incidents)"}</dd>
                  {current.dismiss_reason && <><dt className="font-mono text-xs text-text-3">Dismissed</dt><dd>{current.dismiss_reason}</dd></>}
                  {current.note && <><dt className="font-mono text-xs text-text-3">Note</dt><dd>{current.note}</dd></>}
                </dl>
              </div>
              <div className="flex items-center gap-3 border-t border-border pt-5">
                {current.status === "ready_for_triage" ? (<>
                  <Button onClick={() => setDialog("confirm")}>Confirm incident</Button>
                  <Button variant="outline" onClick={() => setDialog("dismiss")}>Dismiss as false alarm</Button>
                </>) : <Button variant="outline" onClick={() => review("reopen")}>Reopen for review</Button>}
              </div>
            </div>
          ) : <EmptyState title="Select an event">Pick an event from the list to see its evidence.</EmptyState>}
        </section>
      </div>

      <ConfirmDialog open={dialog === "confirm"} onOpenChange={(o) => !o && setDialog(null)} title="Confirm incident?" confirmLabel="Confirm incident" onConfirm={() => review("confirm")}
        description={current?.incident_code ? `This confirms ${current.title} on ${current.incident_code}.` : `This confirms ${current?.title}.`}>
        <Textarea aria-label="Note" placeholder="Add a note (optional)" value={note} onChange={(e) => setNote(e.target.value)} className="border-border-strong bg-background" />
      </ConfirmDialog>
      <ConfirmDialog open={dialog === "dismiss"} onOpenChange={(o) => !o && setDialog(null)} title="Dismiss as false alarm?" confirmLabel="Dismiss event" variant="outline" onConfirm={() => review("dismiss")}
        description="Pick a reason. It helps tune this camera.">
        <RadioGroup value={reason} onValueChange={setReason} className="gap-0">
          {dismissReasons.map((r) => (
            <Label key={r} htmlFor={`r-${r}`} className="flex cursor-pointer items-center gap-3 border-b border-border py-2.5 font-normal"><RadioGroupItem id={`r-${r}`} value={r} />{r}</Label>
          ))}
        </RadioGroup>
      </ConfirmDialog>
    </div>
  );
}
