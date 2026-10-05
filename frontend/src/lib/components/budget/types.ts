// The Budget page's API shapes: GET and POST /api/budget's, from the API contract (lib/api-types.ts, generated from
// runway/server/contract.py). A category's month is what's been spent (its subcategories included) against its
// budget, if it has one; an income category's row has the same shape, with `budget` what's expected to come in and
// `spent` what has. `day` is today's day of the month: 0 for a month still to come, the last day for one that's over.
import type { BudgetCategory } from "$lib/api-types";

export type { BudgetCategory, BudgetMonth, BudgetSaved, PayAccount } from "$lib/api-types";

/** A top-level category plus everything under it. */
export interface Family { top: BudgetCategory; kids: BudgetCategory[] }
