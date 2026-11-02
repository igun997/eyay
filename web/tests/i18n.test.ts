import { describe, expect, it } from "vitest";
import {
  DEFAULT_LANG, LANGS, LANG_COOKIE, catalogOf, fmtAgo, fmtDateTime, fmtNumber, langCookie, langFromCookieHeader, makeT, normaliseLang, setLangCookie, translate,
} from "@/lib/i18n";
import { MESSAGES } from "@/lib/i18n/messages";
import { UI } from "@/lib/i18n/messages-ui";
import { CORE } from "@/lib/i18n/messages-core";
import { issueText } from "@/lib/i18n/validation";
import { automationInput, connectionInput } from "@/lib/automations";
import { reviewInput, sourceInput } from "@/lib/schemas";

const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

describe("the two catalogs (id, en)", () => {
  const id = catalogOf("id"), en = catalogOf("en");
  it("have exactly the same keys", () => expect(Object.keys(id).sort()).toEqual(Object.keys(en).sort()));
  it("have no empty values", () => {
    for (const lang of ["id", "en"] as const) {
      const empty = Object.entries(catalogOf(lang)).filter(([, v]) => typeof v !== "string" || v.trim() === "").map(([k]) => k);
      expect(empty, `empty ${lang} values`).toEqual([]);
    }
  });
  it("use the same {placeholders} in both languages", () => {
    const bad = Object.keys(id).filter((k) => JSON.stringify(placeholders(id[k])) !== JSON.stringify(placeholders(en[k])));
    expect(bad).toEqual([]);
  });
  it("never define the same key twice (the hand-written file must not shadow the generated one)", () => {
    expect(Object.keys(UI).filter((k) => k in CORE)).toEqual([]);
    expect(Object.keys(MESSAGES).length).toBe(Object.keys(UI).length + Object.keys(CORE).length);
  });
  it("are really translated: most Indonesian texts differ from the English ones", () => {
    const same = Object.keys(id).filter((k) => id[k] === en[k]).length;
    expect(same / Object.keys(id).length).toBeLessThan(0.2);
  });
  it("cover every enum the UI builds keys for at run time", () => {
    const need = [
      ...["CRITICAL", "HIGH", "MEDIUM", "LOW"].flatMap((s) => [`sev.${s}`, `desc.sev.${s}`]),
      ...["person", "car", "truck", "bus", "motorcycle", "bicycle", "other", "vehicle", "false_alarm"].map((l) => `label.${l}`),
      ...["person", "car", "truck", "bus", "motorcycle", "bicycle"].map((l) => `desc.label.${l}`),
      ...["person", "vehicle", "false_alarm", "other"].map((l) => `desc.ai.${l}`),
      ...["open", "acknowledged", "resolved"].map((s) => `incidentStatus.${s}`),
      ...["received", "queued", "classifying", "ready_for_triage", "ready", "confirmed", "dismissed", "failed", "skipped"].map((s) => `pipeline.${s}`),
      ...["online", "connecting", "pending", "offline", "error"].map((s) => `health.${s}`),
      ...["camera", "triage", "operator", "note", "system"].map((s) => `kind.${s}`),
      ...["low", "medium", "high"].flatMap((s) => [`sensitivity.${s}`, `sensitivity.help.${s}`]),
      ...["knife", "scissors", "baseball bat", "bottle", "backpack", "handbag", "suitcase", "cell phone", "umbrella"].map((o) => `object.${o}`),
      ...["webhook", "mqtt", "modbus"].map((k) => `alerts.sentAs.${k}`),
      ...["sent", "simulated", "failed", "skipped", "queued", "running"].filter(() => true).map(() => "runResult.close"),
    ];
    expect(need.filter((k) => !(k in id))).toEqual([]);
  });
  it("write the Indonesian terms the product uses", () => {
    const t = makeT("id");
    expect(t("sev.CRITICAL")).toBe("Kritis"); expect(t("sev.HIGH")).toBe("Tinggi"); expect(t("sev.MEDIUM")).toBe("Sedang"); expect(t("sev.LOW")).toBe("Rendah");
    expect(["person", "car", "truck", "bus", "motorcycle", "bicycle", "other"].map((l) => t(`label.${l}` as never))).toEqual(["Orang", "Mobil", "Truk", "Bus", "Sepeda motor", "Sepeda", "Objek lain"]);
    expect(t("nav.alerts")).toBe("Peringatan"); expect(t("nav.triage")).toBe("Tinjauan"); expect(t("nav.cameraSetup")).toBe("Pengaturan kamera");
    expect(t("alerts.live2")).toBe("Aktif (Live)"); expect(t("alerts.practice2")).toBe("Latihan");
    expect(t("overview.secondAiCheck")).toBe("Pemeriksaan AI kedua");
  });
});

