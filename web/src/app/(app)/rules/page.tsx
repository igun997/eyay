"use client";
import { useState } from "react";
import { Lock } from "lucide-react";
import { PageHeader, Panel } from "@/components/console/page-header";
import { SeverityPill } from "@/components/console/severity-pill";
import { StatusChip } from "@/components/console/status-chip";
import { ErrorState, LoadingList } from "@/components/console/states";
import { useApi } from "@/hooks/use-api";
import { cn } from "@/lib/utils";
import type { CategoryRow } from "@/lib/types";

export default function RulesPage() {
  const { data, error, loading, refresh } = useApi<{ categories: CategoryRow[] }>("/api/categories", 30000);
  const [sel, setSel] = useState<string | null>(null);
  if (loading) return <LoadingList />;
  if (!data) return <ErrorState onRetry={() => refresh()} message={error?.message} />;
  const rows = data.categories;
  const cur = rows.find((r) => r.label === sel) ?? rows[0];
  return (
    <>
      <PageHeader title="Rules" badge={<StatusChip icon={Lock} className="h-6">READ-ONLY</StatusChip>} subtitle={`${rows.length} categories · ${rows.filter((r) => r.enabled).length} enabled · rules only create events and incidents, never physical actions`} />
      <div className="grid gap-5 px-8 pb-10 xl:grid-cols-[1fr_400px]">
        <Panel>
          <table className="w-full text-sm">
            <thead><tr className="border-b border-border text-left"><th className="caps px-5 py-3 font-normal">Category</th><th className="caps py-3 font-normal">Severity</th><th className="caps py-3 font-normal">Incident</th><th className="caps py-3 pr-5 font-normal">State</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.label} onClick={() => setSel(r.label)} className={cn("cursor-pointer border-b border-border last:border-0 hover:bg-surface-2", r.label === cur?.label && "bg-surface-2 shadow-[inset_2px_0_0_var(--accent)]")}>
                <td className="px-5 py-3.5"><p className="font-mono font-semibold">{r.title}</p><p className="text-xs text-text-2">Detector class “{r.label}”</p></td>
                <td><SeverityPill severity={r.severity} /></td>
                <td className="text-text-2">{r.opens_incident ? "Opens immediately" : "Event only"}</td>
                <td className="pr-5"><StatusChip tone={r.enabled ? "ok" : "neutral"} dot className="border-transparent bg-transparent">{r.enabled ? "ENABLED" : "DISABLED"}</StatusChip></td>
              </tr>))}</tbody>
          </table>
        </Panel>
        {cur && (
          <Panel bodyClassName="divide-y divide-border">
            <div className="p-5"><h2 className="font-mono text-lg font-semibold">{cur.title}</h2><p className="mt-2 text-sm text-text-2">Applies on every source that lists “{cur.label}” in its categories.</p></div>
            <div className="space-y-2 p-5"><p className="caps">When</p>
              {[`Live stream detects a ${cur.label} above the source’s minimum confidence`, "The tracker sees a new track, or the cooldown has passed"].map((t, n) => <p key={t} className="flex gap-3 text-sm"><span className="font-mono text-xs text-brand">0{n + 1}</span>{t}</p>)}</div>
            <div className="space-y-2 p-5"><p className="caps">Then</p>
              <p className="grid grid-cols-[80px_1fr] text-sm"><span className="font-mono text-xs text-text-3">Event</span>Severity {cur.severity}, sent to triage</p>
              <p className="grid grid-cols-[80px_1fr] text-sm"><span className="font-mono text-xs text-text-3">Incident</span>{cur.opens_incident ? "Opened immediately; repeats within 5 min are grouped" : "Not opened"}</p>
              <p className="grid grid-cols-[80px_1fr] text-sm"><span className="font-mono text-xs text-text-3">Cooldown</span>{cur.cooldown_s} s per camera</p></div>
            <p className="m-5 border-l-2 border-brand bg-surface-2 p-3 text-xs text-text-2"><span className="caps block">Events only</span>This rule cannot open doors, sound alarms or control machines.</p>
          </Panel>
        )}
      </div>
    </>
  );
}
