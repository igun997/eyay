import { describe, expect, it } from "vitest";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadMigrations, plan } from "../scripts/migrations-lib";

const dirWith = (files: Record<string, string>) => {
  const d = mkdtempSync(join(tmpdir(), "mig-"));
  for (const [n, s] of Object.entries(files)) writeFileSync(join(d, n), s);
  return d;
};

describe("migrations", () => {
  it("loads in filename order and ignores non-sql files", () => {
    const m = loadMigrations(dirWith({ "002_b.sql": "select 2", "001_a.sql": "select 1", "README.md": "x" }));
    expect(m.map((x) => x.name)).toEqual(["001_a.sql", "002_b.sql"]);
  });
  it("rejects badly named files", () => expect(() => loadMigrations(dirWith({ "init.sql": "x" }))).toThrow(/bad migration name/));
  it("the repo's real migrations are valid and ordered", () => {
    const m = loadMigrations(join(__dirname, "..", "..", "db", "migrations"));
    expect(m.length).toBeGreaterThanOrEqual(3);
    expect(m.map((x) => x.name)).toEqual([...m.map((x) => x.name)].sort());
  });
  it("plans only what is pending", () => {
    const all = loadMigrations(dirWith({ "001_a.sql": "a", "002_b.sql": "b" }));
    expect(plan(all, new Map()).map((m) => m.name)).toEqual(["001_a.sql", "002_b.sql"]);
    expect(plan(all, new Map([["001_a.sql", all[0].checksum]])).map((m) => m.name)).toEqual(["002_b.sql"]);
    expect(plan(all, new Map([["001_a.sql", all[0].checksum], ["002_b.sql", all[1].checksum]]))).toEqual([]);
  });
  it("refuses a migration edited after it was applied, but adopts legacy rows without a checksum", () => {
    const all = loadMigrations(dirWith({ "001_a.sql": "a" }));
    expect(() => plan(all, new Map([["001_a.sql", "deadbeef"]]))).toThrow(/modified after it was applied/);
    expect(plan(all, new Map([["001_a.sql", null]]))).toEqual([]);
  });
});
