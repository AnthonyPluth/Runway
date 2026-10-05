// What the web app knows about account types, so the lists aren't repeated page by page. The types are the ones the
// server accepts (runway/server/api/accounts.py KINDS).
import { apiCall } from "./contract";
import type { AccountItem } from "./api-types";

export const ACCOUNT_KINDS = ["checking", "savings", "credit", "loan", "investment"] as const;
export type AccountKind = (typeof ACCOUNT_KINDS)[number];

/** How a type reads in a list or a heading. */
export const KIND_LABEL: Record<AccountKind, string> = { checking: "Checking", savings: "Savings", credit: "Credit card", loan: "Loan", investment: "Investment" };

/** Accounts that hold spending money: what the forecast can follow. */
const CASH_KINDS: readonly AccountKind[] = ["checking", "savings"];
/** Accounts a Plaid bank connection can be matched to (investments connect separately). */
const BANK_KINDS: readonly AccountKind[] = ["checking", "savings", "credit", "loan"];
/** Accounts a category's spending can be put on. */
const PAYING_KINDS: readonly AccountKind[] = ["credit", "checking", "savings"];
/** Settings → Accounts' groups, in order. */
export const KIND_GROUPS: readonly (readonly [string, readonly AccountKind[]])[] = [
  ["Cash", CASH_KINDS], ["Credit cards", ["credit"]], ["Loans", ["loan"]], ["Investments", ["investment"]],
];

export const isCash = (kind: string) => (CASH_KINDS as readonly string[]).includes(kind);
export const isBankKind = (kind: string) => (BANK_KINDS as readonly string[]).includes(kind);
export const isPayingKind = (kind: string) => (PAYING_KINDS as readonly string[]).includes(kind);

/** Whether an account id is one Runway made for an account that came only from Plaid ("pl:…"), rather than one
 * SimpleFIN or you added. */
export const isPlaidStub = (id: string | null | undefined): boolean => !!id?.startsWith("pl:");

/** A row of GET /api/accounts as loadAccounts gives it. */
export type LoadedAccount = Omit<AccountItem, "kind" | "hidden"> & { kind: AccountKind; hidden: boolean };

/** GET /api/accounts, with `hidden` as the yes/no it means (the server sends 0 or 1), and each account's type one of
 * ACCOUNT_KINDS (the only ones the server keeps: runway/server/api/accounts.py KINDS). */
export async function loadAccounts(): Promise<LoadedAccount[]> {
  const rows = await apiCall<"GET /api/accounts">("/api/accounts");
  return rows.map((a) => ({ ...a, kind: a.kind as AccountKind, hidden: !!a.hidden }));
}
