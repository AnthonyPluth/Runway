// When recurring items come: how far off the next one is (or how late), the order to list them in, and what they
// come to in a month.
import { fmtDate, parseDate, plural } from "$lib/format";
import type { RecurringItem } from "./types";

/** Whole days from `from` to `to` (YYYY-MM-DD, local dates): negative when `to` is earlier. */
export const daysBetween = (from: string, to: string) => Math.round((parseDate(to).getTime() - parseDate(from).getTime()) / 864e5);

/** "3 days late", "1 day late". */
export const lateBy = (date: string, today: string) => `${plural(Math.max(1, daysBetween(date, today)), "day")} late`;

/** Up to a month ahead, how far ("today", "tomorrow", "in 9 days"); further, the date ("next Nov 20", or with another
 * `prefix`, like none for a one-time item). */
export function dueIn(date: string, today: string, prefix = "next "): string {
  const n = daysBetween(today, date);
  if (n <= 0) return "today";
  if (n === 1) return "tomorrow";
  return n <= 30 ? `in ${n} days` : `${prefix}${fmtDate(date)}`;
}

/**
 * What a summary line says about when an item comes: late since its late date ("3 days late", `late`), else when it's
 * next due; a one-time item that has been says its date. Nothing for a paused item (it says Paused instead).
 */
export function dueLabel(r: Pick<RecurringItem, "active" | "frequency" | "anchor_date" | "next_date" | "late_date">, today: string): { text: string; late: boolean } | null {
  if (!r.active) return null;
  if (r.late_date) return { text: lateBy(r.late_date, today), late: true };
  if (r.next_date) return { text: dueIn(r.next_date, today, r.frequency === "once" ? "" : "next "), late: false };
  if (r.frequency === "once" && r.anchor_date) return { text: fmtDate(r.anchor_date), late: false };
  return { text: "no upcoming date", late: false };
}

/** The order within a group: late first, then by when each is next due; ones with no date, then paused ones, last. */
export function byDue(a: RecurringItem, b: RecurringItem): number {
  const key = (r: RecurringItem) => [r.active ? 0 : 1, r.late_date ?? r.next_date ?? "9999"] as const;
  const [x, y] = [key(a), key(b)];
  return x[0] - y[0] || x[1].localeCompare(y[1]) || a.name.localeCompare(b.name);
}

/** How many times a month a schedule comes, on average: 52 weeks a year over 12 months for weekly, a third for quarterly;
 * twice-a-month and set dates by how many days or dates are listed. Nothing for a one-time item. */
export function perMonth(frequency: string, dates?: string | null): number {
  const listed = (dates ?? "").split(",").filter((x) => x.trim()).length;
  const per: Record<string, number> = { weekly: 52 / 12, biweekly: 26 / 12, semimonthly: listed || 2, monthly: 1, quarterly: 1 / 3,
    semiannual: 1 / 6, yearly: 1 / 12, dates: listed / 12 };
  return per[frequency] ?? 0;
}

/** What a list of items comes to in a month (unsigned), at the amounts the forecast expects; paused ones don't count. */
export const monthlyTotal = (items: RecurringItem[]) =>
  items.reduce((sum, r) => sum + (r.active ? Math.abs(r.expected_amount ?? r.amount) * perMonth(r.frequency, r.dates) : 0), 0);
