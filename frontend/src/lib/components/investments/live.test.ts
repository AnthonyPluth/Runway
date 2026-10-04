import { afterEach, describe, expect, it, vi } from "vitest";
import { applyLiveQuotes } from "./live";
import type { Holding, Investments, Quote } from "./types";

const NOW = 1_790_000_000;

function holding(over: Partial<Holding>): Holding {
  return {
    security_id: "s", group: "g", ticker: null, name: null, type: null, asset_class: "equity", sector: null, is_cash: false,
    quantity: 0, value: 0, cost_basis: 0, cost_known: true, cost_manual: false, accounts: [], price: null, lots: [],
    allocation: 0, gain: null, gain_pct: null, day_change: null, day_change_pct: null, ...over,
  };
}
const page = (holdings: Holding[]) =>
  ({ holdings, total: 0, unrealized_gain: null, cost_basis: null, day_change: null, day_change_pct: null }) as unknown as Investments;
const quote = (over: Partial<Quote>): Quote => ({ price: 0, prev_close: null, time: NOW - 60, type: "EQUITY", ...over });

afterEach(() => { vi.useRealTimers(); });

describe("applyLiveQuotes", () => {
  it("re-prices holdings and recomputes the page's totals", () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW * 1000);
    const d = page([
      holding({ ticker: "AAA", quantity: 10, value: 1000, cost_basis: 800, gain: 200 }),
      holding({ ticker: null, is_cash: true, quantity: 500, value: 500 }),
    ]);
    applyLiveQuotes(d, { AAA: quote({ price: 110, prev_close: 100 }) }, "open");
    const [a, cash] = d.holdings;
    expect(a.price).toBe(110);
    expect(a.value).toBe(1100);
    expect(a.live).toBe(true);
    expect(a.day_change).toBe(100);
    expect(a.day_change_pct).toBeCloseTo(0.1, 10);
    expect(a.gain).toBe(300);
    expect(a.gain_pct).toBeCloseTo(0.375, 10);
    expect(cash.live).toBe(false);
    expect(d.total).toBe(1600);
    expect(a.allocation).toBeCloseTo(1100 / 1600, 10);
    expect(cash.allocation).toBeCloseTo(500 / 1600, 10);
    expect(d.day_change).toBe(100);
    expect(d.day_change_pct).toBeCloseTo(0.1, 10);
    expect(d.unrealized_gain).toBe(300);
    expect(d.cost_basis).toBe(800);
  });

  it("marks a price live only while the market is open, recent, and not a mutual fund", () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW * 1000);
    const d = page([
      holding({ ticker: "OLD", quantity: 1 }),
      holding({ ticker: "FUND", quantity: 1 }),
      holding({ ticker: "NOW", quantity: 1 }),
    ]);
    const quotes = {
      OLD: quote({ price: 1, time: NOW - 1300 }),
      FUND: quote({ price: 1, type: "MutualFund" }),
      NOW: quote({ price: 1 }),
    };
    applyLiveQuotes(d, quotes, "open");
    expect(d.holdings.map((h) => h.live)).toEqual([false, false, true]);
    applyLiveQuotes(d, quotes, "closed");
    expect(d.holdings.map((h) => h.live)).toEqual([false, false, false]);
  });

  it("leaves the day's change unknown when nothing has a previous close", () => {
    const d = page([holding({ ticker: "AAA", quantity: 2 })]);
    applyLiveQuotes(d, { AAA: quote({ price: 5 }) }, "closed");
    expect(d.total).toBe(10);
    expect(d.day_change).toBeNull();
    expect(d.day_change_pct).toBeNull();
    expect(d.unrealized_gain).toBeNull();
  });
});
