"use client";
import { useState } from "react";
import { toast } from "sonner";
import { Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/hooks/use-api";
import { SECRET_MASK, parseConnectionConfig, type ConnectionKind } from "@/lib/automations";
import type { ConnectionRow } from "@/lib/server/automations";
import { useT } from "@/lib/i18n/client";
import { K, type MessageKey } from "@/lib/i18n";

export const KIND_TEXT: Record<ConnectionKind, { name: MessageKey; help: MessageKey }> = {
  webhook: { name: K("runResult.webService"), help: K("connectionDialog.sendsAMessageToAn") },
  mqtt: { name: K("connection.mqtt"), help: K("connectionDialog.publishesAMessageThatOther") },
  modbus: { name: K("connection.modbus"), help: K("connectionDialog.switchesAnOutputOnFor") },
};

const num = "h-9 border-border-strong bg-background font-mono";
type Header = { k: string; v: string; keep: boolean };
const str = (v: unknown, d = "") => (typeof v === "string" ? v : d);
const n = (v: unknown, d: number) => (typeof v === "number" ? v : d);

/** Where alerts go. Secrets are never shown: a field that is already set says so, and typing replaces it. */
export function ConnectionDialog({ open, onOpenChange, connection, onSaved }: { open: boolean; onOpenChange: (o: boolean) => void; connection?: ConnectionRow; onSaved: () => void }) {
  const t = useT();
  const c = (connection?.config ?? {}) as Record<string, unknown>;
  const [kind, setKind] = useState<ConnectionKind>(connection?.kind ?? "webhook");
  const [name, setName] = useState(connection?.name ?? "");
  const [enabled, setEnabled] = useState(connection?.enabled ?? true);
  const [timeoutS, setTimeoutS] = useState(String(n(c.timeout_s, 5)));
  // web service
  const [url, setUrl] = useState(str(c.url));
  const [method, setMethod] = useState<"POST" | "PUT">(c.method === "PUT" ? "PUT" : "POST");
  const [headers, setHeaders] = useState<Header[]>(Object.entries((c.headers ?? {}) as Record<string, string>).map(([k, v]) => ({ k, v: v === SECRET_MASK ? "" : v, keep: v === SECRET_MASK })));
  const [secret, setSecret] = useState<string | null>(null); // null = untouched
  // broker / PLC
  const [host, setHost] = useState(str(c.host));
  const [port, setPort] = useState(String(n(c.port, kind === "modbus" ? 502 : 1883)));
  const [username, setUsername] = useState(str(c.username));
  const [password, setPassword] = useState<string | null>(null);
  const [clientId, setClientId] = useState(str(c.client_id, "eyeai"));
  const [unitId, setUnitId] = useState(String(n(c.unit_id, 1)));
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const secretSet = (key: string) => c[key] === SECRET_MASK;

  const config = (): Record<string, unknown> => {
    const base = { timeout_s: Number(timeoutS) };
    if (kind === "webhook") {
      const h = Object.fromEntries(headers.filter((x) => x.k.trim()).map((x) => [x.k.trim(), x.keep ? SECRET_MASK : x.v]));
      return { ...base, url, method, headers: h, ...(secret !== null ? { secret } : {}) };
    }
    if (kind === "mqtt") return { ...base, host, port: Number(port), username, client_id: clientId, ...(password !== null ? { password } : {}) };
    return { ...base, host, port: Number(port), unit_id: Number(unitId) };
  };
  const parsed = parseConnectionConfig(kind, config(), t);
  const problems = [...(!name.trim() ? [t("connectionDialog.giveName")] : []), ...(parsed.ok ? [] : parsed.errors)];

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (problems.length) { setErr(problems.join("; ")); return; }
    setBusy(true); setErr(null);
    try {
      if (connection) await api(`/api/connections/${connection.id}`, { method: "PUT", json: { name, enabled, config: config() } });
      else await api("/api/connections", { method: "POST", json: { name, kind, enabled, config: config() } });
      toast.success(connection ? t("common.savedToast", { name }) : t("common.addedToast", { name }), { description: t("connectionDialog.useCheckConnectionToMake") });
      onSaved(); onOpenChange(false);
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  }

  const SecretField = ({ id, label, current, set, value }: { id: string; label: string; current: boolean; set: (v: string | null) => void; value: string | null }) => (
    <div className="space-y-2"><Label htmlFor={id}>{label}</Label>
      <div className="flex gap-2">
        <Input id={id} type="password" autoComplete="new-password" value={value ?? ""} onChange={(e) => set(e.target.value)} placeholder={current && value === null ? t("connectionDialog.setTypeToReplaceIt") : t("connectionDialog.notSet")} className={num} />
        {current && <Button type="button" variant="ghost" onClick={() => set(value === "" ? null : "")}>{value === "" ? t("connectionDialog.keepIt") : t("connectionDialog.remove")}</Button>}
      </div>
      {value === "" && current && <p className="text-xs text-text-3">{t("connectionDialog.itWillBeRemovedWhen")}</p>}</div>
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto rounded-lg border-border bg-surface-2 sm:max-w-xl">
        <form onSubmit={save} className="space-y-4" noValidate>
          <DialogHeader>
            <DialogTitle>{connection ? t("common.editName", { name: connection.name }) : t("connectionDialog.addAPlaceToSend")}</DialogTitle>
            <DialogDescription>{t(KIND_TEXT[kind].help)} {t("connectionDialog.passwordsAndSecretsAreStored")}</DialogDescription>
          </DialogHeader>
          {err && <p role="alert" className="rounded-sm border border-sev-critical/40 bg-sev-critical/10 px-3 py-2 text-sm text-sev-critical">{err}</p>}
          <div className="grid grid-cols-[1fr_1fr] gap-3">
            <div className="space-y-2"><Label htmlFor="cn-name">{t("connectionDialog.name")}</Label><Input id="cn-name" value={name} onChange={(e) => setName(e.target.value)} placeholder={t("connectionDialog.plantControlRoom")} className="border-border-strong bg-background" /></div>
            <div className="space-y-2"><Label htmlFor="cn-kind">{t("connectionDialog.type")}</Label>
              <select id="cn-kind" value={kind} disabled={!!connection} onChange={(e) => { const k = e.target.value as ConnectionKind; setKind(k); setPort(k === "modbus" ? "502" : "1883"); }} className="h-9 w-full rounded-sm border border-border-strong bg-background px-2 text-sm">
                {(Object.keys(KIND_TEXT) as ConnectionKind[]).map((k) => <option key={k} value={k}>{t(KIND_TEXT[k].name)}</option>)}</select></div>
          </div>

          {kind === "webhook" && (
            <>
              <div className="grid grid-cols-[1fr_110px] gap-3">
                <div className="space-y-2"><Label htmlFor="cn-url">{t("connectionDialog.addressUrl")}</Label><Input id="cn-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/hooks/eyeai" className={num} /></div>
                <div className="space-y-2"><Label htmlFor="cn-method">{t("connectionDialog.method")}</Label><select id="cn-method" value={method} onChange={(e) => setMethod(e.target.value as "POST" | "PUT")} className="h-9 w-full rounded-sm border border-border-strong bg-background px-2 text-sm"><option>{t("connectionDialog.post")}</option><option>{t("connectionDialog.put")}</option></select></div>
              </div>
              <SecretField id="cn-secret" label={t("connectionDialog.signingSecretOptional")} current={secretSet("secret")} value={secret} set={setSecret} />
              <p className="-mt-2 text-xs text-text-3">{t("connectionDialog.whenSetEveryMessageIs")}</p>
              <div className="space-y-2"><p className="text-sm font-medium">{t("connectionDialog.extraHeadersOptional")}</p>
                {headers.map((h, i) => (
                  <div key={i} className="flex gap-2">
                    <Input aria-label={t("connectionDialog.headerName")} value={h.k} onChange={(e) => setHeaders(headers.map((x, j) => (j === i ? { ...x, k: e.target.value } : x)))} placeholder={t("connectionDialog.xApiKey")} className={num} />
                    <Input aria-label={t("connectionDialog.headerValue")} type="password" autoComplete="new-password" value={h.v} onChange={(e) => setHeaders(headers.map((x, j) => (j === i ? { ...x, v: e.target.value, keep: false } : x)))} placeholder={h.keep ? t("connectionDialog.setTypeToReplaceIt") : t("connectionDialog.value")} className={num} />
                    <Button type="button" variant="ghost" size="icon" aria-label={t("connectionDialog.removeHeader")} onClick={() => setHeaders(headers.filter((_, j) => j !== i))}><X className="size-4" aria-hidden /></Button>
                  </div>
                ))}
                <Button type="button" variant="outline" size="sm" onClick={() => setHeaders([...headers, { k: "", v: "", keep: false }])}><Plus className="size-3.5" aria-hidden /> {t("connectionDialog.addAHeader")}</Button></div>
            </>
          )}

          {kind !== "webhook" && (
            <div className="grid grid-cols-[1fr_110px] gap-3">
              <div className="space-y-2"><Label htmlFor="cn-host">{kind === "mqtt" ? t("connectionDialog.brokerAddress") : t("connectionDialog.plcAddress")}</Label><Input id="cn-host" value={host} onChange={(e) => setHost(e.target.value)} placeholder={kind === "mqtt" ? "broker.local" : "192.168.1.20"} className={num} /></div>
              <div className="space-y-2"><Label htmlFor="cn-port">{t("connectionDialog.port")}</Label><Input id="cn-port" type="number" min={1} max={65535} value={port} onChange={(e) => setPort(e.target.value)} className={num} /></div>
            </div>
          )}
          {kind === "mqtt" && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-2"><Label htmlFor="cn-user">{t("connectionDialog.userNameOptional")}</Label><Input id="cn-user" value={username} onChange={(e) => setUsername(e.target.value)} className={num} /></div>
                <div className="space-y-2"><Label htmlFor="cn-client">{t("connectionDialog.clientName")}</Label><Input id="cn-client" value={clientId} onChange={(e) => setClientId(e.target.value)} className={num} /></div>
              </div>
              <SecretField id="cn-pass" label={t("connectionDialog.passwordOptional")} current={secretSet("password")} value={password} set={setPassword} />
              <p className="-mt-2 text-xs text-text-3">{t("connectionDialog.plainMqtt311")}</p>
            </>
          )}
          {kind === "modbus" && <div className="space-y-2"><Label htmlFor="cn-unit">{t("connectionDialog.unitNumber0To255")}</Label><Input id="cn-unit" type="number" min={0} max={255} value={unitId} onChange={(e) => setUnitId(e.target.value)} className={num} /></div>}

          <div className="grid grid-cols-[1fr_auto] items-end gap-3">
            <div className="space-y-2"><Label htmlFor="cn-timeout">{t("connectionDialog.giveUpAfterSeconds")}</Label><Input id="cn-timeout" type="number" min={1} max={30} value={timeoutS} onChange={(e) => setTimeoutS(e.target.value)} className={num} /></div>
            <div className="flex items-center gap-3 pb-2 text-sm"><Label htmlFor="cn-enabled" className="font-normal">{t("connectionDialog.switchedOn")}</Label><Switch id="cn-enabled" checked={enabled} onCheckedChange={setEnabled} /></div>
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t("connectionDialog.cancel")}</Button>
            <Button type="submit" disabled={busy || problems.length > 0}>{connection ? t("connectionDialog.saveChanges") : t("connectionDialog.addConnection")}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
