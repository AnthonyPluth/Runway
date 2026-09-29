// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { showTransactions, txFilters } from "./filters.svelte";

beforeEach(() => { showTransactions({}); location.hash = ""; });

describe("showTransactions", () => {
  it("sets the filters and goes to the Transactions page", () => {
    showTransactions({ category: "Groceries", month: "2026-03" });
    expect(txFilters.transactions).toEqual({ q: "", account: "", category: "Groceries", month: "2026-03", scope: "" });
    expect(location.hash).toBe("#transactions");
  });

  it("clears filters that aren't given, so an old search doesn't hide the new view", () => {
    showTransactions({ q: "rent", account: "a1" });
    showTransactions({ category: "Dining" });
    expect(txFilters.transactions).toMatchObject({ q: "", account: "", category: "Dining" });
  });

  it("leaves the Review tab's filters alone", () => {
    txFilters.review.q = "kept";
    showTransactions({ q: "other" });
    expect(txFilters.review.q).toBe("kept");
    txFilters.review.q = "";
  });
});