describe("language choice", () => {
  it("defaults to Indonesian and only accepts id | en", () => {
    expect(DEFAULT_LANG).toBe("id");
    expect(LANGS).toEqual(["id", "en"]);
    expect(normaliseLang(undefined)).toBe("id"); expect(normaliseLang("fr")).toBe("id"); expect(normaliseLang("en")).toBe("en");
    expect(langFromCookieHeader(null)).toBe("id");
    expect(langFromCookieHeader("a=1; b=2")).toBe("id");
    expect(translate(DEFAULT_LANG, "nav.alerts")).toBe("Peringatan");
  });
  it("switching sets the cookie: eyeai_lang, 1 year, SameSite=Lax, whole site", () => {
    const doc = { cookie: "" };
    setLangCookie("en", doc);
    expect(doc.cookie).toBe(langCookie("en"));
    for (const part of [`${LANG_COOKIE}=en`, "Max-Age=31536000", "SameSite=Lax", "Path=/"]) expect(doc.cookie).toContain(part);
    expect(langFromCookieHeader(`theme=dark; ${doc.cookie.split(";")[0]}; other=1`)).toBe("en");
    setLangCookie("id", doc);
    expect(langFromCookieHeader(doc.cookie.split(";")[0])).toBe("id");
  });
  it("a key that is not in the catalog passes through, and {params} are filled in", () => {
    expect(translate("en", "Some raw message")).toBe("Some raw message");
    expect(makeT("id")("alerts.deleteQ", { name: "Malam" })).toBe("Hapus “Malam”?");
    expect(makeT("en")("alerts.deleteQ", { name: "Night" })).toBe("Delete “Night”?");
  });
});

describe("dates, times and numbers follow the language", () => {
  it("numbers use the language's separators", () => {
    expect(fmtNumber("id", 0.45)).toBe("0,45");
    expect(fmtNumber("en", 0.45)).toBe("0.45");
    expect(fmtNumber("id", 1234.5, 1)).toBe("1.234,5");
  });
  it("date and time are formatted per locale", () => {
    const iso = "2026-11-02T10:30:00Z";
    expect(fmtDateTime("id", iso)).toMatch(/Nov/);
    expect(fmtDateTime("en", iso)).toMatch(/Nov/);
    expect(fmtDateTime("id", iso)).not.toBe(fmtDateTime("en", iso));
  });
  it("relative times", () => {
    const now = Date.parse("2026-11-02T12:00:00Z");
    expect(fmtAgo("id", "2026-11-02T11:50:00Z", now)).toMatch(/10 m/);
    expect(fmtAgo("en", "2026-11-02T11:50:00Z", now)).toMatch(/10 min/);
  });
});

describe("validation messages are localised", () => {
  const first = (r: { success: boolean; error?: { issues: never[] } }) => (r.error!.issues as never[])[0];
  it("custom schema messages are catalog keys, shown in the user's language", () => {
    const r = sourceInput.safeParse({ code: "a b", name: "x", url: "rtsp://h/s", labels: ["person"] });
    expect(r.success).toBe(false);
    const issue = r.error!.issues.find((i) => i.path[0] === "code")!;
    expect(issueText(makeT("id"), issue)).toBe("Hanya huruf, angka, - dan _");
    expect(issueText(makeT("en"), issue)).toBe("Letters, digits, - and _ only");
  });
  it("zod's own messages are mapped by code", () => {
    const issue = first(automationInput.safeParse({ name: "", actions: [{ type: "webhook", connection: "9ea0bb5d-faa9-4b16-a51d-d9579ba64b39" }] }) as never);
    expect(issueText(makeT("id"), issue)).toBe("Minimal 1");
    expect(issueText(makeT("en"), issue)).toBe("Must be at least 1");
    const unknownKey = first(connectionInput.safeParse({ name: "a", kind: "webhook", nope: 1 }) as never);
    expect(issueText(makeT("en"), unknownKey)).toBe("That setting does not exist");
    expect(issueText(makeT("id"), first(reviewInput.safeParse({ decision: "dismiss", reason: "x" }) as never))).toBe("Pilihan tidak dikenal");
  });
});
