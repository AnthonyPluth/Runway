// Money and date formatting, the same as the classic app's.

const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const money0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
export const fmt = (n: number | null | undefined) => money.format(n ?? 0);
export const fmt0 = (n: number | null | undefined) => money0.format(n ?? 0);
/** Whole dollars rounded down, for a low a balance stays above: $4,820.55 is "$4,820", never "$4,821". */
export const fmt0Down = (n: number | null | undefined) => money0.format(Math.floor(n ?? 0));

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

/** A share (0–1) as a whole percentage that never rounds a real amount to "0%" or "100%": 0.003 → "<1%", 0.997 → ">99%". */
export function pct(share: number): string {
  if (share === 0) return "0%";
  if (share < 0.005) return "<1%";
  if (share >= 0.995 && share < 1) return ">99%";
  return `${Math.round(share * 100)}%`;
}
/** A share (0–1) as a progress bar's CSS width, clamped to 0–100% and rounded to a tenth ("33.3%", "100%"). No share is an empty bar. */
export function barWidth(share: number | null | undefined): string {
  const p = share == null || Number.isNaN(share) ? 0 : Math.min(100, Math.max(0, share * 100));
  return `${Math.round(p * 10) / 10}%`;
}

/** "2026-09" → "September 2026". */
export function monthLabel(m: string): string {
  const [y, mo] = m.split("-").map(Number);
  return new Date(y, mo - 1, 1).toLocaleDateString("en-US", { month: "long", year: "numeric" });
}
/** "2026-09" → "Sep" (or "Sep 2026"). */
export function monthShort(m: string, withYear = false): string {
  const [y, mo] = m.split("-").map(Number);
  return new Date(y, mo - 1, 1).toLocaleDateString("en-US", withYear ? { month: "short", year: "numeric" } : { month: "short" });
}
/** This month as YYYY-MM. */
export const thisMonth = () => isoDay().slice(0, 7);

/** A server timestamp as a Date: ISO (with its UTC offset, or without one for local time), or "2026-09-28 17:54:18" in UTC. */
export const serverTime = (s: string) => new Date(s.includes("T") ? s : s.replace(" ", "T") + "Z");
/** "Sep 28, 5:54 PM": a moment, in this browser's time zone. */
export const fmtDateTime = (d: Date) => d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

/** A server timestamp ("2026-09-28 17:54:18" in UTC, or ISO) as "just now", "5 minutes ago", "3 hours ago" or a date. */
export function relTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = serverTime(iso);
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} minutes ago`;
  if (s < 129600) return `${Math.round(s / 3600)} hours ago`;
  return fmtDate(isoDay(d), { month: "short", day: "numeric", year: "numeric" });
}
