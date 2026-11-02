"use client";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { PageHeader, Panel } from "@/components/console/page-header";
import { SeverityPill } from "@/components/console/severity-pill";
import { IncidentStatusChip, PipelineStatusChip, StatusChip } from "@/components/console/status-chip";
import { EmptyState, ErrorState, LoadingList, OfflineBanner } from "@/components/console/states";
import { useApi } from "@/hooks/use-api";
import { ago } from "@/lib/format";
import type { IncidentRow } from "@/lib/types";

type Overview = {
  counts: { open_incidents: number; awaiting: number; online: number; total: number; events_24h: number; confirmed_24h: number; dismissed_24h: number };
  worker: { last_seen: string; sources: number; model: string | null; classifier: string | null } | null;
  pipeline: { queued: number; classifying: number; ready: number; failed: number };
  byLabel: { label: string; n: number }[];
  attention: IncidentRow[];
};

function Bar({ label, value, max, tone }: { label: string; value: number; max: number; tone: string }) {
  return (
    <div className="grid grid-cols-[180px_1fr_48px] items-center gap-4 text-sm">
      <span className="text-text-2">{label}</span>
      <div className="h-2 rounded-sm bg-surface-2"><div className={`h-full rounded-sm ${tone}`} style={{ width: `${max ? Math.max(2, (value / max) * 100) : 0}%` }} /></div>
      <span className="text-right font-mono">{value}</span>
    </div>
  );
}

export default function OverviewPage() {
  const { data, error, loading, refresh, offline } = useApi<Overview>("/api/overview", 5000);
  if (loading) return <LoadingList />;
  if (!data) return <ErrorState onRetry={() => refresh()} message={error?.message} />;
  const { counts: c, worker } = data;
  const workerOk = !!worker && Date.now() - new Date(worker.last_seen).getTime() < 20000;
  const filtered = c.events_24h ? Math.round(((c.dismissed_24h) / c.events_24h) * 100) : 0;
  return (
    <>
      <PageHeader title="Overview" subtitle={`${c.open_incidents} open incidents · ${c.awaiting} awaiting triage · ${c.online} of ${c.total} cameras online`} />
      {offline && <OfflineBanner />}
      <div className="grid gap-5 px-8 pb-10 xl:grid-cols-[1fr_380px]">
        <div className="space-y-5">
          <Panel title="Needs attention" aside="Open incidents, most urgent first">
            {data.attention.length === 0 ? <EmptyState title="No open incidents">New incidents appear here as soon as a camera spots something that opens one.</EmptyState> : (
              <ul className="divide-y divide-border">
                {data.attention.map((i) => (
                  <li key={i.id}>
                    <Link href={`/incidents/${i.code}`} className="flex items-center gap-4 px-5 py-4 hover:bg-surface-2">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[15px] font-medium">{i.title}</p>
                        <p className="mt-1 font-mono text-xs text-text-2">{i.code} · {i.source_code} · {i.assignee_name ?? "Unassigned"}</p>
                      </div>
                      <SeverityPill severity={i.severity} /><IncidentStatusChip status={i.status} />
                      <span className="w-14 text-right font-mono text-xs text-text-2">{ago(i.opened_at)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex items-center justify-between border-t border-border px-5 py-4">
              <span className="text-sm text-text-2">{c.awaiting} events are waiting in the triage queue</span>
              <Button variant="outline" asChild><Link href="/triage">Open triage</Link></Button>
            </div>
          </Panel>

          <Panel title="Noise filtering · last 24 h" bodyClassName="space-y-4 p-5">
            <p><span className="text-5xl font-semibold tracking-tight">{filtered}%</span> <span className="ml-2 text-text-2">of detections were dismissed as false alarms</span></p>
            <Bar label="Detections" value={c.events_24h} max={c.events_24h} tone="bg-border-strong" />
            <Bar label="Awaiting triage" value={c.awaiting} max={c.events_24h} tone="bg-text-3" />
            <Bar label="Confirmed" value={c.confirmed_24h} max={c.events_24h} tone="bg-brand" />
          </Panel>

          <Panel title="Second AI check" aside={data.worker?.classifier ? <span className="font-mono">on</span> : <span className="font-mono">not switched on (optional)</span>} bodyClassName="flex flex-wrap items-center gap-x-6 gap-y-3 p-5">
            {([["queued", data.pipeline.queued], ["classifying", data.pipeline.classifying], ["ready", data.pipeline.ready], ["failed", data.pipeline.failed]] as const).map(([k, n]) => (
              <span key={k} className="flex items-center gap-2"><PipelineStatusChip status={k} /><span className="font-mono text-sm">{n}</span></span>
            ))}
          </Panel>

          <Panel title="By category · last 24 h" bodyClassName="flex flex-wrap gap-3 p-5">
            {data.byLabel.length === 0 ? <p className="text-sm text-text-2">No detections yet.</p> : data.byLabel.map((b) => (
              <StatusChip key={b.label} tone="neutral" className="h-7 px-3 text-xs">{b.label} <span className="text-foreground">{b.n}</span></StatusChip>
            ))}
          </Panel>
        </div>

        <div className="space-y-5">
          <Panel title="Live cameras" bodyClassName="p-5 space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-lg font-semibold">Cameras</span>
              <StatusChip tone={c.online === c.total && c.total > 0 ? "ok" : "warn"} dot>{c.online === c.total && c.total > 0 ? "HEALTHY" : "DEGRADED"}</StatusChip>
            </div>
            <dl className="grid grid-cols-2 gap-4">
              <div><dt className="caps">Online</dt><dd className="mt-1 font-mono">{c.online} / {c.total}</dd></div>
              <div><dt className="caps">Analysis service</dt><dd className="mt-1 font-mono">{workerOk ? `OK · ${worker!.sources} cameras watched` : "NOT RESPONDING"}</dd></div>
            </dl>
          </Panel>
        </div>
      </div>
    </>
  );
}
