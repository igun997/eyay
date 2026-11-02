"use client";
import { Camera, Inbox, MessageSquare, Settings2, User, type LucideIcon } from "lucide-react";
import type { TimelineRow } from "@/lib/types";
import { tOr } from "@/lib/i18n";
import { useFmt, useT } from "@/lib/i18n/client";

const icons: Record<string, LucideIcon> = { camera: Camera, triage: Inbox, operator: User, note: MessageSquare, system: Settings2 };

export function Timeline({ rows, renderExtra }: { rows: TimelineRow[]; renderExtra?: (row: TimelineRow) => React.ReactNode }) {
  const t = useT();
  const fmt = useFmt();
  return (
    <ol className="relative">
      {rows.map((r, i) => {
        const Icon = icons[r.kind] ?? Settings2;
        return (
          <li key={r.id} className="relative grid grid-cols-[72px_32px_1fr] gap-x-3 pb-5">
            <time className="pt-1.5 font-mono text-xs text-text-2">{fmt.time(r.at)}</time>
            <div className="relative flex justify-center">
              {i < rows.length - 1 && <span className="absolute top-8 -bottom-5 w-px bg-border" aria-hidden />}
              <span className="z-10 flex size-8 items-center justify-center rounded-sm border border-border-strong bg-surface"><Icon className="size-4 text-text-2" aria-hidden /></span>
            </div>
            <div className="min-w-0 pt-0.5">
              <p className="text-[14px]"><span className="caps mr-2">{tOr(t, `kind.${r.kind}`, r.kind.toUpperCase())}</span><span className="font-medium">{r.title}</span></p>
              {(r.detail || r.user_name) && <p className="mt-0.5 font-mono text-xs text-text-2">{r.detail}{r.detail && r.user_name ? " · " : ""}{r.user_name}</p>}
              {renderExtra?.(r)}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
