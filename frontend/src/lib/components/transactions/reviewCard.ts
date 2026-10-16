import type { Category } from "$lib/types";
import type { Tx } from "./types";

/** Up to `n` categories to offer beside a suggestion: the ones used most on the rows at hand (never a transfer's, nor
 *  the suggestion itself), for money in or out as the transaction is. */
export function alternatives(t: Tx, rows: Tx[], cats: Category[], n = 2): string[] {
  const skip = new Set(cats.filter((c) => c.is_transfer).map((c) => c.name));
  const known = new Set(cats.map((c) => c.name));
  const seen = new Map<string, number>();
  for (const r of rows) {
    if (!r.category || r.category === t.category || skip.has(r.category) || !known.has(r.category) || r.is_split) continue;
    if (r.amount > 0 !== t.amount > 0) continue;
    seen.set(r.category, (seen.get(r.category) ?? 0) + 1);
  }
  return [...seen].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, n).map(([name]) => name);
}
