"use client";

import { useT } from "@/lib/i18n/client";export function Logo({ size = 22 }: { size?: number }) {
  const t = useT();
  return (
    <span className="flex items-center gap-2.5">
      <svg width={size * 1.4} height={size} viewBox="0 0 28 20" fill="none" aria-hidden>
        <path d="M1.5 10S6 2 14 2s12.5 8 12.5 8S22 18 14 18 1.5 10 1.5 10Z" stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" />
        <circle cx="14" cy="10" r="4.5" stroke="var(--accent)" strokeWidth="2" /><circle cx="14" cy="10" r="1.8" fill="var(--accent)" />
      </svg>
      <span className="text-lg font-semibold tracking-tight">{t("logo.eyeai")}</span>
    </span>
  );
}
