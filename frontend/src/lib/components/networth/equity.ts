// Stock options, RSUs and shares: the kinds of grant, and what a grant is worth as it vests.
import { fmtDate, isoDay, parseDate, pct } from "$lib/format";
import type { Company, Grant, GrantBody } from "./types";

export const EQ_KINDS: Record<string, string> = { iso: "ISO options", nso: "NSO options", rsu: "RSUs", rsa: "Restricted stock", shares: "Shares" };
export const isOption = (k: string) => k === "iso" || k === "nso";
export const shares = (n: number | null | undefined) => (n == null ? "—" : Number(n).toLocaleString("en-US", { maximumFractionDigits: 2 }));

/** How many companies the vesting chart draws a line for; past that, the smaller ones are added up as "Other". */
export const CHART_LINES = 4;

/** Vested value over time, one line per company, month by month from the first grant to the last share vesting.
 *  With more than {@link CHART_LINES} companies, the biggest (by what they come to once all vested) keep their own
 *  line and the rest are added up into one "Other" line, so every grant is on the chart. Returns the month-end dates
 *  and each line's values. */
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
  const lines = companies.map((co) => ({ name: co.name, values: xs.map((day) => co.grants.reduce((a, g) => a + worth(co, g, vestedAt(g, day)), 0)) }));
  if (lines.length <= CHART_LINES) return { xs, lines };
  const big = [...lines].sort((p, q) => (q.values.at(-1) ?? 0) - (p.values.at(-1) ?? 0));
  const rest = big.slice(CHART_LINES - 1);
  const keep = new Set(big.slice(0, CHART_LINES - 1));
  return { xs, lines: [...lines.filter((l) => keep.has(l)), { name: "Other", values: xs.map((_, i) => rest.reduce((a, l) => a + l.values[i], 0)) }] };
}

/** Where today falls along the chart's dates, as a (fractional) index into them, or null when it's before the first or
 *  after the last. Dates are YYYY-MM-DD. */
export function todayIndex(xs: string[], today: string): number | null {
  if (xs.length < 2 || today < xs[0] || today > xs[xs.length - 1]) return null;
  const i = Math.min(xs.length - 2, xs.findLastIndex((d) => d <= today));
  const a = parseDate(xs[i]).getTime(), b = parseDate(xs[i + 1]).getTime();
  return i + (parseDate(today).getTime() - a) / (b - a);
}

/** Whole months from one YYYY-MM-DD date to another (a month counts on its day of the month, as vesting does). */
function monthsBetween(a: string, b: string): number {
  const x = parseDate(a), y = parseDate(b);
  return (y.getFullYear() - x.getFullYear()) * 12 + y.getMonth() - x.getMonth() - (y.getDate() < x.getDate() ? 1 : 0);
}

/** A share price old enough to be worth updating (about three months or more), as "price from 7 months ago"; null
 *  when it's recent, or when there's no price date. */
export function stalePrice(asOf: string | null | undefined, today: string): string | null {
  if (!asOf) return null;
  const months = monthsBetween(asOf, today);
  if (months < 3) return null;
  return months >= 24 ? `price from ${Math.floor(months / 12)} years ago` : months >= 12 ? "price from over a year ago" : `price from ${months} months ago`;
}

const EVERY: Record<string, string> = { "1": "monthly", "3": "quarterly", "12": "yearly" };

/** A grant's vesting in one line, from the form's fields as typed: "25% on Jan 2027, then monthly until Jan 2030".
 *  The same rule as runway/domain/equity.py's vested_on: nothing before the cliff, then a share every `vest_every` months.
 *  Null when there's nothing to say yet (plain shares, no start date, a length that isn't a number). */
export function vestingPreview(f: Pick<GrantBody, "kind" | "vest_start" | "granted_on" | "vest_months" | "cliff_months" | "vest_every">): string | null {
  if (f.kind === "shares") return null;
  const start = /^\d{4}-\d{2}-\d{2}$/.test(f.vest_start) ? f.vest_start : /^\d{4}-\d{2}-\d{2}$/.test(f.granted_on) ? f.granted_on : "";
  if (!start || Number.isNaN(parseDate(start).getTime())) return null;
  const total = f.vest_months.trim() === "" ? 0 : Number(f.vest_months);
  const cliff = f.cliff_months.trim() === "" ? 0 : Number(f.cliff_months);
  const every = Math.max(1, Number(f.vest_every) || 1);
  if (![total, cliff].every((v) => Number.isInteger(v) && v >= 0 && v <= 600)) return null;
  const at = (months: number) => {
    const d = parseDate(start), y = d.getFullYear(), m = d.getMonth() + months;
    return fmtDate(isoDay(new Date(y, m, Math.min(d.getDate(), new Date(y, m + 1, 0).getDate()))), { month: "short", year: "numeric" });
  };
  if (total === 0) return `All on ${at(0)}`;
  // The first month anything vests: the cliff, or the first step after the start.
  let first = Math.max(1, cliff);
  while (Math.min(total, first - (first % every)) <= 0) first++;
  const share = Math.min(total, first - (first % every)) / total;
  if (share >= 1) return `All on ${at(first)}`;
  const word = EVERY[String(every)] ?? `every ${every} months`;
  return first > every ? `${pct(share)} on ${at(first)}, then ${word} until ${at(total)}`
    : `${word[0].toUpperCase()}${word.slice(1)} from ${at(first)} until ${at(total)}`;
}
