import { toast } from "svelte-sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn() }));
vi.mock("$lib/app.svelte", () => ({ refreshState: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));

import { api } from "$lib/api";
import { refreshState } from "$lib/app.svelte";
import { always, askRemember, closeRemember, remember } from "./remember.svelte";

const offer = { merchant: "Blue Bottle" };
beforeEach(() => vi.useFakeTimers());
afterEach(() => { closeRemember(); vi.useRealTimers(); vi.clearAllMocks(); });

describe("the 'always use this category' question", () => {
  it("stays up for 12 seconds, then goes away by itself", () => {
    askRemember("t1", "Coffee", offer, vi.fn());
    expect(remember.ask).toMatchObject({ txId: "t1", category: "Coffee" });
    vi.advanceTimersByTime(11_900);
    expect(remember.ask).not.toBeNull();
    vi.advanceTimersByTime(200);
    expect(remember.ask).toBeNull();
  });

  it("replaces an earlier question, and restarts the clock", () => {
    askRemember("t1", "Coffee", offer, vi.fn());
    vi.advanceTimersByTime(10_000);
    askRemember("t2", "Dining", offer, vi.fn());
    vi.advanceTimersByTime(10_000);
    expect(remember.ask?.txId).toBe("t2");
  });

  it("clears other toasts so two messages don't stack", () => {
    askRemember("t1", "Coffee", offer, vi.fn());
    expect(toast.dismiss).toHaveBeenCalled();
  });

  it("'Always' saves the category as a rule and tells you how many others changed", async () => {
    const reload = vi.fn();
    askRemember("a/b", "Coffee", offer, reload);
    vi.mocked(api).mockResolvedValue({ also_updated: 3 });
    await always();
    expect(api).toHaveBeenCalledWith("/api/transactions/a%2Fb/category", { method: "POST", body: { category: "Coffee", remember: true } });
    expect(toast.success).toHaveBeenCalledWith("From now on, Blue Bottle is Coffee · 3 more updated");
    expect(refreshState).toHaveBeenCalled();
    expect(reload).toHaveBeenCalled();
    expect(remember.ask).toBeNull();
  });

  it("doesn't reload the list when no other transaction changed", async () => {
    const reload = vi.fn();
    askRemember("t1", "Coffee", offer, reload);
    vi.mocked(api).mockResolvedValue({ also_updated: 0 });
    await always();
    expect(toast.success).toHaveBeenCalledWith("From now on, Blue Bottle is Coffee");
    expect(reload).not.toHaveBeenCalled();
  });

  it("shows the error if saving the rule fails", async () => {
    askRemember("t1", "Coffee", offer, vi.fn());
    vi.mocked(api).mockRejectedValue(new Error("Rule exists"));
    await always();
    expect(toast.error).toHaveBeenCalledWith("Rule exists");
  });

  it("does nothing when there's no question open", async () => {
    await always();
    expect(api).not.toHaveBeenCalled();
  });
});
