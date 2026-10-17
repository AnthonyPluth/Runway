import { describe, expect, it } from "vitest";
import { holding } from "../../../test/fixtures";
import { accountDayChange, foldAllocation } from "./allocation";
import type { InvAccount } from "./types";

const acct = { id: "a1" } as InvAccount;
const lot = (account_id: string, quantity: number) => ({ account_id, security_id: "s1", account_name: "", quantity, value: quantity * 250, cost_basis: null, reported_cost_basis: null, per_share: null, manual: false });

describe("accountDayChange", () => {
  it("is null when no holding in the account has a change", () => {
    expect(accountDayChange(acct, [holding({ day_change: null, lots: [lot("a1", 10)] })])).toBeNull();
  });

  it("is null for an account none of the lots belong to", () => {
    expect(accountDayChange(acct, [holding({ day_change: 12, lots: [lot("a2", 10)] })])).toBeNull();
  });

  it("leaves out a lot of no shares, and a holding of no shares, without dividing by zero", () => {
    expect(accountDayChange(acct, [holding({ quantity: 10, day_change: 12, lots: [lot("a1", 0), lot("a2", 10)] })])).toBe(0);
    expect(accountDayChange(acct, [holding({ quantity: 0, day_change: 12, lots: [lot("a1", 0)] })])).toBeNull();
  });

  it("splits a holding's change over its lots by shares", () => {
    expect(accountDayChange(acct, [holding({ quantity: 10, day_change: 12, lots: [lot("a1", 5), lot("a2", 5)] })])).toBe(6);
  });
});

describe("foldAllocation", () => {
  const item = (name: string, share: number) => ({ name, value: share * 1000, share });

  it("keeps equal shares in the order they came, and folds the last of them into Other", () => {
    const rows = foldAllocation(["A", "B", "C", "D", "E", "F", "G"].map((n) => item(n, 0.1)));
    expect(rows.map((r) => r.name)).toEqual(["A", "B", "C", "D", "E", "Other"]);
    expect(rows[5].other).toBe(true);
    expect(rows[5].share).toBeCloseTo(0.2);
  });

  it("folds a group the data calls Other into the one Other row, so no name repeats", () => {
    const rows = foldAllocation([item("Other", 0.3), ...["A", "B", "C", "D", "E", "F"].map((n, i) => item(n, 0.2 - i * 0.01))]);
    expect(rows.map((r) => r.name)).toEqual(["A", "B", "C", "D", "E", "Other"]);
    expect(rows[5].share).toBeCloseTo(0.3 + 0.15);
    expect(rows[5].value).toBeCloseTo(450);
  });

  it("sorts up to six groups largest first without folding", () => {
    const rows = foldAllocation([item("S", 0.1), item("L", 0.5), item("M", 0.4)]);
    expect(rows.map((r) => r.name)).toEqual(["L", "M", "S"]);
  });
});
