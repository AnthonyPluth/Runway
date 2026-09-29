// How the Churning page words and filters what the server worked out (runway/churning.py, runway/bank_bonuses.py).
import { fmt0, fmtDate, parseDate } from "$lib/format";
import type { BankBonus, ChurnCard, Eligibility, Five24, UpcomingItem } from "./types";

export const BOTH = "";   // the person switcher's "everyone" value

/** Only this person's items ("" = everyone's). */
export const mine = <T extends { owner: string | null }>(items: T[], person: string) =>
  person === BOTH ? items : items.filter((x) => x.owner === person);

export const daysUntil = (day: string, today: string) =>
  Math.round((parseDate(day).getTime() - parseDate(today).getTime()) / 864e5);

const fullDate = (d: string) => fmtDate(d, { month: "short", day: "numeric", year: "numeric" });
export { fullDate };

/** 60,000 → "60k", 1,250 → "1,250" (points and miles). */
export function points(n: number | null | undefined): string {
  if (n == null) return "—";
  if (Math.abs(n) >= 10000) return `${Math.round(n / 1000).toLocaleString("en-US")}k`;
  return Math.round(n).toLocaleString("en-US");
}

/** A bonus in its own currency: "$200" for cash, "60k Ultimate Rewards" for points. */
export function bonusLabel(amount: number | null, currencyKey: string, currencyName: string): string {
  if (!amount) return "";
  return currencyKey === "cash" ? fmt0(amount) : `${points(amount)} ${currencyName.replace(/^(Chase|Amex|Citi|Capital One) /, "")}`;
}

/** Spending toward a bonus: the share done (0 to 1) and what's left. */
export function spendProgress(spent: number | null, need: number | null): { share: number; left: number } {
  const s = spent ?? 0, n = need ?? 0;
  if (n <= 0) return { share: s > 0 ? 1 : 0, left: 0 };
  return { share: Math.max(0, Math.min(1, s / n)), left: Math.max(0, n - s) };
}

/** When a bonus can be earned again, in a few words. */
export function eligibilityText(e: Eligibility, today: string): string {
  switch (e.status) {
    case "now": return e.override ? "Now (your date)" : "Now";
    case "later": return fullDate(e.on!) + (e.override ? " (your date)" : "");
    case "never": return "Never (once per lifetime)";
    case "in_progress": return "Earning it now";
    case "held": return e.on && daysUntil(e.on, today) > 0 ? `After you close it, from ${fullDate(e.on)}` : "After you close or downgrade it";
    case "au": return "—";
    default: return "Rule unknown";
  }
}

/** "3/24" and what happens next: "4/24 on Jan 10, 2027" (at 5 or more: the day you're under). */
export function five24Line(f: Five24 | undefined): { count: string; next: string } {
  if (!f) return { count: "0/24", next: "No cards yet" };
  const count = `${f.count}/24`;
  if (f.under_on) return { count, next: `Under 5/24 on ${fullDate(f.under_on)}` };
  if (f.next_fall_off) return { count, next: `${f.count - 1}/24 on ${fullDate(f.next_fall_off)}` };
  return { count, next: "Nothing counts right now" };
}

export const STATUS_LABEL: Record<string, string> = { open: "Open", closed: "Closed", product_changed: "Product changed" };
export const BANK_STATUS_LABEL: Record<string, string> = { open: "Open", pending: "Requirements met", received: "Bonus received", closed: "Closed" };
export const BANK_TYPE_LABEL: Record<string, string> = { checking: "Checking", savings: "Savings", business: "Business" };
export const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** Open cards first, then by when they were opened, newest first. */
export const cardOrder = (a: ChurnCard, b: ChurnCard) =>
  Number(a.status !== "open") - Number(b.status !== "open") || b.opened_on.localeCompare(a.opened_on) || a.id - b.id;

/** Bonuses still in play first (being earned, then waiting to post), then the rest, newest first. */
export function bankOrder(a: BankBonus, b: BankBonus): number {
  const rank = { active: 0, met: 1, missed: 2, received: 3, closed: 4 };
  return rank[a.state] - rank[b.state] || b.opened_on.localeCompare(a.opened_on) || a.id - b.id;
}

/** Annual fees due in the next `days` days: the total and how many. */
export function feesDue(cards: ChurnCard[], today: string, days = 90): { total: number; count: number } {
  const due = cards.filter((c) => c.fee_due && daysUntil(c.fee_due, today) <= days);
  return { total: due.reduce((s, c) => s + (c.annual_fee || 0), 0), count: due.length };
}

/** Bank bonus requirements that aren't met yet, in words: "$200 more direct deposits · 3 more debit purchases". */
export function bankLeft(b: BankBonus): string[] {
  const p = b.progress, out: string[] = [];
  if (b.dd_total && p.dd_total < b.dd_total) out.push(`${fmt0(b.dd_total - p.dd_total)} more direct deposits`);
  if (b.dd_count && p.dd_count != null && p.dd_count < b.dd_count) out.push(`${b.dd_count - p.dd_count} more deposit${b.dd_count - p.dd_count === 1 ? "" : "s"}`);
  if (b.debit_count && p.debits < b.debit_count) out.push(`${b.debit_count - p.debits} more debit purchase${b.debit_count - p.debits === 1 ? "" : "s"}`);
  if (b.min_balance && p.balance_ok === false) out.push(`${fmt0(b.min_balance - (p.balance ?? 0))} more to reach the minimum balance`);
  return out;
}

/** The Upcoming list's label for each kind of item. */
export const KIND_LABEL: Record<UpcomingItem["kind"], string> = {
  task: "To-do", fee: "Annual fee", bonus: "Bonus spending", five24: "5/24", eligible: "Bonus again",
  bank_due: "Bank bonus", bank_hold: "Balance hold", bank_post: "Bonus posting", bank_close: "Safe to close",
  bank_fee: "Monthly fee", bank_eligible: "Bonus again",
};
