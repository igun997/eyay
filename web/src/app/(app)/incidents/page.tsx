"use client";
import Link from "next/link";
import { useState } from "react";
import { PageHeader } from "@/components/console/page-header";
import { SeverityPill } from "@/components/console/severity-pill";
import { IncidentStatusChip } from "@/components/console/status-chip";
import { EmptyState, ErrorState, LoadingList, OfflineBanner } from "@/components/console/states";
import { useApi } from "@/hooks/use-api";
import { cn } from "@/lib/utils";
import type { IncidentRow } from "@/lib/types";
import { useFmt, useT } from "@/lib/i18n/client";

export default function IncidentsPage() {
  const t = useT();
  const fmt = useFmt();
  const [tab, setTab] = useState<"active" | "resolved">("active");
  const { data, error, loading, refresh, offline } = useApi<IncidentRow[]>(`/api/incidents?status=${tab}`, 5000);
  return (
    <>
      <PageHeader title={t("incidents.incidents")} subtitle={t("incidents.openedAutomaticallyWhenADetection")} />
      <div role="tablist" className="flex gap-6 border-b border-border px-8">
        {(["active", "resolved"] as const).map((k) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)} className={cn("-mb-px border-b-2 pb-3 text-sm capitalize", tab === k ? "border-brand font-medium" : "border-transparent text-text-2")}>{k}</button>
        ))}
      </div>
      {offline && <div className="pt-3"><OfflineBanner /></div>}
      <div className="px-8 py-5">
        {loading ? <LoadingList /> : !data ? <ErrorState onRetry={() => refresh()} message={error?.message} /> :
          data.length === 0 ? <EmptyState title={tab === "active" ? t("incidents.noOpenIncidents") : t("incidents.noResolvedIncidents")} /> : (
            <div className="overflow-hidden rounded-sm border border-border bg-surface">
              <table className="w-full text-sm">
                <thead><tr className="border-b border-border text-left"><th className="caps px-5 py-3 font-normal">{t("incidents.incident")}</th><th className="caps py-3 font-normal">{t("incidents.camera")}</th><th className="caps py-3 font-normal">{t("incidents.severity")}</th><th className="caps py-3 font-normal">{t("incidents.status")}</th><th className="caps py-3 font-normal">{t("incidents.assigned")}</th><th className="caps py-3 pr-5 text-right font-normal">{t("incidents.opened")}</th></tr></thead>
                <tbody>
                  {data.map((i) => (
                    <tr key={i.id} className="border-b border-border last:border-0 hover:bg-surface-2">
                      <td className="px-5 py-3.5"><Link href={`/incidents/${i.code}`} className="block"><span className="font-medium">{i.title}</span><span className="block font-mono text-xs text-text-2">{i.code} · {i.events} {t("incidents.events")}</span></Link></td>
                      <td className="font-mono text-xs">{i.source_code ?? "–"}</td>
                      <td><SeverityPill severity={i.severity} /></td>
                      <td><IncidentStatusChip status={i.status} /></td>
                      <td className="text-text-2">{i.assignee_name ?? t("incidents.unassigned")}</td>
                      <td className="pr-5 text-right font-mono text-xs text-text-2">{fmt.ago(i.opened_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>
    </>
  );
}
