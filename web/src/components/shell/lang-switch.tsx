"use client";
import { cn } from "@/lib/utils";
import { LANGS, type Lang } from "@/lib/i18n";
import { useLang, useSetLang, useT } from "@/lib/i18n/client";

const NAME: Record<Lang, string> = { id: "Bahasa Indonesia", en: "English" };

/** ID | EN. Works before login (it only sets a cookie) and updates the page without a reload. */
export function LangSwitch({ className }: { className?: string }) {
  const lang = useLang();
  const setLang = useSetLang();
  const t = useT();
  return (
    <div role="group" aria-label={t("lang.switch")} className={cn("inline-flex overflow-hidden rounded-sm border border-border-strong font-mono text-[11px]", className)}>
      {LANGS.map((l, i) => (
        <button key={l} type="button" lang={l} title={NAME[l]} aria-pressed={lang === l} onClick={() => setLang(l)}
          className={cn("px-2 py-1 uppercase", i > 0 && "border-l border-border-strong", lang === l ? "bg-surface-2 font-semibold text-foreground" : "text-text-2 hover:text-foreground")}>{l}</button>
      ))}
    </div>
  );
}
