import { vi } from "vitest";
import { api } from "$lib/api";
import type {
  PlaidItem,
  PlaidStatus,
  SettingsAccount,
} from "../lib/components/settings/types";

export const acct = (over: Partial<SettingsAccount> = {}): SettingsAccount => ({
  id: "chk",
  name: "Checking",
  kind: "checking",
  balance: 1000,
  ...over,
});
export const card = acct({
  id: "amex",
  name: "Amex Gold",
  kind: "credit",
  balance: -400,
  plaid_account_id: null,
  plaid_link: null,
});
export const item = (
  accounts: PlaidItem["accounts"],
  over: Partial<PlaidItem> = {},
): PlaidItem => ({
  item_id: "it1",
  institution_name: "Chase",
  products: ["transactions"],
  bank: true,
  last_sync: "2026-09-30 15:00:00",
  accounts,
  ...over,
});
export const status = (
  items: PlaidItem[],
  over: Partial<PlaidStatus> = {},
): PlaidStatus => ({
  configured: true,
  env: "sandbox",
  client_id: "x",
  items,
  last_inv_sync: null,
  syncing: false,
  inv_accounts: 0,
  simplefin_connected: false,
  simplefin_last_sync: null,
  simplefin_seen: [],
  ...over,
});
export const unmatched = {
  id: "pa1",
  name: "Freedom",
  subtype: "credit card",
  type: "credit",
  mask: "4242",
  balance: -120,
  ignored: 0,
  account_id: null,
};
export const matched = {
  id: "pa2",
  name: "Total Checking",
  type: "depository",
  mask: "1111",
  balance: 900,
  ignored: 0,
  account_id: "chk",
};
export const ignored = {
  id: "pa3",
  name: "Old Savings",
  type: "depository",
  mask: "9999",
  balance: 5,
  ignored: 1,
  account_id: null,
};

export const serve = (st: PlaidStatus | Error) =>
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/api/plaid/status") {
      if (st instanceof Error) throw st;
      return st;
    }
    return { ok: true };
  });
export const matchCall = () =>
  vi.mocked(api).mock.calls.find(([p]) => p === "/api/plaid/match");
