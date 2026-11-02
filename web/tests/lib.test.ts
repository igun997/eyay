import { describe, expect, it } from "vitest";
import { kindOf, sourceInput, reviewInput } from "@/lib/schemas";
import { can } from "@/lib/roles";
import { fmtAgo } from "@/lib/i18n";
import { agreement } from "@/lib/classification";
import { maskCreds } from "@/lib/server/queries";

describe("kindOf", () => {
  it.each([
    ["rtsp://u:p@10.0.0.5:554/stream1", "rtsp"],
    ["RTSP://cam/live", "rtsp"],
    ["https://cdn.example.com/hls/live.m3u8", "m3u8"],
    ["http://h/x.m3u8?token=abc", "m3u8"],
    ["http://h/video.mp4", null],
    ["ftp://h/live.m3u8", null],
    ["not a url", null],
  ])("%s -> %s", (url, kind) => expect(kindOf(url)).toBe(kind));
});

describe("sourceInput", () => {
  const ok = { code: "cam-01", name: "Dock 2", url: "rtsp://h/s", labels: ["person"] };
  it("normalises code, infers kind, applies defaults", () => {
    const v = sourceInput.parse(ok);
    expect(v).toMatchObject({ code: "CAM-01", kind: "rtsp", min_confidence: 0.4, enabled: true });
  });
  it("rejects unsupported URLs, empty categories and bad codes", () => {
    expect(sourceInput.safeParse({ ...ok, url: "http://h/a.mp4" }).success).toBe(false);
    expect(sourceInput.safeParse({ ...ok, labels: [] }).success).toBe(false);
    expect(sourceInput.safeParse({ ...ok, code: "cam 1!" }).success).toBe(false);
  });
});

describe("reviewInput", () => {
  it("requires a reason to dismiss and only known reasons", () => {
    expect(reviewInput.safeParse({ decision: "dismiss" }).success).toBe(false);
    expect(reviewInput.safeParse({ decision: "dismiss", reason: "Because" }).success).toBe(false);
    expect(reviewInput.safeParse({ decision: "dismiss", reason: "Animal" }).success).toBe(true);
    expect(reviewInput.safeParse({ decision: "confirm" }).success).toBe(true);
  });
});

describe("roles", () => {
  it("only admin reaches sources and users; security reaches the rest", () => {
    for (const p of ["/sources", "/users", "/sources/x"]) {
      expect(can("admin", p)).toBe(true);
      expect(can("security", p)).toBe(false);
    }
    for (const p of ["/overview", "/triage", "/incidents/INC-0001", "/cameras", "/rules"]) expect(can("security", p)).toBe(true);
  });
  it("does not match on prefix lookalikes", () => expect(can("security", "/sources-health")).toBe(true));
});

describe("format + masking", () => {
  const now = Date.parse("2026-10-31T12:00:00Z");
  it("relative times follow the language and never say \"now ago\"", () => {
    const now = Date.parse("2026-10-31T12:00:00Z");
    expect(fmtAgo("en", "2026-10-31T11:59:58Z", now)).toBe("now");
    expect(fmtAgo("id", "2026-10-31T11:59:58Z", now)).toBe("baru saja");
    expect(fmtAgo("en", "2026-10-31T11:55:00Z", now)).toMatch(/5 min/);
    expect(fmtAgo("id", "2026-10-31T11:55:00Z", now)).toMatch(/5 mnt|5 menit/);
    expect(fmtAgo("en", null)).toBe("never");
    expect(fmtAgo("id", null)).toBe("belum pernah");
  });
  it("maskCreds hides the password but keeps user and host", () => {
    expect(maskCreds({ url: "rtsp://admin:s3cret@10.0.0.5/s" }).url).toBe("rtsp://admin:••••@10.0.0.5/s");
    expect(maskCreds({ url: "rtsp://10.0.0.5/s" }).url).toBe("rtsp://10.0.0.5/s");
  });
});

describe("classifier agreement", () => {
  it("compares the edge class with the classifier vocabulary", () => {
    expect(agreement("person", "person")).toBe("agree");
    expect(agreement("truck", "vehicle")).toBe("agree");
    expect(agreement("car", "false_alarm")).toBe("disagree");
    expect(agreement("person", "vehicle")).toBe("disagree");
  });
  it("is unknown without a classifier result or for classes it does not cover", () => {
    expect(agreement("person", null)).toBe("unknown");
    expect(agreement("bicycle", "person")).toBe("unknown");
  });
});
