// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { amountLabel, fromQuery, isFiltered, monthRange, presets, rangeLabel as label, showTransactions, toQuery, txFilters, type TxFilters } from "./filters.svelte";

const none: TxFilters = { q: "", account: "", category: "", from: "", to: "", min: "", max: "", kind: "", scope: "" };
beforeEach(() => { showTransactions({}); location.hash = ""; });

describe("showTransactions", () => {
  it("sets the filters and goes to the Transactions page with them in the address", () => {
    showTransactions({ category: "Groceries", month: "2026-03", scope: "budget" });
    expect(txFilters.transactions).toEqual({ ...none, category: "Groceries", from: "2026-03-01", to: "2026-03-31", scope: "budget" });
    expect(location.hash).toBe("#transactions?category=Groceries&from=2026-03-01&to=2026-03-31&scope=budget");
  });

  it("takes any range (a report's)", () => {
    showTransactions({ category: "Dining", from: "2026-01-01", to: "2026-06-30" });
    expect(txFilters.transactions).toMatchObject({ category: "Dining", from: "2026-01-01", to: "2026-06-30" });
  });

  it("clears filters that aren't given, so an old search doesn't hide the new view", () => {
    showTransactions({ q: "rent", account: "a1", min: "10", kind: "out" });
    showTransactions({ category: "Dining" });
    expect(txFilters.transactions).toEqual({ ...none, category: "Dining" });
    expect(location.hash).toBe("#transactions?category=Dining");
  });

  it("goes to plain #transactions with no filters", () => {
    showTransactions({});
    expect(location.hash).toBe("#transactions");
  });

  it("leaves the Review tab's filters alone", () => {
    txFilters.review.q = "kept";
    showTransactions({ q: "other" });
    expect(txFilters.review.q).toBe("kept");
    txFilters.review.q = "";
  });
});

describe("the address's query", () => {
  it("round-trips every filter, awkward text included", () => {
    const f: TxFilters = { q: "Café & “co” 50%", account: "a|1", category: "Food > Dining", from: "2026-02-01", to: "2026-02-28",
      min: "10.5", max: "200", kind: "transfer", scope: "budget" };
    expect(fromQuery(toQuery(f))).toEqual(f);
  });

  it("leaves out what isn't set", () => {
    expect(toQuery({ ...none, q: "rent", kind: "out" })).toBe("q=rent&kind=out");
    expect(toQuery(none)).toBe("");
  });

  it("drops what's malformed rather than failing", () => {
    expect(fromQuery("from=2026-02-30&to=soon&min=lots&max=-5&kind=sideways&scope=other&q=ok")).toEqual({ ...none, q: "ok" });
  });

  it("reads an older link's month as its days", () => {
    expect(fromQuery("month=2026-02&category=Dining")).toMatchObject({ category: "Dining", from: "2026-02-01", to: "2026-02-28" });
    expect(fromQuery("month=2026-02&from=2026-01-05")).toMatchObject({ from: "2026-01-05", to: "" });
  });
});

describe("isFiltered", () => {
  it("counts every filter but the scope", () => {
    expect(isFiltered(none)).toBe(false);
    expect(isFiltered({ ...none, scope: "budget" })).toBe(false);
    for (const k of ["q", "account", "category", "from", "to", "min", "max", "kind"] as const)
      expect(isFiltered({ ...none, [k]: k === "kind" ? "in" : "x" })).toBe(true);
  });
});

describe("date ranges", () => {
  const today = new Date(2026, 9, 3);   // Oct 3, 2026

  it("offers this month, last month, the last 3 months, the next month and 3, this year and all time", () => {
    expect(presets(today).map((p) => [p.label, p.from, p.to])).toEqual([
      ["This month", "2026-10-01", "2026-10-31"], ["Last month", "2026-09-01", "2026-09-30"],
      ["Last 3 months", "2026-08-01", "2026-10-31"], ["Next month", "2026-11-01", "2026-11-30"], ["Next 3 months", "2026-11-01", "2027-01-31"],
      ["This year", "2026-01-01", "2026-12-31"], ["All time", "", ""]]);
    expect(presets(new Date(2026, 0, 15))[1]).toMatchObject({ from: "2025-12-01", to: "2025-12-31" });   // across a year
    expect(presets(new Date(2026, 11, 15))[3]).toMatchObject({ from: "2027-01-01", to: "2027-01-31" });   // next month, across a year
    expect(presets(new Date(2026, 11, 15))[4]).toMatchObject({ from: "2027-01-01", to: "2027-03-31" });
    expect(presets(new Date(2026, 0, 31))[3]).toMatchObject({ from: "2026-02-01", to: "2026-02-28" });   // from a long month into a short one
  });

  it("names the range", () => {
    const rangeLabel = (from: string, to: string, d: Date) => label(from, to, d).replace(/\u00a0/g, " ");   // dates keep their words together
    expect(rangeLabel("", "", today)).toBe("All dates");
    expect(rangeLabel("2026-09-01", "2026-09-30", today)).toBe("Last month");
    expect(rangeLabel("2026-03-01", "2026-03-31", today)).toBe("March 2026");
    expect(rangeLabel("2026-03-05", "2026-04-02", today)).toBe("Mar 5 – Apr 2");
    expect(rangeLabel("2025-12-20", "2026-01-10", today)).toBe("Dec 20, 2025 – Jan 10, 2026");
    expect(rangeLabel("2026-11-01", "2026-11-30", today)).toBe("Next month");
    expect(rangeLabel("2026-11-01", "2027-01-31", today)).toBe("Next 3 months");
    expect(rangeLabel("", "2027-02-10", today)).toBe("Until Feb 10, 2027");   // a future end alone
    expect(rangeLabel("2026-03-05", "", today)).toBe("From Mar 5");
    expect(rangeLabel("", "2025-03-05", today)).toBe("Until Mar 5, 2025");
    expect(rangeLabel("2026-03-05", "2026-03-05", today)).toBe("Mar 5");
  });

  it("knows a month's last day", () => {
    expect(monthRange("2024-02")).toEqual({ from: "2024-02-01", to: "2024-02-29" });
    expect(monthRange("2026-12")).toEqual({ from: "2026-12-01", to: "2026-12-31" });
  });

  it("says the amount short", () => {
    expect(amountLabel("50", "100")).toBe("$50–$100");
    expect(amountLabel("1500", "")).toBe("$1,500 or more");
    expect(amountLabel("", "20.5")).toBe("Up to $20.5");
    expect(amountLabel("59.28", "59.28")).toBe("$59.28");
  });
});
