// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Report, monthTick } from "./chart.svelte";
import { catFilter, channels, contrast, dayBefore, drill, INK_DARK, INK_LIGHT, textOn } from "./look";
import { monthsOptions, rangeDates, rangeOptions } from "./state.svelte";

describe("monthTick", () => {
  it("shows the short month, with the year to mark where one starts", () => {
    expect(monthTick("2026-09")).toBe("Sep");
    expect(monthTick("2026-01", true)).toBe("Jan 26");
  });
});

describe("report ranges", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date("2026-05-17T12:00:00")); });
  afterEach(() => vi.useRealTimers());

  it("ends at the start of next month and reaches back the right number of months", () => {
    expect(rangeDates("1m")).toEqual({ start: "2026-05-01", end: "2026-06-01" });
    expect(rangeDates("3m")).toEqual({ start: "2026-03-01", end: "2026-06-01" });
    expect(rangeDates("12m")).toEqual({ start: "2025-06-01", end: "2026-06-01" });
    expect(rangeDates("ytd")).toEqual({ start: "2026-01-01", end: "2026-06-01" });
  });

  it("rolls into the next year in December", () => {
    vi.setSystemTime(new Date("2026-12-31T20:00:00"));
    expect(rangeDates("1m")).toEqual({ start: "2026-12-01", end: "2027-01-01" });
  });

  it("offers each range and month count as select options", () => {
    expect(rangeOptions.map((o) => o.value)).toEqual(["1m", "3m", "12m", "ytd"]);
    expect(monthsOptions).toContainEqual({ value: "12", label: "12 months" });
  });
});

describe("Report", () => {
  const deferred = <T>() => { let resolve!: (v: T) => void, reject!: (e: Error) => void; const p = new Promise<T>((a, b) => { resolve = a; reject = b; }); return { p, resolve, reject }; };

  it("loads on creation and keeps the last answer on screen while the next loads", async () => {
    const calls = [deferred<string>(), deferred<string>()];
    let n = 0;
    const r = new Report(() => calls[n++].p);
    calls[0].resolve("first");
    await calls[0].p;
    expect(r.data).toBe("first");
    r.load();
    expect(r.data).toBe("first");
    calls[1].resolve("second");
    await calls[1].p;
    expect(r.data).toBe("second");
  });

  it("is loading while an answer is out, and not once the latest one is in or has failed", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const calls = [deferred<string>(), deferred<string>(), deferred<string>()];
    let n = 0;
    const r = new Report(() => calls[n++].p);
    expect(r.loading).toBe(true);
    r.load();
    calls[0].resolve("old");
    await calls[0].p;
    expect(r.loading).toBe(true);   // the newer one is still out
    calls[1].resolve("new");
    await calls[1].p;
    expect(r.loading).toBe(false);
    r.load();
    calls[2].reject(new Error("nope"));
    await calls[2].p.catch(() => {});
    expect(r.loading).toBe(false);
    expect(r.data).toBe("new");
  });

  it("records a fetcher that throws before it answers as a failure", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const r = new Report<string>(() => { throw new Error("bad url"); });
    await Promise.resolve(); await Promise.resolve();
    expect(r.error?.message).toBe("bad url");
    expect(r.loading).toBe(false);
  });

  it("ignores a slow old answer that arrives after a newer one", async () => {
    const calls = [deferred<string>(), deferred<string>()];
    let n = 0;
    const r = new Report(() => calls[n++].p);
    r.load();
    calls[1].resolve("new");
    await calls[1].p;
    calls[0].resolve("old");
    await calls[0].p;
    expect(r.data).toBe("new");
  });

  it("records a failure and clears it when a later load works", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const bad = deferred<string>(), good = deferred<string>();
    const answers = [bad, good];
    let n = 0;
    const r = new Report(() => answers[n++].p);
    bad.reject(new Error("nope"));
    await bad.p.catch(() => {});
    expect(r.error?.message).toBe("nope");
    r.load();
    good.resolve("ok");
    await good.p;
    expect(r.error).toBeNull();
    expect(r.data).toBe("ok");
  });
});

describe("report colors and links", () => {
  it("reads a color's channels", () => {
    expect(channels("#3987e5")).toEqual([57, 135, 229]);
    expect(channels("#fff")).toEqual([255, 255, 255]);
    expect(channels("rgb(0 131 0)")).toEqual([0, 131, 0]);
    expect(channels("rgba(1, 2, 3, 0.5)")).toEqual([1, 2, 3]);
    expect(channels("nonsense")).toBeNull();
  });

  it("picks white on dark fills and dark ink on light ones, always at 4.5:1 or better", () => {
    expect(textOn("#008300")).toBe(INK_LIGHT);
    expect(textOn("#6b6a66")).toBe(INK_LIGHT);
    expect(textOn("#c98500")).toBe(INK_DARK);
    expect(textOn("#3987e5")).toBe(INK_DARK);
    for (const c of ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767", "#1c9aa8", "#8a8a86", "#6b6a66"]) {
      const ink = channels(textOn(c))!;
      expect(contrast(channels(c)!, ink)).toBeGreaterThanOrEqual(4.5);
    }
    expect(textOn("var(--nowhere)")).toBe(INK_DARK);   // can't be read: dark ink
  });

  it("turns a report's exclusive end into Transactions' last day, across months and years", () => {
    expect(dayBefore("2026-10-01")).toBe("2026-09-30");
    expect(dayBefore("2027-01-01")).toBe("2026-12-31");
    expect(dayBefore("2026-03-09")).toBe("2026-03-08");   // the day the clocks change
  });

  it("asks Transactions for the accounts the reports count, and for no category as Uncategorized", () => {
    expect(drill({ month: "2026-09", kind: "out" })).toEqual({ scope: "budget", month: "2026-09", kind: "out" });
    expect(catFilter("Uncategorized")).toBe("__none__");
    expect(catFilter("Groceries")).toBe("Groceries");
  });
});
