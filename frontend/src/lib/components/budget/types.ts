// The Budget page's API shapes (runway/server.py api_budget).

/** A spending category's month: what's been spent (its subcategories included) against its budget, if it has one. */
export interface BudgetCategory {
  name: string;
  parent: string | null;
  path: string[];
  depth: number;
  top: string;
  has_children: boolean;
  icon?: string;
  budget: number | null;
  /** The account the category's spending goes on (for the budget forecast; set in Settings → Categories); null means automatic. */
  pay_with: string | null;
  /** The account it usually goes on, which "Automatic" uses. */
  usual_account: string | null;
  /** YYYY-MM the budget has rolled over since (what's left each month adds to the next); null when it doesn't. */
  rollover_from?: string | null;
  /** Left over from earlier months and added to this one's budget. */
  carried?: number;
  /** The budget plus what was carried: what there is to spend this month. */
  available?: number | null;
  spent: number;
  own_spent: number;
  left: number | null;
}

export interface PayAccount { id: string; name: string; kind: string }

/** GET /api/budget?month=YYYY-MM */
export interface BudgetMonth {
  month: string;
  days_in_month: number;
  /** Today's day of the month; 0 for a month still to come, the last day for one that's over. */
  day: number;
  categories: BudgetCategory[];   // tree order: each category followed by its subcategories
  income: number;
  uncategorized: number;
  pay_accounts: PayAccount[];
}

/** A top-level category plus everything under it. */
export interface Family { top: BudgetCategory; kids: BudgetCategory[] }
