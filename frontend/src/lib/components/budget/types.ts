// The Budget page's API shapes (runway/server.py api_budget).

/** A spending category's month: what's been spent (its subcategories included) against its budget, if it has one. */
export interface BudgetCategory {
  name: string;
  parent: string | null;
  path: string[];
  depth: number;
  top: string;
  has_children: boolean;
  budget: number | null;
  /** The account this budget's spending goes on (for the budget forecast); null means automatic. */
  pay_with: string | null;
  /** The account it usually goes on, which "Automatic" uses. */
  usual_account: string | null;
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
