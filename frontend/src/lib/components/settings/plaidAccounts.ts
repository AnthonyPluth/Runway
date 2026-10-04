// Plaid's accounts (GET /api/plaid/status), flattened across connections for Settings → Accounts. Bank and card accounts
// match your accounts; an investment item's accounts match its `candidates` (your investment accounts at that institution),
// and "left out" is stored as account_id "ignore" rather than a flag. Investment ones are normalised here so both read alike.
import { isBankKind, isPlaidStub } from "$lib/accounts";
import type { PlaidAccount, PlaidItem, PlaidStatus, SettingsAccount } from "./types";

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

/** Where one of your accounts' balances and transactions come from, as its row in Settings → Accounts shows it: the Plaid
 * link (if any), whether Plaid made the account itself, which Plaid account is behind it, and the words for all that. */
export function sourceInfo(a: SettingsAccount, plaid: PlaidStatus | null) {
  const link = a.plaid_link;
  // Where balances and transactions come from: a choice once the account is matched to a Plaid account.
  const canSwitch = !isPlaidStub(a.id) && !!link?.transactions;
  const where = `${link?.institution || "Plaid"}${link?.mask ? ` ••${link.mask}` : ""}`;
  const own = isPlaidStub(a.id);
  // The Plaid account behind this one (its connection says when it last synced).
  const behind = plaidFor(plaid, a.id);
  const linkKind = isBankKind(a.kind);
  // An investment account has no plaid_link: a Plaid account is tied to it by that account's own choice (among its connection's candidates).
  const invKind = a.kind === "investment";
  const invLinked = invKind && !own ? behind : undefined;
  // Shown once Plaid is set up, or this account already uses it.
  const showSource = !!link || own || !!invLinked || ((linkKind || invKind) && !!plaid && (plaid.configured || plaid.items.length > 0));
  const mask = link?.mask ? ` ••${link.mask}` : behind?.p.mask && (own || invLinked) ? ` ••${behind.p.mask}` : "";
  const source = own ? `Plaid${mask}` : link || invLinked ? `SimpleFIN + Plaid${mask}` : "SimpleFIN";
  return { link, canSwitch, where, own, behind, linkKind, invKind, invLinked, showSource, source };
}
