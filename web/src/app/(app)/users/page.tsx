"use client";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { PageHeader, Panel } from "@/components/console/page-header";
import { StatusChip } from "@/components/console/status-chip";
import { ErrorState, LoadingList } from "@/components/console/states";
import { api, revalidate, useApi } from "@/hooks/use-api";
import type { UserRow } from "@/lib/types";
import { useFmt, useT } from "@/lib/i18n/client";

export default function UsersPage() {
  const t = useT();
  const fmt = useFmt();
  const { data, error, loading, refresh } = useApi<UserRow[]>("/api/users", 15000);
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ name: "", email: "", password: "", role: "security" });
  const [err, setErr] = useState<string | null>(null);
  if (loading) return <LoadingList />;
  if (!data) return <ErrorState onRetry={() => refresh()} message={error?.message} />;
  async function create(e: React.FormEvent) {
    e.preventDefault(); setErr(null);
    try { await api("/api/users", { method: "POST", json: f }); toast.success(t("users.toastAdded", { name: f.name })); setOpen(false); setF({ name: "", email: "", password: "", role: "security" }); await revalidate("/api/users"); }
    catch (x) { setErr((x as Error).message); }
  }
  return (
    <>
      <PageHeader title={t("users.users")} subtitle={t("users.adminRegistersStreamsAndUsers")} actions={<Button onClick={() => setOpen(true)}>{t("users.addUser")}</Button>} />
      <div className="px-8 pb-10"><Panel>
        <table className="w-full text-sm">
          <thead><tr className="border-b border-border text-left"><th className="caps px-5 py-3 font-normal">{t("users.name")}</th><th className="caps py-3 font-normal">{t("users.email")}</th><th className="caps py-3 font-normal">{t("users.role")}</th><th className="caps py-3 pr-5 font-normal">{t("users.created")}</th></tr></thead>
          <tbody>{data.map((u) => (
            <tr key={u.id} className="border-b border-border last:border-0"><td className="px-5 py-3 font-medium">{u.name}</td><td className="font-mono text-xs">{u.email}</td>
              <td><StatusChip tone={u.role === "admin" ? "accent" : "neutral"}>{t(u.role === "admin" ? "users.roleAdmin" : "users.roleSecurity")}</StatusChip></td><td className="pr-5 font-mono text-xs text-text-2">{fmt.date(u.created_at)}</td></tr>))}</tbody>
        </table></Panel></div>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-md rounded-lg border-border bg-surface-2">
          <form onSubmit={create} className="space-y-4" noValidate>
            <DialogHeader><DialogTitle>{t("users.addUser")}</DialogTitle><DialogDescription>{t("users.theySignInWithThis")}</DialogDescription></DialogHeader>
            {err && <p role="alert" className="rounded-sm border border-sev-critical/40 bg-sev-critical/10 px-3 py-2 text-sm text-sev-critical">{err}</p>}
            {(["name", "email", "password"] as const).map((k) => (
              <div key={k} className="space-y-2"><Label htmlFor={`u-${k}`} className="capitalize">{k}</Label>
                <Input id={`u-${k}`} type={k === "password" ? "password" : k} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} className="border-border-strong bg-background" /></div>))}
            <div className="space-y-2"><Label>{t("users.role")}</Label>
              <Select value={f.role} onValueChange={(role) => setF({ ...f, role })}><SelectTrigger className="border-border-strong bg-background"><SelectValue /></SelectTrigger>
                <SelectContent className="border-border-strong bg-surface-2"><SelectItem value="security">{t("users.security")}</SelectItem><SelectItem value="admin">{t("users.admin")}</SelectItem></SelectContent></Select></div>
            <DialogFooter><Button type="button" variant="ghost" onClick={() => setOpen(false)}>{t("users.cancel")}</Button><Button type="submit" disabled={!f.name || !f.email || f.password.length < 8}>{t("users.addUser")}</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
