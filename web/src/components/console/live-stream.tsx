"use client";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import type { SourceRow } from "@/lib/types";

/** Live picture with boxes drawn on it. Shows NO SIGNAL when the camera is not online or the picture fails. */
export function LiveStream({ source, className, compact }: { source: Pick<SourceRow, "id" | "status" | "code" | "name" | "kind">; className?: string; compact?: boolean }) {
  const [failed, setFailed] = useState(false);
  const [nonce, setNonce] = useState(0);
  const live = source.status === "online" && !failed;
  const [tick, setTick] = useState(0);
  // compact tiles poll a still instead of holding one MJPEG connection per tile (browser connection limit)
  useEffect(() => {
    if (!compact || !live) return;
    const t = setInterval(() => setTick((n) => n + 1), 2000);
    return () => clearInterval(t);
  }, [compact, live]);
  useEffect(() => { setFailed(false); }, [source.id, source.status]);
  // retry a failed stream every 5 s while the source claims to be online
  useEffect(() => {
    if (!failed || source.status !== "online") return;
    const t = setTimeout(() => { setFailed(false); setNonce((n) => n + 1); }, 5000);
    return () => clearTimeout(t);
  }, [failed, source.status]);
  return (
    <div className={cn("relative aspect-video overflow-hidden rounded-sm bg-black", className)}>
      {live ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img key={`${source.id}-${nonce}`} src={compact ? `/api/live/${source.id}?mode=snapshot&t=${tick}` : `/api/live/${source.id}?n=${nonce}`} alt={`Live ${source.code}`} className="size-full object-contain" onError={() => setFailed(true)} />
      ) : (
        <div className="flex size-full items-center justify-center font-mono text-xs tracking-[0.2em] text-text-3">NO SIGNAL</div>
      )}
      {!compact && (
        <>
          <div className="absolute top-3 left-3 flex items-center gap-2 rounded-sm bg-black/70 px-2.5 py-1 font-mono text-xs">
            <span className="font-semibold">{source.code}</span><span className="text-text-2">{source.name}</span>
            {live && <span className="flex items-center gap-1 font-semibold"><span className="size-1.5 animate-pulse rounded-full bg-sev-critical" />LIVE</span>}
          </div>
          <div className="absolute top-3 right-3 rounded-sm bg-black/70 px-2.5 py-1 font-mono text-[11px] tracking-wider text-text-2">{source.kind === "rtsp" ? "CAMERA STREAM" : "WEB STREAM"}</div>
        </>
      )}
    </div>
  );
}
