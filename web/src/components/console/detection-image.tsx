"use client";
import { cn } from "@/lib/utils";
import type { BBox } from "@/lib/types";
import { useFmt, useT } from "@/lib/i18n/client";

/** Snapshot with the detection box overlaid from normalised bbox (the JPEG itself is stored raw). */
export function DetectionImage({ src, bbox, label, confidence, className, showTag = true }: {
  src: string; bbox?: BBox | null; label?: string; confidence?: number; className?: string; showTag?: boolean;
}) {
  const t = useT();
  const fmt = useFmt();
  return (
    <div className={cn("relative aspect-video overflow-hidden rounded-sm bg-black", className)}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt={label ? t("detection.alt", { label }) : t("detectionImage.savedPhoto")} className="size-full object-contain" />
      {bbox && (
        <div className="absolute border-2 border-brand" style={{ left: `${bbox.x * 100}%`, top: `${bbox.y * 100}%`, width: `${bbox.w * 100}%`, height: `${bbox.h * 100}%` }}>
          {showTag && label && (
            <span className="absolute -top-5 -left-0.5 bg-brand px-1.5 font-mono text-[10px] font-semibold uppercase leading-5 text-brand-ink whitespace-nowrap">
              {label} {confidence !== undefined && fmt.num(confidence)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
