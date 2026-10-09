// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import type { BudgetSuggestions } from "$lib/api-types";
import { toast } from "svelte-sonner";
import BudgetSuggest from "./BudgetSuggest.svelte";

const reply = (extra: Partial<BudgetSuggestions> = {}): BudgetSuggestions => ({
  months: 6, first: "2026-03", last: "2026-08", recurring_month: "2026-10",
  suggestions: [
    { category: "Groceries", suggested: 520, typical: 517.4, recurring: 0, budget: 450 },
    { category: "Home", suggested: 1200, typical: 1100, recurring: 1200, budget: null },
    { category: "Fun", suggested: 80, typical: 76, recurring: 0, budget: 80 },
  ],
  ...extra,
});

// GET answers with the suggestions (`next` after the first time), POST /api/budget with `saved` (a function may throw).
function serve(first: BudgetSuggestions, next = first, saved: (body: { category: string }) => unknown = () => ({ ok: true, raised: [] })) {
  let gets = 0;
  vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string; body?: unknown }) => {
    if (opts?.method === "POST") return saved(opts.body as { category: string });
    return gets++ ? next : first;
  });
}
const posts = () => vi.mocked(api).mock.calls.filter(([, o]) => (o as { method?: string } | undefined)?.method === "POST")
  .map(([, o]) => (o as { body: unknown }).body);

async function openSheet(onchanged = vi.fn()) {
  render(BudgetSuggest, { month: "2026-09", onchanged });
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Suggest budgets" }));
  const dialog = await screen.findByRole("dialog", { name: "Suggested budgets" });
  await within(dialog).findByRole("list", { name: "Suggestions" });
  return { user, dialog, onchanged };
}

beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(toast.success).mockClear(); vi.mocked(toast.error).mockClear(); });

describe("BudgetSuggest", () => {
  it("loads nothing until opened, then shows each suggestion and what it's from", async () => {
    serve(reply());
    render(BudgetSuggest, { month: "2026-09" });
    expect(api).not.toHaveBeenCalled();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Suggest budgets" }));
    const dialog = await screen.findByRole("dialog", { name: "Suggested budgets" });
    expect(api).toHaveBeenCalledWith("/api/budget/suggestions?month=2026-09");
    await within(dialog).findByText("Groceries");
    expect(dialog).toHaveAccessibleDescription(/last 6 full months \(Mar–Aug\).*due in Oct 2026.*Nothing changes until you apply one/);
    expect(within(dialog).getByText("now $450")).toBeInTheDocument();
    expect(within(dialog).getByText("no budget")).toBeInTheDocument();
    expect(within(dialog).getByText("typical month $1,100 · bills $1,200")).toBeInTheDocument();
    // Fun's budget is the suggestion already: nothing to apply
    expect(within(dialog).getAllByRole("button", { name: /^Apply \$/ }).map((b) => b.getAttribute("aria-label")))
      .toEqual(["Apply $520 to Groceries", "Apply $1,200 to Home"]);
    expect(posts()).toEqual([]);   // nothing saved by looking
  });

  it("applies one suggestion through the budget's own save", async () => {
    serve(reply(), reply({ suggestions: reply().suggestions.map((x) => x.category === "Groceries" ? { ...x, budget: 520 } : x) }));
    const { user, dialog, onchanged } = await openSheet();
    await user.click(within(dialog).getByRole("button", { name: "Apply $520 to Groceries" }));
    await waitFor(() => expect(onchanged).toHaveBeenCalledTimes(1));
    expect(posts()).toEqual([{ category: "Groceries", amount: 520 }]);
    expect(toast.success).toHaveBeenCalledWith("Groceries budget set to $520");
    await waitFor(() => expect(within(dialog).queryByRole("button", { name: "Apply $520 to Groceries" })).not.toBeInTheDocument());
    expect(within(dialog).getByRole("button", { name: "Apply it" })).toBeInTheDocument();
  });

  it("says so when one can't be applied, and leaves the page as it was", async () => {
    serve(reply(), reply(), () => { throw new Error("Pick a spending or income category"); });
    const { user, dialog, onchanged } = await openSheet();
    await user.click(within(dialog).getByRole("button", { name: "Apply $520 to Groceries" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Pick a spending or income category"));
    expect(toast.success).not.toHaveBeenCalled();
    expect(onchanged).not.toHaveBeenCalled();
    expect(within(dialog).getByRole("button", { name: "Apply $520 to Groceries" })).toBeInTheDocument();
  });

  it("applies all of them, one at a time, skipping the ones already set", async () => {
    serve(reply(), reply({ suggestions: reply().suggestions.map((x) => ({ ...x, budget: x.suggested })) }));
    const { user, dialog, onchanged } = await openSheet();
    await user.click(within(dialog).getByRole("button", { name: "Apply all 2" }));
    await waitFor(() => expect(onchanged).toHaveBeenCalledTimes(1));
    expect(posts()).toEqual([{ category: "Groceries", amount: 520 }, { category: "Home", amount: 1200 }]);
    expect(toast.success).toHaveBeenCalledWith("2 budgets set");
    await waitFor(() => expect(within(dialog).queryByRole("button", { name: /^Apply/ })).not.toBeInTheDocument());
  });

  it("stops at a failure and says what was set before it", async () => {
    serve(reply(), reply(), (body) => { if (body.category === "Home") throw new Error("The amount is too large"); return { ok: true }; });
    const { user, dialog, onchanged } = await openSheet();
    await user.click(within(dialog).getByRole("button", { name: "Apply all 2" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("1 budget set, then stopped at Home: The amount is too large"));
    expect(toast.success).not.toHaveBeenCalled();
    expect(onchanged).toHaveBeenCalledTimes(1);   // Groceries was set: the page shows it
  });

  it("says when there's nothing to go on, and when loading fails", async () => {
    serve(reply({ months: 0, first: null, last: null, suggestions: [] }));
    render(BudgetSuggest, { month: "2026-12" });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Suggest budgets" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/Nothing to suggest yet/)).toBeInTheDocument();
    expect(dialog).toHaveAccessibleDescription(/No full month of spending yet, so just the recurring bills due in Oct 2026/);
    expect(within(dialog).queryByRole("button", { name: /^Apply/ })).not.toBeInTheDocument();
  });

  it("offers to try again when the suggestions can't be loaded", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("Server unavailable")).mockResolvedValue(reply());
    render(BudgetSuggest, { month: "2026-09" });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Suggest budgets" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Couldn’t work out suggestions: Server unavailable")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Try again" }));
    expect(await within(dialog).findByText("Groceries")).toBeInTheDocument();
  });
});
