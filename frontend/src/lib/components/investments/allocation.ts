import type { Holding, InvAccount } from "./types";

export interface AllocRow { name: string; value: number; share: number; other?: boolean }

/** More groups than this fold: the largest `MAX_GROUPS - 1` stay, the rest become one "Other". */
export const MAX_GROUPS = 6;

/** The legend's rows, largest first. Past six groups, the five largest and "Other" (the rest added up). */
export function foldAllocation(items: { name: string; value: number; share: number }[]): AllocRow[] {
  const sorted = [...items].sort((a, b) => b.share - a.share);
  if (sorted.length <= MAX_GROUPS) return sorted;
  // A group the data itself calls "Other" goes into the remainder, so there is only one row of that name.
  const named = sorted.filter((r) => r.name !== "Other");
  const rest = [...named.slice(MAX_GROUPS - 1), ...sorted.filter((r) => r.name === "Other")];
  return [
    ...named.slice(0, MAX_GROUPS - 1),
    { name: "Other", value: rest.reduce((s, r) => s + r.value, 0), share: rest.reduce((s, r) => s + r.share, 0), other: true },
  ];
}

/** Today's change of one account: each holding's change split over its lots by shares. Null when none of its holdings has one. */
export function accountDayChange(account: InvAccount, holdings: Holding[]): number | null {
  let total: number | null = null;
  for (const h of holdings) {
    if (h.day_change == null || !h.quantity) continue;
    for (const lot of h.lots) {
      if (lot.account_id === account.id) total = (total ?? 0) + h.day_change * (lot.quantity / h.quantity);
    }
  }
  return total;
}
