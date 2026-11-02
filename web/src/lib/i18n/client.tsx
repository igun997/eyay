"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { DEFAULT_LANG, fmtAgo, fmtDate, fmtDateTime, fmtNumber, fmtTime, makeT, setLangCookie, type Lang, type T } from "./index";

type Ctx = { lang: Lang; t: T; setLang: (l: Lang) => void };
const LangContext = createContext<Ctx>({ lang: DEFAULT_LANG, t: makeT(DEFAULT_LANG), setLang: () => {} });

/** Gets the language from the server (the cookie) and re-renders everything the moment it is switched. */
export function LangProvider({ lang: initial, children }: { lang: Lang; children: React.ReactNode }) {
  const router = useRouter();
  const [chosen, setChosen] = useState<Lang | null>(null);
  useEffect(() => setChosen(null), [initial]); // the server caught up: trust it again
  const lang = chosen ?? initial;
  const setLang = useCallback((l: Lang) => {
    setLangCookie(l, document);
    setChosen(l);
    router.refresh(); // server components and route-rendered text follow the cookie too
  }, [router]);
  const value = useMemo(() => ({ lang, t: makeT(lang), setLang }), [lang, setLang]);
  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export const useLang = () => useContext(LangContext).lang;
export const useT = () => useContext(LangContext).t;
export const useSetLang = () => useContext(LangContext).setLang;

/** Dates, times, numbers and relative times in the current language. */
export function useFmt() {
  const lang = useLang();
  return useMemo(() => ({
    dateTime: (iso: string) => fmtDateTime(lang, iso),
    date: (iso: string) => fmtDate(lang, iso),
    time: (iso: string) => fmtTime(lang, iso),
    num: (n: number, digits = 2) => fmtNumber(lang, n, digits),
    ago: (iso: string | null | undefined) => fmtAgo(lang, iso),
  }), [lang]);
}
