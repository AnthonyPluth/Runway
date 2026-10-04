// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";

vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }) }));

import { AddForm } from "./addForm.svelte";

type Key = "rates" | "bonus";
const SECTIONS: [Key, RegExp][] = [["bonus", /bonus|spend/i], ["rates", /rate|earning/i]];
const make = (bad: Record<string, string> = {}) => new AddForm<Key>(() => bad, SECTIONS);

describe("AddForm", () => {
  it("shows no notes until the first Add, then the fields that need fixing, and sends nothing", async () => {
    const f = make({ name: "Enter a name." }), send = vi.fn();
    expect(f.errors).toEqual({});
    await f.add(send, null);
    expect(f.errors).toEqual({ name: "Enter a name." });
    expect(send).not.toHaveBeenCalled();
    expect(f.busy).toBe(false);
  });

  it("sends once the fields are right, and is busy while it does", async () => {
    const f = make();
    let release!: () => void;
    const sending = f.add(() => new Promise<void>((r) => { release = r; }), null);
    await vi.waitFor(() => expect(f.busy).toBe(true));
    await f.add(vi.fn(), null);
    release();
    await sending;
    expect(f.busy).toBe(false);
    expect(f.error).toBe("");
  });

  it("puts a refusal in the section its words belong to, and opens it", async () => {
    const f = make();
    await f.add(async () => { throw new Error("The bonus spend can’t be negative."); }, null);
    expect(f.error).toBe("The bonus spend can’t be negative.");
    expect(f.flagged).toBe("bonus");
    expect(f.open.bonus).toBe(true);
    expect(f.open.rates).toBe(false);
    expect(f.busy).toBe(false);
  });

  it("clears the last refusal on the next Add, and keeps a refusal that names no section to the footer", async () => {
    const f = make();
    await f.add(async () => { throw new Error("Earning rates are listed twice."); }, null);
    expect(f.flagged).toBe("rates");
    await f.add(async () => { throw new Error("Something unrelated."); }, null);
    expect(f.flagged).toBeNull();
    expect(f.error).toBe("Something unrelated.");
    await f.add(async () => {}, null);
    expect(f.error).toBe("");
  });

  it("works without sections", async () => {
    const f = new AddForm(() => ({}));
    await f.add(async () => { throw new Error("No."); }, null);
    expect(f.error).toBe("No.");
    expect(f.flagged).toBeNull();
  });
});
