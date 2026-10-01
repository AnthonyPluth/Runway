// Undo for changes to transactions: the server sends back what each one was (`was`) with any change it makes, and
// sends it again to put them back (POST /api/transactions/bulk with `restore`).
import { api } from "$lib/api";
import { refreshState } from "$lib/app.svelte";

/** A transaction as it was before a change (`was` in the replies of the category, bulk and AI apply endpoints). */
export interface Was {
  id: string;
  category: string | null;
  category_source: string | null;
  confidence: number | null;
  needs_review: number;
  payee: string | null;
  is_split: number;
  splits?: { amount: number; category: string; note?: string | null }[];
  /** The items of its order, when a category change set theirs too: sent back untouched by Undo. */
  items?: { id: number; category: string | null; category_source: string | null; confidence: number | null }[];
  /** Its order's charges and what Runway had given it from them: sent back untouched by Undo. */
  charges?: { id: string; applied: string | null }[];
}

/** One of `changed` in the reply of POST /api/rules/{id}/apply. */
export interface RuleWas {
  id: string;
  was_category: string | null;
  was_source: string | null;
  was_confidence: number | null;
  was_needs_review: number;
  was_payee: string | null;
  was_split: number;
}

export const fromRule = (c: RuleWas): Was => ({
  id: c.id, category: c.was_category, category_source: c.was_source, confidence: c.was_confidence,
  needs_review: c.was_needs_review, payee: c.was_payee, is_split: c.was_split,
});

/** Whether a brand's transactions kept the bank's name before a change (`keep_bank` in POST /api/transactions/{id}/name). */
export interface KeepBank { brand: string; keep: boolean }

/** Put these transactions back as they were (and the brand's setting, if it changed), and refresh what counts them
 *  (the To review badge). */
export async function restoreTx(was: Was[], keepBank?: KeepBank): Promise<void> {
  await api("/api/transactions/bulk", { method: "POST", body: keepBank ? { restore: was, keep_bank: keepBank } : { restore: was } });
  refreshState();
}
