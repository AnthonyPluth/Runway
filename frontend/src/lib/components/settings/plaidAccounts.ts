// Plaid's bank and card accounts (GET /api/plaid/status), flattened across connections for Settings → Accounts.
// Investment items are left out: their accounts match against `candidates` and are still matched under Bank connections.
import type { PlaidAccount, PlaidItem, PlaidStatus } from "./types";

export interface PlaidBankAccount { p: PlaidAccount; it: PlaidItem }

export function bankAccounts(st: PlaidStatus | null): PlaidBankAccount[] {
  return (st?.items ?? []).filter((it) => it.bank).flatMap((it) => it.accounts.filter((p) => p.type !== "investment").map((p) => ({ p, it })));
}
/** Waiting for a decision: not matched to one of your accounts and not left out. */
export const undecidedAccounts = (st: PlaidStatus | null) => bankAccounts(st).filter(({ p }) => !p.account_id && !p.ignored);
/** Left out with "Don't use". */
export const ignoredAccounts = (st: PlaidStatus | null) => bankAccounts(st).filter(({ p }) => !p.account_id && !!p.ignored);
/** What an account row can be linked to: everything not matched yet, including what you left out before. */
export const linkable = (st: PlaidStatus | null) => bankAccounts(st).filter(({ p }) => !p.account_id);
/** The Plaid account (and its connection) behind one of your accounts. */
export const plaidFor = (st: PlaidStatus | null, accountId: string) => bankAccounts(st).find(({ p }) => p.account_id === accountId);

export const plaidLabel = ({ p, it }: PlaidBankAccount) =>
  `${it.institution_name ? it.institution_name + " " : ""}${p.name || p.official_name || "Account"}${p.mask ? ` ••${p.mask}` : ""}`;
