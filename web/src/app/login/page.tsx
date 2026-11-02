import { Suspense } from "react";
import { Logo } from "@/components/console/logo";
import { LangSwitch } from "@/components/shell/lang-switch";
import { getT } from "@/lib/i18n/server";
import { LoginForm } from "./login-form";

export default async function LoginPage() {
  const t = await getT();
  const steps = [["01", t("login.s1k"), t("login.s1t"), t("login.s1s")], ["02", t("login.s2k"), t("login.s2t"), t("login.s2s")], ["03", t("login.s3k"), t("login.s3t"), t("login.s3s")]];
  const facts = [[t("login.outputsK"), t("login.outputsV")], [t("login.videoK"), t("login.videoV")], [t("login.liveK"), t("login.liveV")]];
  return (
    <div className="grid min-h-screen lg:grid-cols-[560px_1fr]">
      <div className="flex flex-col bg-background px-16 py-14">
        <div className="flex items-center justify-between"><Logo size={24} /><LangSwitch /></div>
        <div className="my-auto max-w-[432px]">
          <h1 className="text-[28px] font-semibold tracking-tight">{t("login.signInToEyeai")}</h1>
          <p className="mt-2 mb-8 text-sm text-text-2">{t("login.useYourWorkAccountTo")}</p>
          <Suspense><LoginForm /></Suspense>
          <p className="mt-6 text-[13px] text-text-2">{t("login.noAccountYetAskYour")}</p>
        </div>
        <p className="font-mono text-[11px] text-text-3">{t("login.2026Eyeai")}</p>
      </div>
      <div className="hidden flex-col border-l border-border bg-surface px-16 py-14 lg:flex">
        <p className="caps !text-brand">{t("login.eventFirstOperationsConsole")}</p>
        <h2 className="mt-5 max-w-lg text-6xl leading-[1.05] font-semibold tracking-tight">{t("login.fromMotion")}<br />{t("login.toDecision")}</h2>
        <ol className="my-auto space-y-8">
          {steps.map(([n, k, title, s]) => (
            <li key={n} className="flex gap-5">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-sm border border-border-strong font-mono text-xs">{n}</span>
              <div><p className="caps">{k}</p><p className="mt-1 text-lg">{title}</p><p className="mt-1 font-mono text-[11px] text-text-3">{s}</p></div>
            </li>
          ))}
        </ol>
        <dl className="grid grid-cols-3 gap-6 border-t border-border pt-6">
          {facts.map(([k, v]) => (
            <div key={k}><dt className="caps">{k}</dt><dd className="mt-1 font-medium">{v}</dd></div>
          ))}
        </dl>
      </div>
    </div>
  );
}
