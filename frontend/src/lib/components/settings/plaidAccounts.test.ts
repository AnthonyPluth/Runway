import { describe, expect, it } from "vitest";
import { sourceInfo } from "./plaidAccounts";
import type { PlaidStatus, SettingsAccount } from "./types";

const acct = (over: Partial<SettingsAccount> = {}): SettingsAccount => ({ id: "chk", name: "Checking", kind: "checking", ...over });
const plaid = (accounts: PlaidStatus["items"][number]["accounts"] = [], configured = true): PlaidStatus => ({
  configured, env: "sandbox", client_id: "x", last_inv_sync: null, syncing: false, inv_accounts: 0, simplefin_connected: false, simplefin_last_sync: null, simplefin_seen: [],
  items: [{ item_id: "it1", institution_name: "Chase", products: ["transactions"], bank: true, last_sync: null, accounts }],
});

describe("sourceInfo", () => {
  it("is plain SimpleFIN for an account with no Plaid link, and offers no source section without Plaid", () => {
    const i = sourceInfo(acct(), null);
    expect(i.source).toBe("SimpleFIN");
    expect(i.showSource).toBe(false);
    expect(i.canSwitch).toBe(false);
  });

  it("offers linking once Plaid is set up, or a connection exists", () => {
    expect(sourceInfo(acct(), plaid()).showSource).toBe(true);
    expect(sourceInfo(acct({ kind: "investment" }), plaid()).showSource).toBe(true);
    expect(sourceInfo(acct({ kind: "checking" }), plaid([], false)).showSource).toBe(true);
  });

  it("says SimpleFIN + Plaid for a linked account, with the mask, and lets you switch where it comes from", () => {
    const i = sourceInfo(acct({ plaid_link: { transactions: true, institution: "Chase", mask: "1234" } }), plaid());
    expect(i.source).toBe("SimpleFIN + Plaid ••1234");
    expect(i.where).toBe("Chase ••1234");
    expect(i.canSwitch).toBe(true);
  });

  it("calls an account Plaid made on its own Plaid, and doesn't offer to switch it", () => {
    const behind = { id: "pa1", name: "Savings", type: "depository", mask: "9876", balance: 5, ignored: 0, account_id: "pl:pa1" };
    const i = sourceInfo(acct({ id: "pl:pa1" }), plaid([behind]));
    expect(i.own).toBe(true);
    expect(i.source).toBe("Plaid ••9876");
    expect(i.canSwitch).toBe(false);
    expect(i.behind?.p.id).toBe("pa1");
  });
});
