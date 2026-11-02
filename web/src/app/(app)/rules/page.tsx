"use client";
import { useState } from "react";
import { Lock, Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { sensitivityKey } from "@/lib/plain";
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
import { tOr } from "@/lib/i18n";
import { useFmt, useT } from "@/lib/i18n/client";

export default function RulesPage() {
  const t = useT();
  const fmt = useFmt();
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
      <PageHeader title={t("rules.rules")} badge={admin ? undefined : <StatusChip icon={Lock} className="h-6">{t("rules.readOnly")}</StatusChip>} subtitle={t("rules.subtitle", { n: rows.length, enabled: rows.filter((r) => r.enabled).length })} />
      <div className="grid gap-5 px-8 pb-10 xl:grid-cols-[1fr_400px]">
        <Panel title={t("rules.categories")} aside={admin ? t("rules.clickARowThenEdit") : undefined}>
          <table className="w-full text-sm">
            <thead><tr className="border-b border-border text-left"><th className="caps px-5 py-3 font-normal">{t("rules.category")}</th><th className="caps py-3 font-normal">{t("rules.severity")}</th><th className="caps py-3 font-normal">{t("rules.incident")}</th><th className="caps py-3 pr-5 font-normal">{t("rules.state")}</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.label} onClick={() => setSel(r.label)} className={cn("cursor-pointer border-b border-border last:border-0 hover:bg-surface-2", r.label === cur?.label && "bg-surface-2 shadow-[inset_2px_0_0_var(--accent)]")}>
                <td className="px-5 py-3.5"><p className="font-mono font-semibold">{r.title}</p><p className="text-xs text-text-2">{t("rules.theCameraAiSWord")}{r.label}”</p></td>
                <td><SeverityPill severity={r.severity} /></td>
                <td className="text-text-2">{r.opens_incident ? t("rules.opensImmediately") : t("rules.eventOnly")}</td>
                <td className="pr-5"><StatusChip tone={r.enabled ? "ok" : "neutral"} dot className="border-transparent bg-transparent">{r.enabled ? t("rules.enabled") : t("rules.disabled")}</StatusChip></td>
              </tr>))}</tbody>
          </table>
        </Panel>
        {cur && (
          <Panel bodyClassName="divide-y divide-border">
            <div className="p-5"><h2 className="font-mono text-lg font-semibold">{cur.title}</h2><p className="mt-2 text-sm text-text-2">{t("rules.appliesOnEverySourceThat")}{cur.label}{t("rules.inItsCategories")}</p></div>
            <div className="space-y-2 p-5"><p className="caps">{t("rules.when")}</p>
              {[t("rules.step1", { kind: tOr(t, `label.${cur.label}`, cur.label) }), t("rules.step2")].map((s, n) => <p key={s} className="flex gap-3 text-sm"><span className="font-mono text-xs text-brand">0{n + 1}</span>{s}</p>)}</div>
            <div className="space-y-2 p-5"><p className="caps">{t("rules.then")}</p>
              <p className="grid grid-cols-[80px_1fr] text-sm"><span className="font-mono text-xs text-text-3">{t("rules.event")}</span>{t("rules.severity")} {cur.severity}{t("rules.sentToTriage")}</p>
              <p className="grid grid-cols-[80px_1fr] text-sm"><span className="font-mono text-xs text-text-3">{t("rules.incident")}</span>{cur.opens_incident ? t("rules.openedImmediatelyRepeatsWithin5") : t("rules.notOpened")}</p>
              <p className="grid grid-cols-[80px_1fr] text-sm"><span className="font-mono text-xs text-text-3">{t("rules.quietPeriod")}</span>{cur.cooldown_s} {t("rules.sPerCamera")}</p>
              <p className="grid grid-cols-[80px_1fr] text-sm"><span className="font-mono text-xs text-text-3">{t("rules.photos")}</span>{cur.burst_s > 0 ? t("rules.photosLine", { n: cur.burst_frames, s: cur.burst_s }) : t("rules.aSinglePhoto")}</p></div>
            <div className="space-y-1 px-5 pb-1 pt-4 text-xs text-text-2">
              <p><span className="font-mono text-text-3">{t("rules.quietPeriod")}</span> · {t("rules.oneEventPerKindOf")}</p>
              <p><span className="font-mono text-text-3">{t("rules.followUpPhotos")}</span> · {t("rules.nPhotosOverSSeconds")}</p>
            </div>
            {admin && <div className="p-5"><Button variant="outline" onClick={() => setEditingCat(cur)}>{t("rules.editCategory")}</Button></div>}
            <p className="m-5 border-l-2 border-brand bg-surface-2 p-3 text-xs text-text-2"><span className="caps block">{t("rules.eventsOnly")}</span>{t("rules.aCategoryOnlyCreatesEvents")}</p>
          </Panel>
        )}
      </div>
      <div className="px-8 pb-10">
        <Panel title={t("rules.behaviourRulesWarningSignsOf")} aside={<span className="flex items-center gap-3">{t("rules.warningSignsForAPerson")}{admin && <Button size="sm" onClick={() => setEditing("new")}>{t("rules.addRule")}</Button>}</span>} bodyClassName="">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-border text-left"><th className="caps px-5 py-3 font-normal">{t("rules.rule")}</th><th className="caps py-3 font-normal">{t("rules.appliesTo")}</th><th className="caps py-3 font-normal">{t("rules.howItIsSpotted")}</th><th className="caps py-3 font-normal">{t("rules.sensitivity")}</th><th className="caps py-3 font-normal">{t("rules.severity")}</th><th className="caps py-3 pr-5 font-normal">{t("rules.state")}</th>{admin && <th className="caps py-3 pr-5 text-right font-normal">{t("rules.edit")}</th>}</tr></thead>
            <tbody>{data.rules.map((r) => (
              <tr key={r.key} className="border-b border-border align-top last:border-0">
                <td className="px-5 py-3"><p className="font-medium">{r.title}</p><p className="text-xs text-text-2">{r.description}</p>{r.question && <p className="mt-1 font-mono text-[11px] text-text-3">“{r.question}”</p>}</td>
                <td className="py-3 font-mono text-xs">{r.applies_to.join(", ")}</td>
                <td className="py-3 text-xs">{r.kind === "classifier" ? <><span className="font-mono">{t("rules.secondAiCheck")}</span><span className="block text-text-3">{t("rules.needsTheOptionalSecondAi")}</span></>
                  : r.kind === "object" ? <><span className="font-mono">{t("rules.spottedNextToAPerson")}</span><span className="block text-text-3">{(r.config.objects ?? []).join(", ")} · {t("rules.worksWithTheCamerasAlone")}</span></>
                  : <><span className="font-mono">{t("rules.activityPattern")}</span><span className="block text-text-3">{t("rules.worksWithTheCamerasAlone")}</span></>}</td>
                <td className="py-3 font-mono text-xs" title={r.kind === "indication" ? undefined : t(r.kind === "object" ? "rules.sensitivityTipObject" : "rules.sensitivityTipChance", { n: fmt.num(r.threshold) })}>{r.kind === "indication" ? t("rules.wholeFollowUp") : <>{t(sensitivityKey(r.threshold))} <span className="text-text-3">({fmt.num(r.threshold)})</span></>}</td>
                <td className="py-3"><SeverityPill severity={r.severity} /></td>
                <td className="py-3 pr-5">{admin ? <Switch aria-label={t("rules.enableName", { name: r.title })} checked={r.enabled} onCheckedChange={(v) => toggleRule(r, v)} /> : <StatusChip tone={r.enabled ? "ok" : "neutral"} dot className="border-transparent bg-transparent">{r.enabled ? t("rules.enabled") : t("rules.off")}</StatusChip>}</td>
                {admin && <td className="py-3 pr-5 text-right"><div className="flex justify-end gap-1"><Button variant="ghost" size="icon" aria-label={t("rules.editName", { name: r.title })} onClick={() => setEditing(r)}><Pencil className="size-4" aria-hidden /></Button><Button variant="ghost" size="icon" aria-label={t("rules.deleteName", { name: r.title })} className="text-sev-critical" onClick={() => setRemoving(r)}><Trash2 className="size-4" aria-hidden /></Button></div></td>}
              </tr>))}</tbody>
          </table>
        </Panel>
      </div>
      {editingCat && <CategoryDialog key={editingCat.label} open onOpenChange={(o) => !o && setEditingCat(null)} category={editingCat} onSaved={() => revalidate("/api/categories")} />}
      {editing && <RuleDialog open onOpenChange={(o) => !o && setEditing(null)} categories={data.categories} rule={editing === "new" ? undefined : editing} nextPosition={Math.max(0, ...data.rules.map((r) => r.position)) + 1} onSaved={() => revalidate("/api/categories")} />}
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} title={t("rules.deleteQ", { name: removing?.title ?? "" })} confirmLabel={t("rules.deleteRule")} variant="destructive"
        description={t("rules.eventsItAlreadyFlaggedKeep")} onConfirm={async () => { await api(`/api/rules/${removing!.key}`, { method: "DELETE" }); toast.success(t("rules.ruleDeleted")); await revalidate("/api/categories"); }} />
    </>
  );
}
