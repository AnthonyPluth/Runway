// Money and date formatting, the same as the classic app's.

const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const money0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
export const fmt = (n: number | null | undefined) => money.format(n ?? 0);
export const fmt0 = (n: number | null | undefined) => money0.format(n ?? 0);

/** "$1.2k", "−$350" for chart axes. */
export function shortMoney(v: number): string {
  const a = Math.abs(v);
  const s = a >= 1e6 ? (a / 1e6).toFixed(1).replace(/\.0$/, "") + "M"
    : a >= 1e3 ? (a / 1e3).toFixed(a >= 1e4 ? 0 : 1).replace(/\.0$/, "") + "k" : a.toFixed(0);
  return (v < 0 ? "−$" : "$") + s;
}

/** A YYYY-MM-DD date as a local Date (never UTC, which in the evening is already tomorrow). */
export function parseDate(s: string): Date {
  const [y, m, d] = s.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}
export const isoDay = (d: Date = new Date()) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

// Dates never break across lines ("Oct" at the end of one line, "30" on the next): spaces become non-breaking.
export const fmtDate = (s: string, opts: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" }) =>
  parseDate(s).toLocaleDateString("en-US", opts).replace(/ /g, " ");
export const fmtDow = (s: string) => fmtDate(s, { weekday: "short", month: "short", day: "numeric" });
/** Keep a short phrase on one line. */
export const nb = (s: string) => s.replace(/ /g, " ");

/** "today", "tomorrow", "Monday" (within a week) or "Oct 12". */
export function relDay(s: string, today: string): string {
  const days = Math.round((parseDate(s).getTime() - parseDate(today).getTime()) / 864e5);
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days > 1 && days < 7) return parseDate(s).toLocaleDateString("en-US", { weekday: "long" });
  return fmtDate(s);
}

export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
