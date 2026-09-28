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
  match: string;
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
  end_date?: string | null;
  active: number;
  matched_count: number;
  expected_amount?: number | null;
  next_date?: string | null;
  missed?: (Missed & { recurring_id: number })[];
}

/** GET /api/recurring/suggestions: a payee that shows up on a schedule. */
export interface Suggestion {
  account_id: string;
  name: string;
  match: string;
  amount: number;
  frequency: string;
  anchor_date: string;
  count: number;
}

/** A matched transaction, as GET /api/transactions?recurring=… lists it. */
export interface MatchedTx { id: string; posted: string; description: string; amount: number }

export const FREQ_OPTIONS: [string, string][] = [["monthly", "Monthly"], ["biweekly", "Every 2 weeks"], ["weekly", "Weekly"],
  ["semimonthly", "Twice a month (set days)"], ["quarterly", "Quarterly"], ["semiannual", "Every 6 months"], ["yearly", "Yearly"],
  ["dates", "Specific dates each year"]];
export const MODE_OPTIONS: [string, string][] = [["fixed", "Fixed amount"], ["last", "Same as last payment"], ["avg3", "Average of last 3"]];
/** How often, as the list says it: "every 2 weeks". */
export const FREQ: Record<string, string> = { monthly: "monthly", biweekly: "every 2 weeks", weekly: "weekly", semimonthly: "twice a month",
  quarterly: "quarterly", semiannual: "every 6 months", yearly: "yearly", dates: "on set dates" };

/** Schedules that need their dates (or days of the month) listed. */
export const needsDates = (freq: string) => freq === "dates" || freq === "semimonthly";
