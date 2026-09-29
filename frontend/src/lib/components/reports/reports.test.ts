// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Report, monthTick, niceTicks, scrub } from "./chart.svelte";
import { monthsOptions, rangeDates, rangeOptions } from "./state.svelte";

describe("niceTicks", () => {
  it("picks round steps that cover the range", () => {
    expect(niceTicks(0, 100)).toEqual([0, 20, 40, 60, 80, 100]);
    const t = niceTicks(-130, 480);
    expect(t[0]).toBeLessThanOrEqual(-130);
    expect(t.at(-1)).toBeGreaterThanOrEqual(480);
    expect(t).toContain(0);
  });

  it("gives a single tick, not NaN or a hang, when everything is one value", () => {
    expect(niceTicks(0, 0)).toEqual([0]);
    expect(niceTicks(50, 50)).toEqual([50]);
  });

  it("rounds away floating point dust in small steps", () => {
    expect(niceTicks(0, 0.3)).toEqual([0, 0.1, 0.2, 0.3, 0.4].slice(0, niceTicks(0, 0.3).length));
  });
});

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

describe("scrub", () => {
  const touch = (x: number, y: number) => ({ clientX: x, clientY: y });
  const fire = (el: Element, type: string, t: ReturnType<typeof touch>) => {
    const e = new Event(type, { cancelable: true }) as Event & { touches: unknown[] };
    e.touches = [t];
    el.dispatchEvent(e);
    return e;
  };

  it("follows a sideways drag and keeps the page from scrolling with it", () => {
    const el = document.createElement("div"), moves: number[] = [];
    scrub(el, (x) => moves.push(x));
    fire(el, "touchstart", touch(10, 10));
    const e = fire(el, "touchmove", touch(30, 12));
    expect(moves).toEqual([10, 30]);
    expect(e.defaultPrevented).toBe(true);
  });

  it("lets a mostly vertical drag scroll the page", () => {
    const el = document.createElement("div"), moves: number[] = [];
    scrub(el, (x) => moves.push(x));
    fire(el, "touchstart", touch(10, 10));
    const e = fire(el, "touchmove", touch(12, 60));
    expect(moves).toEqual([10]);
    expect(e.defaultPrevented).toBe(false);
  });

  it("stops listening when destroyed", () => {
    const el = document.createElement("div"), moves: number[] = [];
    const a = scrub(el, (x) => moves.push(x));
    a.destroy();
    fire(el, "touchstart", touch(1, 1));
    expect(moves).toEqual([]);
  });
});
