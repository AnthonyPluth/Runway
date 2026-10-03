import { toast } from "svelte-sonner";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn() }));
vi.mock("$lib/app.svelte", () => ({ refreshState: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));

import { api } from "$lib/api";
import { refreshState } from "$lib/app.svelte";
import { askAlsoMatch, ruleOffer } from "./remember.svelte";

const offer = { merchant: "Blue Bottle" };
afterEach(() => { vi.clearAllMocks(); });

describe("the 'always use this category' offer", () => {
  it("is a button named for the merchant, and says what it would match when the API tells", () => {
    expect(ruleOffer("t1", "Coffee", offer, vi.fn())).toMatchObject({ also: { label: "Always for Blue Bottle" }, description: "" });
    expect(ruleOffer("t1", "Coffee", { merchant: "Whole Foods", match: "wholefds mkt", also_updated: 4, replaces: "Dining" }, vi.fn()).description)
      .toBe("matches “wholefds mkt” · instead of Dining · +4 more");
    expect(ruleOffer("t1", "Coffee", { merchant: "Whole Foods Market Downtown", match: "whole foods" }, vi.fn()))
      .toMatchObject({ also: { label: "Always" }, description: "for Whole Foods Market Downtown · matches “whole foods”" });
  });

  it("'Always' saves the category as a rule and tells you how many others changed", async () => {
    const reload = vi.fn();
    vi.mocked(api).mockResolvedValue({ also_updated: 3 });
    await ruleOffer("a/b", "Coffee", offer, reload).also.run();
    expect(api).toHaveBeenCalledWith("/api/transactions/a%2Fb/category", { method: "POST", body: { category: "Coffee", remember: true } });
    expect(toast.success).toHaveBeenCalledWith("From now on, Blue Bottle is Coffee · 3 more updated");
    expect(refreshState).toHaveBeenCalled();
    expect(reload).toHaveBeenCalled();
  });

  it("doesn't reload the list when no other transaction changed", async () => {
    const reload = vi.fn();
    vi.mocked(api).mockResolvedValue({ also_updated: 0 });
    await ruleOffer("t1", "Coffee", offer, reload).also.run();
    expect(toast.success).toHaveBeenCalledWith("From now on, Blue Bottle is Coffee");
    expect(reload).not.toHaveBeenCalled();
  });

  it("lets a failure through, for the toast to show", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Rule exists"));
    await expect(ruleOffer("t1", "Coffee", offer, vi.fn()).also.run()).rejects.toThrow("Rule exists");
  });
});

describe("the 'also match this text' offer", () => {
  type Opts = { description: string; action: { label: string; onClick: () => Promise<void> } };
  it("asks in a toast, and adds the text when you say so", async () => {
    vi.mocked(api).mockResolvedValue({ ok: true, linked: 2 });
    const reload = vi.fn();
    askAlsoMatch(1, "Paycheck", "online transfer from savings", reload);
    const [msg, opts] = vi.mocked(toast).mock.calls[0] as [string, Opts];
    expect(msg).toBe("Linked to Paycheck");
    expect(opts.description).toBe("Also match “online transfer from savings” from now on?");
    await opts.action.onClick();
    expect(api).toHaveBeenCalledWith("/api/recurring/1/match", { method: "POST", body: { text: "online transfer from savings" } });
    expect(toast.success).toHaveBeenCalledWith("Paycheck also matches “online transfer from savings” now · 2 more linked");
    expect(reload).toHaveBeenCalled();
  });

  it("says so when the text can't be added", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Use at least three letters of text"));
    askAlsoMatch(1, "Paycheck", "churn", vi.fn());
    await (vi.mocked(toast).mock.calls[0] as [string, Opts])[1].action.onClick();
    expect(toast.error).toHaveBeenCalledWith("Use at least three letters of text");
  });
});
