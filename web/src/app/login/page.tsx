import { Suspense } from "react";
import { Logo } from "@/components/console/logo";
import { LoginForm } from "./login-form";

const steps = [
  ["01", "LIVE CAMERA", "An admin adds a live camera.", "Watched continuously · no raw video is uploaded"],
  ["02", "LIVE DETECTION", "Spots people and vehicles in each picture.", "Boxes shown live · followed from picture to picture"],
  ["03", "TRIAGE", "Security confirms or dismisses, with evidence attached.", "Every decision recorded in the audit log"],
];

export default function LoginPage() {
  return (
    <div className="grid min-h-screen lg:grid-cols-[560px_1fr]">
      <div className="flex flex-col bg-background px-16 py-14">
        <Logo size={24} />
        <div className="my-auto max-w-[432px]">
          <h1 className="text-[28px] font-semibold tracking-tight">Sign in to EyeAI</h1>
          <p className="mt-2 mb-8 text-sm text-text-2">Use your work account to open your sites and review incidents.</p>
          <Suspense><LoginForm /></Suspense>
          <p className="mt-6 text-[13px] text-text-2">No account yet? Ask your workspace administrator for an invite.</p>
        </div>
        <p className="font-mono text-[11px] text-text-3">© 2026 EyeAI</p>
      </div>
      <div className="hidden flex-col border-l border-border bg-surface px-16 py-14 lg:flex">
        <p className="caps !text-brand">Event-first operations console</p>
        <h2 className="mt-5 max-w-lg text-6xl leading-[1.05] font-semibold tracking-tight">From motion<br />to decision.</h2>
        <ol className="my-auto space-y-8">
          {steps.map(([n, k, t, s]) => (
            <li key={n} className="flex gap-5">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-sm border border-border-strong font-mono text-xs">{n}</span>
              <div><p className="caps">{k}</p><p className="mt-1 text-lg">{t}</p><p className="mt-1 font-mono text-[11px] text-text-3">{s}</p></div>
            </li>
          ))}
        </ol>
        <dl className="grid grid-cols-3 gap-6 border-t border-border pt-6">
          {[["Outputs", "Only the alerts you switch on, always logged"], ["Video leaving site", "Photos only"], ["Live view", "Boxes drawn live"]].map(([k, v]) => (
            <div key={k}><dt className="caps">{k}</dt><dd className="mt-1 font-medium">{v}</dd></div>
          ))}
        </dl>
      </div>
    </div>
  );
}
