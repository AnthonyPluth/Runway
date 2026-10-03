// The Recurring page's API shapes (runway/server.py api_recurring, api_recurring_suggestions).
import type { Missed } from "$lib/types";

/** The fields you edit on a recurring item (and send to POST /api/recurring[/id]). */
export interface RecurringValues {
  name: string;
  account_id: string;
  amount: number | string | null;
  amount_mode: string;
  frequency: string;
  dates: string;
  anchor_date: string;
  /** Merchant texts, one per line: a transaction with any of them matches. */
  match: string;
  /** "Only amounts between": either end may be blank (any amount matches when both are). */
  amount_min: number | string | null;
  amount_max: number | string | null;
  /** "Ends on": the last day it can come (blank: it carries on). */
  end_date: string;
}

/** GET /api/recurring: a row of the recurring table, plus what's been matched and when it's next due. */
export interface RecurringItem {
  id: number;
  name: string;
  account_id: string;
  account_name?: string | null;
  amount: number;
  amount_mode?: string | null;
  frequency: string;
  dates?: string | null;
  anchor_date?: string | null;
  match?: string | null;
  amount_min?: number | null;
  amount_max?: number | null;
  end_date?: string | null;
  active: number;
  matched_count: number;
  expected_amount?: number | null;
  /** A fixed amount's last few posted payments all came to something else: about this much (signed like amount). */
  suggested_amount?: number | null;
  /** The first date from today on that it's still expected (a skipped one isn't). */
  next_date?: string | null;
  /** An earlier date it's still expected while its window is open: it's late (the forecast has it today). */
  late_date?: string | null;
  /** Dates from today on you've skipped (a one-off $0 edit). */
  skipped?: string[];
  last_matched?: MatchedTx | null;
  /** Its logo: the one you chose, else its last matched transaction's merchant's (null: its category's icon). */
  logo?: string | null;
  missed?: (Missed & { recurring_id: number })[];
}

/** GET /api/recurring/suggestions: a payee that shows up on a schedule. */
export interface Suggestion {
  /** Stable across visits (account, merchant text, schedule); what "Not recurring" remembers. */
  key: string;
  account_id: string;
  name: string;
  match: string;
  amount: number;
  /** What the last few came to, smallest and largest (unsigned): "$12–$15" when they vary. */
  amount_low?: number;
  amount_high?: number;
  frequency: string;
  anchor_date: string;
  count: number;
}

/** GET /api/recurring/suggestions/dismissed: a suggestion marked "Not recurring". Only what its key holds is kept, so no amount. */
export interface DismissedSuggestion {
  key: string;
  account_id: string;
  account_name?: string | null;
  /** The merchant text, lowercased. */
  match: string;
  /** The payee as its transactions name it (the merchant text when none is left). */
  name?: string;
  frequency: string;
}

/** GET /api/recurring/{id}/candidates: a transaction that could be the payment a missed date was for. */
export interface Candidate { id: string; posted: string; amount: number; name: string; pending?: number }

/** A matched transaction, as GET /api/transactions?recurring=… lists it. */
export interface MatchedTx {
  id: string; posted: string; description: string; amount: number; category?: string | null;
  /** How it was linked: by you (from Transactions) or automatically by its merchant text; null from before Runway kept it. */
  recurring_linked_by?: "you" | "auto" | null;
}

export const FREQ_OPTIONS: [string, string][] = [["monthly", "Monthly"], ["biweekly", "Every 2 weeks"], ["weekly", "Weekly"],
  ["semimonthly", "Twice a month (set days)"], ["quarterly", "Quarterly"], ["semiannual", "Every 6 months"], ["yearly", "Yearly"],
  ["dates", "Specific dates each year"], ["once", "Once (a one-time item)"]];
export const MODE_OPTIONS: [string, string][] = [["fixed", "Always the amount above"], ["last", "Same as the last payment"], ["avg3", "Average of the last 3 payments"]];
/** How often, as the list says it: "every 2 weeks". */
export const FREQ: Record<string, string> = { monthly: "monthly", biweekly: "every 2 weeks", weekly: "weekly", semimonthly: "twice a month",
  quarterly: "quarterly", semiannual: "every 6 months", yearly: "yearly", dates: "on set dates", once: "one-time" };

/** Schedules that need their dates (or days of the month) listed. */
export const needsDates = (freq: string) => freq === "dates" || freq === "semimonthly";

/** The fields that can be wrong, and what to tell you about each. */
export type Errors = Partial<Record<"name" | "account_id" | "amount" | "dates" | "anchor_date" | "amount_max" | "end_date", string>>;

/** The stored amount for what you typed: negative for money out, positive for money in (the forecast relies on the sign). */
export function signedAmount(magnitude: number | string | null, out: boolean): number | null {
  if (magnitude === null || String(magnitude).trim() === "") return null;
  const n = Math.abs(Number(magnitude));
  return Number.isFinite(n) ? (out ? -n : n) : null;
}

/** What's missing or wrong with a recurring item's fields, before it goes to the server. */
export function validate(v: RecurringValues): Errors {
  const e: Errors = {};
  if (!v.name.trim()) e.name = "Enter a name, like Paycheck or Rent.";
  if (!v.account_id) e.account_id = "Choose an account.";
  const amount = v.amount === null || String(v.amount).trim() === "" ? NaN : Number(v.amount);
  if (Number.isNaN(amount)) e.amount = "Enter an amount, like 120.00.";
  else if (amount === 0 && v.amount_mode === "fixed") e.amount = "Enter an amount above 0.";
  if (needsDates(v.frequency) && !v.dates.trim()) e.dates = v.frequency === "semimonthly" ? "List the days of the month, like 1, 15." : "List the dates, like Apr 15, Oct 15.";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(v.anchor_date)) e.anchor_date = "Pick a date.";
  const [lo, hi] = [v.amount_min, v.amount_max].map((x) => (x === null || String(x).trim() === "" ? NaN : Math.abs(Number(x))));
  if (lo > hi) e.amount_max = "The largest amount is smaller than the smallest.";
  if (v.end_date && /^\d{4}-\d{2}-\d{2}$/.test(v.anchor_date) && v.end_date < v.anchor_date) e.end_date = "It ends before it starts.";
  return e;
}
