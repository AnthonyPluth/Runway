import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ api: vi.fn() }));

import { ACCOUNT_KINDS, isBankKind, isCash, isPayingKind, isPlaidStub, KIND_GROUPS, KIND_LABEL, loadAccounts } from "./accounts";
import { api } from "./api";

beforeEach(() => vi.mocked(api).mockReset());

describe("account types", () => {
  it("has a label for each type the server accepts, and groups that cover them all once", () => {
    expect(Object.keys(KIND_LABEL).sort()).toEqual([...ACCOUNT_KINDS].sort());
    expect(KIND_GROUPS.flatMap(([, kinds]) => kinds).sort()).toEqual([...ACCOUNT_KINDS].sort());
  });

  it("says which types hold cash, can link to a bank, and can pay for a category", () => {
    expect(["checking", "savings", "credit", "loan", "investment"].map(isCash)).toEqual([true, true, false, false, false]);
    expect(["checking", "savings", "credit", "loan", "investment"].map(isBankKind)).toEqual([true, true, true, true, false]);
    expect(["checking", "savings", "credit", "loan", "investment"].map(isPayingKind)).toEqual([true, true, true, false, false]);
    expect(isCash("")).toBe(false);
  });

  it("knows an account Plaid made on its own by its id", () => {
    expect(isPlaidStub("pl:abc")).toBe(true);
    expect(isPlaidStub("ACT-1")).toBe(false);
    expect(isPlaidStub(null)).toBe(false);
    expect(isPlaidStub(undefined)).toBe(false);
  });
});

describe("loadAccounts", () => {
  it("turns the server's 0 and 1 into hidden: false and true, leaving the rest as sent", async () => {
    vi.mocked(api).mockResolvedValue([
      { id: "a", name: "A", kind: "checking", hidden: 0, balance: 5 },
      { id: "b", name: "B", kind: "credit", hidden: 1 },
      { id: "c", name: "C", kind: "loan" },
    ] as never);
    expect(await loadAccounts()).toEqual([
      { id: "a", name: "A", kind: "checking", hidden: false, balance: 5 },
      { id: "b", name: "B", kind: "credit", hidden: true },
      { id: "c", name: "C", kind: "loan", hidden: false },
    ]);
    expect(api).toHaveBeenCalledWith("/api/accounts");
  });

  it("lets a failure through for the page to show", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("Can’t reach Runway."));
    await expect(loadAccounts()).rejects.toThrow("Can’t reach Runway.");
  });
});
