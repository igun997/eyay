"use client";
import { useState } from "react";
import { Lock, Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { sensitivityText } from "@/lib/plain";
import { RuleDialog } from "@/components/console/rule-form";
import { CategoryDialog } from "@/components/console/category-form";
import { ConfirmDialog } from "@/components/console/confirm-dialog";
import { PageHeader, Panel } from "@/components/console/page-header";
import { SeverityPill } from "@/components/console/severity-pill";
import { StatusChip } from "@/components/console/status-chip";
import { ErrorState, LoadingList } from "@/components/console/states";
import { api, revalidate, useApi } from "@/hooks/use-api";
import { cn } from "@/lib/utils";
import type { BehaviorRule, CategoryRow } from "@/lib/types";

export default function RulesPage() {
  const { data, error, loading, refresh } = useApi<{ categories: CategoryRow[]; rules: BehaviorRule[] }>("/api/categories", 30000);
  const { data: me } = useApi<{ role: string }>("/api/me", 60000);
  const admin = me?.role === "admin";
  const [sel, setSel] = useState<string | null>(null);
  const [editing, setEditing] = useState<BehaviorRule | "new" | null>(null);
  const [removing, setRemoving] = useState<BehaviorRule | null>(null);
  const [editingCat, setEditingCat] = useState<CategoryRow | null>(null);
  if (loading) return <LoadingList />;
  if (!data) return <ErrorState onRetry={() => refresh()} message={error?.message} />;
  const rows = data.categories;
  const toggleRule = async (r: BehaviorRule, enabled: boolean) => {
    try { const { key, ...rest } = r; void key; await api(`/api/rules/${r.key}`, { method: "PUT", json: { ...rest, enabled } }); await revalidate("/api/categories"); } catch (e) { toast.error((e as Error).message); }
  };
  const cur = rows.find((r) => r.label === sel) ?? rows[0];
  return (
    <>
      <PageHeader title="Rules" badge={admin ? undefined : <StatusChip icon={Lock} className="h-6">READ-ONLY</StatusChip>} subtitle={`${rows.length} categories · ${rows.filter((r) => r.enabled).length} enabled · categories decide how events and incidents are created; alerts are set up under Alerts`} />
      <div className="grid gap-5 px-8 pb-10 xl:grid-cols-[1fr_400px]">
        <Panel title="Categories" aside={admin ? "Click a row, then Edit" : undefined}>
          <table className="w-full text-sm">
            <thead><tr className="border-b border-border text-left"><th className="caps px-5 py-3 font-normal">Category</th><th className="caps py-3 font-normal">Severity</th><th className="caps py-3 font-normal">Incident</th><th className="caps py-3 pr-5 font-normal">State</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.label} onClick={() => setSel(r.label)} className={cn("cursor-pointer border-b border-border last:border-0 hover:bg-surface-2", r.label === cur?.label && "bg-surface-2 shadow-[inset_2px_0_0_var(--accent)]")}>
                <td className="px-5 py-3.5"><p className="font-mono font-semibold">{r.title}</p><p className="text-xs text-text-2">The camera AI’s word for it: “{r.label}”</p></td>
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
              {[`A live camera spots a ${cur.label} with at least the camera’s minimum certainty`, "It is a new object, or the quiet period has passed"].map((t, n) => <p key={t} className="flex gap-3 text-sm"><span className="font-mono text-xs text-brand">0{n + 1}</span>{t}</p>)}</div>
            <div className="space-y-2 p-5"><p className="caps">Then</p>
              <p className="grid grid-cols-[80px_1fr] text-sm"><span className="font-mono text-xs text-text-3">Event</span>Severity {cur.severity}, sent to triage</p>
              <p className="grid grid-cols-[80px_1fr] text-sm"><span className="font-mono text-xs text-text-3">Incident</span>{cur.opens_incident ? "Opened immediately; repeats within 5 min are grouped" : "Not opened"}</p>
              <p className="grid grid-cols-[80px_1fr] text-sm"><span className="font-mono text-xs text-text-3">Quiet period</span>{cur.cooldown_s} s per camera</p>
              <p className="grid grid-cols-[80px_1fr] text-sm"><span className="font-mono text-xs text-text-3">Photos</span>{cur.burst_s > 0 ? `${cur.burst_frames} follow-up photos over up to ${cur.burst_s} s (never video)` : "a single photo"}</p></div>
            <div className="space-y-1 px-5 pb-1 pt-4 text-xs text-text-2">
              <p><span className="font-mono text-text-3">Quiet period</span> · one event per kind of object per camera per quiet period.</p>
              <p><span className="font-mono text-text-3">Follow-up photos</span> · N photos over S seconds after an event; warning-sign rules look at them.</p>
            </div>
            {admin && <div className="p-5"><Button variant="outline" onClick={() => setEditingCat(cur)}>Edit category</Button></div>}
            <p className="m-5 border-l-2 border-brand bg-surface-2 p-3 text-xs text-text-2"><span className="caps block">Events only</span>A category only creates events and incidents. Sending alerts is set up separately under Alerts, and only for what an admin switches on.</p>
          </Panel>
        )}
      </div>
      <div className="px-8 pb-10">
        <Panel title="Behaviour rules · warning signs of suspicious behaviour" aside={<span className="flex items-center gap-3">Warning signs for a person to check, never verdicts{admin && <Button size="sm" onClick={() => setEditing("new")}>Add rule</Button>}</span>} bodyClassName="">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-border text-left"><th className="caps px-5 py-3 font-normal">Rule</th><th className="caps py-3 font-normal">Applies to</th><th className="caps py-3 font-normal">How it is spotted</th><th className="caps py-3 font-normal">Sensitivity</th><th className="caps py-3 font-normal">Severity</th><th className="caps py-3 pr-5 font-normal">State</th>{admin && <th className="caps py-3 pr-5 text-right font-normal">Edit</th>}</tr></thead>
            <tbody>{data.rules.map((r) => (
              <tr key={r.key} className="border-b border-border align-top last:border-0">
                <td className="px-5 py-3"><p className="font-medium">{r.title}</p><p className="text-xs text-text-2">{r.description}</p>{r.question && <p className="mt-1 font-mono text-[11px] text-text-3">“{r.question}”</p>}</td>
                <td className="py-3 font-mono text-xs">{r.applies_to.join(", ")}</td>
                <td className="py-3 text-xs">{r.kind === "classifier" ? <><span className="font-mono">Second AI check</span><span className="block text-text-3">needs the optional second AI check to be switched on</span></>
                  : r.kind === "object" ? <><span className="font-mono">Spotted next to a person</span><span className="block text-text-3">{(r.config.objects ?? []).join(", ")} · works with the cameras alone</span></>
                  : <><span className="font-mono">Activity pattern</span><span className="block text-text-3">works with the cameras alone</span></>}</td>
                <td className="py-3 font-mono text-xs" title={r.kind === "indication" ? undefined : `Shows from ${r.threshold.toFixed(2)}${r.kind === "object" ? " certainty that the object is there" : " chance of “yes”"}`}>{r.kind === "indication" ? "whole follow-up" : <>{sensitivityText(r.threshold)} <span className="text-text-3">({r.threshold.toFixed(2)})</span></>}</td>
                <td className="py-3"><SeverityPill severity={r.severity} /></td>
                <td className="py-3 pr-5">{admin ? <Switch aria-label={`Enable ${r.title}`} checked={r.enabled} onCheckedChange={(v) => toggleRule(r, v)} /> : <StatusChip tone={r.enabled ? "ok" : "neutral"} dot className="border-transparent bg-transparent">{r.enabled ? "ENABLED" : "OFF"}</StatusChip>}</td>
                {admin && <td className="py-3 pr-5 text-right"><div className="flex justify-end gap-1"><Button variant="ghost" size="icon" aria-label={`Edit ${r.title}`} onClick={() => setEditing(r)}><Pencil className="size-4" aria-hidden /></Button><Button variant="ghost" size="icon" aria-label={`Delete ${r.title}`} className="text-sev-critical" onClick={() => setRemoving(r)}><Trash2 className="size-4" aria-hidden /></Button></div></td>}
              </tr>))}</tbody>
          </table>
        </Panel>
      </div>
      {editingCat && <CategoryDialog key={editingCat.label} open onOpenChange={(o) => !o && setEditingCat(null)} category={editingCat} onSaved={() => revalidate("/api/categories")} />}
      {editing && <RuleDialog open onOpenChange={(o) => !o && setEditing(null)} categories={data.categories} rule={editing === "new" ? undefined : editing} nextPosition={Math.max(0, ...data.rules.map((r) => r.position)) + 1} onSaved={() => revalidate("/api/categories")} />}
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} title={`Delete ${removing?.title}?`} confirmLabel="Delete rule" variant="destructive"
        description="Events it already flagged keep their warning sign. New events are no longer checked against it." onConfirm={async () => { await api(`/api/rules/${removing!.key}`, { method: "DELETE" }); toast.success("Rule deleted"); await revalidate("/api/categories"); }} />
    </>
  );
}
