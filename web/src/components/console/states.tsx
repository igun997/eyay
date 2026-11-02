"use client";
import { AlertCircle, Inbox, WifiOff, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useT } from "@/lib/i18n/client";

function Centered({ icon: Icon, tone, title, children, actions }: { icon: LucideIcon; tone?: string; title: string; children?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
      <Icon className={`size-8 ${tone ?? "text-text-2"}`} aria-hidden />
      <h3 className="text-base font-semibold">{title}</h3>
      {children && <p className="max-w-sm text-sm text-text-2">{children}</p>}
      {actions && <div className="mt-1 flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export const EmptyState = ({ title, children, action }: { title: string; children?: React.ReactNode; action?: React.ReactNode }) => (
  <Centered icon={Inbox} title={title} actions={action}>{children}</Centered>
);

export const ErrorState = ({ title, message, onRetry }: { title?: string; message?: string; onRetry?: () => void }) => { const t = useT(); return (
  <Centered icon={AlertCircle} tone="text-sev-critical" title={title ?? t("states.couldntLoad")}
    actions={onRetry && <Button variant="outline" onClick={onRetry}>{t("states.tryAgain")}</Button>}>
    {message ?? t("states.theServerDidNotRespond")}
  </Centered>
); };

export const LoadingList = ({ rows = 3 }: { rows?: number }) => { const t = useT(); return (
  <div className="space-y-3 p-4" role="status" aria-label={t("states.loading")}>
    {Array.from({ length: rows }, (_, i) => (
      <div key={i} className="flex gap-4 rounded-sm border border-border bg-surface p-3">
        <Skeleton className="h-16 w-28 rounded-sm" />
        <div className="flex-1 space-y-2 py-1"><Skeleton className="h-3 w-3/4" /><Skeleton className="h-3 w-1/2" /><Skeleton className="h-3 w-1/3" /></div>
      </div>
    ))}
  </div>
); };

export const OfflineBanner = () => { const t = useT(); return (
  <div role="alert" className="mx-8 mb-3 flex items-center gap-3 rounded-sm border border-sev-high bg-sev-high/10 px-4 py-3">
    <WifiOff className="size-4 text-sev-high" aria-hidden />
    <div><p className="text-sm font-semibold">{t("states.connectionLost")}</p><p className="text-xs text-text-2">{t("states.showingTheLastDataReceived")}</p></div>
  </div>
); };
