// Plaid's accounts (GET /api/plaid/status), flattened across connections for Settings → Accounts. Bank and card accounts
// match your accounts; an investment item's accounts match its `candidates` (your investment accounts at that institution),
// and "left out" is stored as account_id "ignore" rather than a flag. Investment ones are normalised here so both read alike.
import type { PlaidAccount, PlaidItem, PlaidStatus } from "./types";

export interface PlaidBankAccount { p: PlaidAccount; it: PlaidItem }

/** An investment connection's account, with "ignore" turned into the `ignored` flag the bank accounts use. */
const investment = (p: PlaidAccount): PlaidAccount => p.account_id === "ignore" ? { ...p, account_id: null, ignored: 1 } : { ...p, ignored: 0 };

function bankAccounts(st: PlaidStatus | null): PlaidBankAccount[] {
  return (st?.items ?? []).filter((it) => it.bank).flatMap((it) => it.accounts.filter((p) => p.type !== "investment").map((p) => ({ p, it })));
}
function investmentAccounts(st: PlaidStatus | null): PlaidBankAccount[] {
  return (st?.items ?? []).filter((it) => !it.bank).flatMap((it) => it.accounts.map((p) => ({ p: investment(p), it })));
}
const allAccounts = (st: PlaidStatus | null) => [...bankAccounts(st), ...investmentAccounts(st)];

/** Waiting for a decision: not matched to one of your accounts and not left out. */
export const undecidedAccounts = (st: PlaidStatus | null) => allAccounts(st).filter(({ p }) => !p.account_id && !p.ignored);
/** Left out with "Don't use". */
export const ignoredAccounts = (st: PlaidStatus | null) => allAccounts(st).filter(({ p }) => !p.account_id && !!p.ignored);
/** What a bank or card account row can be linked to: everything not matched yet, including what you left out before. */
export const linkable = (st: PlaidStatus | null) => bankAccounts(st).filter(({ p }) => !p.account_id);
/** What an investment account row can be linked to: its institution's Plaid accounts not matched yet. */
export const linkableInvestments = (st: PlaidStatus | null, accountId: string) =>
  investmentAccounts(st).filter(({ p, it }) => !p.account_id && (it.candidates ?? []).some((c) => c.id === accountId && (!c.linked_to || c.linked_to === p.id)));
/** The Plaid account (and its connection) behind one of your accounts. */
export const plaidFor = (st: PlaidStatus | null, accountId: string) => allAccounts(st).find(({ p }) => p.account_id === accountId);

export const plaidLabel = ({ p, it }: PlaidBankAccount) =>
  `${it.institution_name ? it.institution_name + " " : ""}${p.name || p.official_name || "Account"}${p.mask ? ` ••${p.mask}` : ""}`;
