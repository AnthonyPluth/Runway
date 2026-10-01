// Stock options, RSUs and shares: the kinds of grant, and what a grant is worth as it vests.
import { isoDay, parseDate } from "$lib/format";
import type { Company, Grant } from "./types";

export const EQ_KINDS: Record<string, string> = { iso: "ISO options", nso: "NSO options", rsu: "RSUs", rsa: "Restricted stock", shares: "Shares" };
export const isOption = (k: string) => k === "iso" || k === "nso";
export const shares = (n: number | null | undefined) => (n == null ? "—" : Number(n).toLocaleString("en-US", { maximumFractionDigits: 2 }));

/** Vested value over time, one line per company (up to three), month by month from the first grant to the last
 *  share vesting. Returns the month-end dates and each company's values. */
export function vestingSeries(companies: Company[]): { xs: string[]; lines: { name: string; values: number[] }[] } | null {
  const starts = companies.flatMap((co) => co.grants.map((g) => g.schedule[0]?.[0]).filter(Boolean)).sort();
  const ends = companies.flatMap((co) => co.grants.map((g) => g.fully_vested_on || g.schedule.at(-1)?.[0]).filter(Boolean)).sort();
  if (!starts.length) return null;
  const xs: string[] = [];
  for (let t = parseDate(starts[0]); isoDay(t) <= (ends.at(-1) || starts[0]) || xs.length < 2; t = new Date(t.getFullYear(), t.getMonth() + 1, 1)) {
    xs.push(isoDay(new Date(t.getFullYear(), t.getMonth() + 1, 0)));
    if (xs.length > 240) break;
  }
  const vestedAt = (g: Grant, day: string) => { let v = 0; for (const [dd, q] of g.schedule) if (dd <= day) v = q; return v; };
  const worth = (co: Company, g: Grant, v: number) =>
    isOption(g.kind) ? Math.max(0, (co.share_price || 0) - (g.strike || 0)) * v : (co.share_price || 0) * v;
  return {
    xs,
    lines: companies.slice(0, 3).map((co) => ({ name: co.name, values: xs.map((day) => co.grants.reduce((a, g) => a + worth(co, g, vestedAt(g, day)), 0)) })),
  };
}

/** Where today falls along the chart's dates, as a (fractional) index into them, or null when it's before the first or
 *  after the last. Dates are YYYY-MM-DD. */
export function todayIndex(xs: string[], today: string): number | null {
  if (xs.length < 2 || today < xs[0] || today > xs[xs.length - 1]) return null;
  const i = Math.min(xs.length - 2, xs.findLastIndex((d) => d <= today));
  const a = parseDate(xs[i]).getTime(), b = parseDate(xs[i + 1]).getTime();
  return i + (parseDate(today).getTime() - a) / (b - a);
}
