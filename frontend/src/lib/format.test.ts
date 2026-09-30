import { afterEach, describe, expect, it, vi } from "vitest";
import { fmt, fmt0, fmtDate, fmtDow, isoDay, monthLabel, monthShort, nb, parseDate, pct, plural, relDay, relTime, shortMoney, thisMonth } from "./format";

const NBSP = " ";

afterEach(() => { vi.useRealTimers(); });

describe("money", () => {
  it("formats dollars and cents", () => {
    expect(fmt(1234.5)).toBe("$1,234.50");
    expect(fmt(-5)).toBe("-$5.00");
    expect(fmt(null)).toBe("$0.00");
    expect(fmt(undefined)).toBe("$0.00");
  });
  it("formats whole dollars", () => {
    expect(fmt0(1234.5)).toBe("$1,235");
    expect(fmt0(null)).toBe("$0");
  });
  it("shortens amounts for chart axes", () => {
    expect(shortMoney(0)).toBe("$0");
    expect(shortMoney(350)).toBe("$350");
    expect(shortMoney(-350)).toBe("−$350");
    expect(shortMoney(1000)).toBe("$1k");
    expect(shortMoney(1200)).toBe("$1.2k");
    expect(shortMoney(12_345)).toBe("$12k");
    expect(shortMoney(1_500_000)).toBe("$1.5M");
    expect(shortMoney(-2_000_000)).toBe("−$2M");
  });
});

describe("dates", () => {
  it("reads a day as local midnight, ignoring any time", () => {
    const d = parseDate("2026-09-28T23:30:00Z");
    expect([d.getFullYear(), d.getMonth(), d.getDate(), d.getHours()]).toEqual([2026, 8, 28, 0]);
  });
  it("writes a local day back out", () => {
    expect(isoDay(new Date(2026, 0, 5, 23, 59))).toBe("2026-01-05");
    expect(isoDay(parseDate("2026-12-31"))).toBe("2026-12-31");
  });
  it("keeps dates on one line", () => {
    expect(fmtDate("2026-10-30")).toBe(`Oct${NBSP}30`);
    expect(fmtDow("2026-10-30")).toBe(`Fri,${NBSP}Oct${NBSP}30`);
    expect(fmtDate("2026-10-30", { month: "short", day: "numeric", year: "numeric" })).toBe(`Oct${NBSP}30,${NBSP}2026`);
    expect(nb("in 3 days")).toBe(`in${NBSP}3${NBSP}days`);
  });
  it("says how far off a day is", () => {
    const today = "2026-09-28";   // a Monday
    expect(relDay("2026-09-28", today)).toBe("today");
    expect(relDay("2026-09-29", today)).toBe("tomorrow");
    expect(relDay("2026-10-01", today)).toBe("Thursday");
    expect(relDay("2026-10-04", today)).toBe("Sunday");
    expect(relDay("2026-10-05", today)).toBe(`Oct${NBSP}5`);   // a week out
    expect(relDay("2026-09-27", today)).toBe(`Sep${NBSP}27`);  // yesterday is a date
  });
  it("counts days across a month end", () => {
    expect(relDay("2026-03-01", "2026-02-28")).toBe("tomorrow");
  });
});

describe("months", () => {
  it("names a month", () => {
    expect(monthLabel("2026-09")).toBe("September 2026");
    expect(monthLabel("2027-01")).toBe("January 2027");
    expect(monthShort("2026-09")).toBe("Sep");
    expect(monthShort("2026-09", true)).toBe("Sep 2026");
  });
  it("knows this month", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 8, 15, 12));
    expect(thisMonth()).toBe("2026-09");
  });
});

describe("relTime", () => {
  it("reads server timestamps as UTC", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-28T18:00:00Z"));
    expect(relTime("2026-09-28 17:59:00")).toBe("just now");
    expect(relTime("2026-09-28 17:30:00")).toBe("30 minutes ago");
    expect(relTime("2026-09-28 15:00:00")).toBe("3 hours ago");
    expect(relTime("2026-09-28T15:00:00Z")).toBe("3 hours ago");
    expect(relTime("2026-09-20 12:00:00")).toBe(`Sep${NBSP}20,${NBSP}2026`);
  });
  it("is blank with no timestamp", () => {
    expect(relTime(null)).toBe("");
    expect(relTime("")).toBe("");
  });
});

describe("plural", () => {
  it("adds an s except for one", () => {
    expect(plural(1, "item")).toBe("1 item");
    expect(plural(0, "item")).toBe("0 items");
    expect(plural(3, "item")).toBe("3 items");
  });
});

describe("pct", () => {
  it("formats a share as a percentage", () => {
    expect(pct(0)).toBe("0%");
    expect(pct(0.001)).toBe("<1%");
    expect(pct(0.004)).toBe("<1%");
    expect(pct(0.005)).toBe("1%");
    expect(pct(0.1)).toBe("10%");
    expect(pct(0.5)).toBe("50%");
    expect(pct(0.994)).toBe("99%");
    expect(pct(0.995)).toBe(">99%");
    expect(pct(0.999)).toBe(">99%");
    expect(pct(1)).toBe("100%");
  });
});
