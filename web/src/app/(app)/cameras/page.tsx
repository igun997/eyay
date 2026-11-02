"use client";
import { useState } from "react";
import Link from "next/link";
import { PageHeader, Panel } from "@/components/console/page-header";
import { LiveStream } from "@/components/console/live-stream";
import { EmptyState, ErrorState, LoadingList } from "@/components/console/states";
import { Button } from "@/components/ui/button";
import { useApi } from "@/hooks/use-api";
import { cn } from "@/lib/utils";
import type { SourceRow } from "@/lib/types";
import { useFmt, useT } from "@/lib/i18n/client";

export default function CamerasPage() {
  const t = useT();
  const fmt = useFmt();
  const { data, error, loading, refresh } = useApi<SourceRow[]>("/api/sources", 5000);
  const { data: me } = useApi<{ role: string }>("/api/me", 60000);
  const [sel, setSel] = useState<string | null>(null);
  if (loading) return <LoadingList />;
  if (!data) return <ErrorState onRetry={() => refresh()} message={error?.message} />;
  const cams = data.filter((s) => s.enabled);
  const cur = cams.find((c) => c.id === sel) ?? cams.find((c) => c.status === "online") ?? cams[0];
  const online = cams.filter((c) => c.status === "online").length;
  return (
    <>
      <PageHeader title={t("cameras.cameras")} subtitle={t("cameras.subtitle", { n: cams.length, online })} />
      {cams.length === 0 ? (
        <EmptyState title={t("cameras.noCamerasAddedYet")} action={me?.role === "admin" ? <Button asChild><Link href="/sources">{t("cameras.addACamera")}</Link></Button> : undefined}>
          {me?.role === "admin" ? t("cameras.addACameraSLive") : t("cameras.askAnAdministratorToAdd")}
        </EmptyState>
      ) : (
        <div className="grid gap-5 px-8 pb-10 xl:grid-cols-[1fr_340px]">
          <div className="space-y-3">
            {cur && <LiveStream source={cur} />}
            {cur && <p className="font-mono text-xs text-text-2">{cur.code} · {cur.status === "online" ? t("cameras.throughput", { fps: cur.fps != null ? fmt.num(cur.fps, 1) : "–", ms: cur.inference_ms != null ? fmt.num(cur.inference_ms, 0) : "–" }) : cur.last_error ?? cur.status}</p>}
          </div>
          <Panel title={t("cameras.panelTitle", { n: cams.length })} bodyClassName="grid grid-cols-2 gap-3 p-3">
            {cams.map((c) => (
              <button key={c.id} onClick={() => setSel(c.id)} aria-pressed={c.id === cur?.id} className={cn("overflow-hidden rounded-sm border bg-surface-2 text-left", c.id === cur?.id ? "border-brand" : "border-border")}>
                <LiveStream source={c} compact className="rounded-none" />
                <div className="p-2.5"><p className="font-mono text-[13px] font-semibold">{c.code}</p>
                  <p className={cn("truncate text-xs", c.status === "online" ? "text-text-2" : "text-sev-critical")}>{c.status === "online" ? c.name : t("cameras.offlineSince", { ago: fmt.ago(c.last_seen) })}</p></div>
              </button>
            ))}
          </Panel>
        </div>
      )}
    </>
  );
}
