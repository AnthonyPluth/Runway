import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { BANK_MODES, FAKES_FILE, bankMode, readFakes, setBankMode } from "./fakes.mjs";

describe("the stand-ins beside a demo server", () => {
  let dir;
  afterEach(() => { if (dir) rmSync(dir, { recursive: true, force: true }); dir = undefined; });

  it("are read from the server's folder, and a server without them has none", () => {
    dir = mkdtempSync(join(tmpdir(), "fakes-test-"));
    expect(readFakes(dir)).toBeNull();
    const fakes = { simplefin: { name: "sample-bank.invalid", address: "127.0.0.1", port: 40001, ca: join(dir, "fakes", "ca.pem") }, prices: { url: "http://127.0.0.1:40002/chart" } };
    writeFileSync(join(dir, FAKES_FILE), JSON.stringify(fakes));
    expect(readFakes(dir)).toEqual(fakes);
    writeFileSync(join(dir, FAKES_FILE), "{not json");
    expect(readFakes(dir)).toBeNull();
  });

  it("refuse a mode the bank doesn't have, before asking it", async () => {
    await expect(setBankMode({ simplefin: { name: "sample-bank.invalid", address: "127.0.0.1", port: 1, ca: "/nowhere" } }, "down"))
      .rejects.toThrow(`the bank's mode is one of ${BANK_MODES.join(", ")}, not down`);
  });

  it("say so when the demo server has no stand-in bank", async () => {
    await expect(setBankMode(null, "403")).rejects.toThrow("this demo server has no stand-in bank");
    await expect(bankMode({ prices: {} })).rejects.toThrow("this demo server has no stand-in bank");
  });
});
