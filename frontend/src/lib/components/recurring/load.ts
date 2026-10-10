// GET /api/recurring, and the last reply kept for painting at once (lib/swr.ts).
import { api } from "$lib/api";
import { cacheEpoch, recall, remember } from "$lib/swr";
import type { RecurringItem } from "./types";

/** An item without what's worked out from today and the forecast (when it's due, what's missed, what it's expected to
 *  come to): that mustn't be painted as if current. */
const undated = (r: RecurringItem): RecurringItem => {
  const { next_date: _n, late_date: _l, missed: _m, expected_amount: _e, suggested_amount: _s, skipped: _k, ...rest } = r;
  return rest;
};

export async function loadRecurring(): Promise<RecurringItem[]> {
  const at = cacheEpoch();
  const rows = await api<RecurringItem[]>("/api/recurring");
  remember("recurring", rows.map(undated), at);
  return rows;
}

/** The items as the last loadRecurring saw them, without their due dates, or undefined if there aren't any yet. */
export const staleRecurring = (): RecurringItem[] | undefined => recall<RecurringItem[]>("recurring");
