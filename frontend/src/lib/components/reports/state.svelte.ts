// The Reports page's choices. They live in this module, not in a component, so they survive redraws and moving
// between reports and pages, like the classic app's REPORT_STATE and reportMonth.
import { isoDay } from "$lib/format";

export type RangeKey = "1m" | "3m" | "12m" | "ytd";

export const reportState = $state({
  group: "category" as "category" | "merchant" | "account",   // Over time: what the columns are stacked by
  months: 12,                                                  // Over time and Income
  range: "3m" as RangeKey,                                     // Merchants and Breakdown
  focus: null as string | null,                                // Over time: the one series shown on its own
  path: [] as string[],                                        // Breakdown: where you've clicked in to
  month: null as string | null,                                // Cash flow: the month shown (this month at first)
  monthPicked: false,                                          // Cash flow: a month was picked, so it no longer follows the calendar
  merchantQ: "",                                               // Merchants: the search
  merchantsOpen: [] as string[],                               // Merchants: the ones opened in the table
});

// Date ranges for Merchants and Breakdown: [start, end) as YYYY-MM-DD.
export const RANGES: Record<RangeKey, string> = { "1m": "This month", "3m": "3 months", "12m": "12 months", ytd: "This year" };
export const rangeOptions = Object.entries(RANGES).map(([value, label]) => ({ value, label }));
export const monthsOptions = [6, 12, 24].map((n) => ({ value: String(n), label: `${n} months` }));

export function rangeDates(key: RangeKey): { start: string; end: string } {
  const now = new Date(), next = new Date(now.getFullYear(), now.getMonth() + 1, 1);
  const back = { "1m": 0, "3m": 2, "12m": 11, ytd: 0 }[key];
  const start = key === "ytd" ? new Date(now.getFullYear(), 0, 1) : new Date(now.getFullYear(), now.getMonth() - back, 1);
  return { start: isoDay(start), end: isoDay(next) };
}
