import { fmtDate, isoDay } from "$lib/format";
import type { Tx } from "./types";

/** A day's heading: "Today", "Yesterday", then the date (with the year when it isn't this year's). `today` is the
 *  machine's local day, as everywhere in the app. */
export function dayLabel(day: string, today: Date = new Date()): string {
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
  if (day === isoDay(today)) return "Today";
  if (day === isoDay(yesterday)) return "Yesterday";
  return fmtDate(day, day.startsWith(String(today.getFullYear())) ? { weekday: "long", month: "short", day: "numeric" }
    : { weekday: "short", month: "short", day: "numeric", year: "numeric" });
}

export interface Day { day: string; rows: { t: Tx; i: number }[]; net: number; transfers: boolean }

/** The list, a day at a time in the order given (newest first), each with its net total: `counted(t)` is what a
 *  transaction adds to it (a transfer adds nothing) and `leftOut(t)` whether a transfer was left out of it. */
export function groupDays(items: Tx[], counted: (t: Tx) => number, leftOut: (t: Tx) => boolean): Day[] {
  const out: Day[] = [];
  items.forEach((t, i) => {
    const day = t.posted.slice(0, 10);
    if (out.at(-1)?.day !== day) out.push({ day, rows: [], net: 0, transfers: false });
    const d = out.at(-1)!;
    d.rows.push({ t, i }); d.net += counted(t);
    if (leftOut(t)) d.transfers = true;
  });
  return out;
}
