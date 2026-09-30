// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import type { BudgetCategory, BudgetMonth } from "$lib/components/budget/types";
import { toast } from "svelte-sonner";
import Budget from "./Budget.svelte";

const cat = (name: string, extra: Partial<BudgetCategory> = {}): BudgetCategory => ({
  name, parent: null, path: [name], depth: 0, top: name, has_children: false, budget: null, pay_with: null, usual_account: null,
  spent: 0, own_spent: 0, left: null, ...extra,
});
const month = (extra: Partial<BudgetMonth> = {}): BudgetMonth => ({
  month: "2026-03", days_in_month: 31, day: 10, income: 4000, uncategorized: 0, pay_accounts: [],
  categories: [
    cat("Groceries", { budget: 500, spent: 200, left: 300, has_children: true }),
    cat("Produce", { parent: "Groceries", path: ["Groceries", "Produce"], depth: 1, top: "Groceries", spent: 50 }),
    cat("Dining", { budget: 100, spent: 160, left: -60 }),
    cat("Gas", { spent: 80 }),
    cat("Pets"),
  ],
  ...extra,
});
const serve = (m: BudgetMonth | ((path: string) => BudgetMonth)) => vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
  if (path === "/api/categories") return [] as never;
  if (path.startsWith("/api/budget?") && !opts?.method) return (typeof m === "function" ? m(path) : m) as never;
  return {} as never;
});

// The page remembers the month you were on (module state), so tests that move it put it back at March.
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-03-15T12:00:00") });
  vi.mocked(api).mockReset();
  vi.mocked(toast.error).mockClear();
});

afterEach(() => { vi.useRealTimers(); });

describe("Budget page", () => {
  it("shows the month's totals across the budgeted categories", async () => {
    serve(month());
    render(Budget);
    expect(await screen.findByText("Budgeted")).toBeInTheDocument();
    expect(screen.getByText("$600")).toBeInTheDocument();                       // 500 + 100
    expect(screen.getByText("$360")).toBeInTheDocument();                       // 200 + 160
    expect(screen.getByText("$300 left · ▲ $60 over in 1 budget")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Budget" })).toBeInTheDocument();
    expect(screen.getByText("March 2026")).toBeInTheDocument();
  });

  it("counts what's spent outside any budget as other spending, and mentions uncategorized", async () => {
    serve(month({ uncategorized: 25 }));
    render(Budget);
    // Gas 80 is unbudgeted; the 25 uncategorized goes on top
    expect(await screen.findByText("$105")).toBeInTheDocument();
    expect(screen.getByText("incl. $25 uncategorized")).toBeInTheDocument();
  });

  it("puts budgeted categories under Budgets and spending without a budget under Not budgeted", async () => {
    serve(month());
    render(Budget);
    await screen.findByText("Budgeted");
    const budgets = screen.getByRole("heading", { name: "Budgets" }).closest("section")!;
    const not = screen.getByRole("heading", { name: "Not budgeted" }).closest("section")!;
    expect(within(budgets).getByRole("link", { name: "Groceries" })).toBeInTheDocument();
    expect(within(budgets).getByRole("link", { name: "Dining" })).toBeInTheDocument();
    expect(within(not).getByRole("link", { name: "Gas" })).toBeInTheDocument();
    expect(within(not).queryByRole("link", { name: "Pets" })).not.toBeInTheDocument();   // nothing spent: only in the picker
    expect(within(not).getByRole("option", { name: "Pets" })).toBeInTheDocument();
  });

  it("says so when there are no budgets", async () => {
    serve(month({ categories: [cat("Gas", { spent: 80 })] }));
    render(Budget);
    expect(await screen.findByText("No budgets yet. Set one below.")).toBeInTheDocument();
    expect(screen.getByText("Set a budget below")).toBeInTheDocument();
  });

  it("goes to the previous and next month", async () => {
    serve((path) => month({ month: path.split("=")[1] }));
    render(Budget);
    await screen.findByText("March 2026");
    await userEvent.click(screen.getByRole("button", { name: "Previous month" }));
    await screen.findByText("February 2026");
    expect(api).toHaveBeenCalledWith("/api/budget?month=2026-02");
    await userEvent.click(screen.getByRole("button", { name: "Next month" }));
    await userEvent.click(screen.getByRole("button", { name: "Next month" }));
    await screen.findByText("April 2026");
    await userEvent.click(screen.getByRole("button", { name: "Previous month" }));
    await screen.findByText("March 2026");
  });

  it("wraps from January to the year before", async () => {
    serve((path) => month({ month: path.split("=")[1] }));
    render(Budget);
    await screen.findByText("Budgeted");
    for (let i = 0; i < 3; i++) await userEvent.click(screen.getByRole("button", { name: "Previous month" }));
    await screen.findByText("December 2025");
    for (let i = 0; i < 3; i++) await userEvent.click(screen.getByRole("button", { name: "Next month" }));
    await screen.findByText("March 2026");
  });

  it("saves a changed budget and shows the month again", async () => {
    serve(month());
    render(Budget);
    await screen.findByText("Budgeted");
    const box = screen.getByLabelText("Budget for Gas");
    await userEvent.type(box, "120");
    await userEvent.tab();
    expect(api).toHaveBeenCalledWith("/api/budget", { method: "POST", body: { category: "Gas", amount: "120" } });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Budget saved"));
  });

  it("removes a budget when its box is emptied", async () => {
    serve(month());
    render(Budget);
    await screen.findByText("Budgeted");
    await userEvent.clear(screen.getByLabelText("Budget for Dining"));
    await userEvent.tab();
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Budget removed"));
  });

  it("asks to pick a category before setting a budget for 'another category'", async () => {
    serve(month());
    render(Budget);
    await screen.findByText("Budgeted");
    const box = screen.getByLabelText("Budget for the chosen category");
    await userEvent.type(box, "50");
    await userEvent.tab();
    expect(toast.error).toHaveBeenCalledWith("Choose a category first");
    await userEvent.selectOptions(screen.getByLabelText("Category to budget"), "Pets");
    await userEvent.clear(box);
    await userEvent.type(box, "40");
    await userEvent.tab();
    expect(api).toHaveBeenCalledWith("/api/budget", { method: "POST", body: { category: "Pets", amount: "40" } });
  });

  it("shows the error with a way to try again", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => { if (path === "/api/categories") return [] as never; throw new Error("Server down"); });
    render(Budget);
    expect(await screen.findByText("Something went wrong: Server down")).toBeInTheDocument();
    serve(month());
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Budgeted")).toBeInTheDocument();
  });

  it("mentions the month's income", async () => {
    serve(month());
    render(Budget);
    expect(await screen.findByText("Money in this month: $4,000.00")).toBeInTheDocument();
  });
});

describe("Budget tabs", () => {
  it("has Budget and Bills & income tabs, with the month picker only on Budget", async () => {
    serve(month());
    const { unmount } = render(Budget);
    const tabs = screen.getByRole("navigation", { name: "Budget" });
    expect(within(tabs).getByRole("link", { name: "Budget" })).toHaveAttribute("aria-current", "page");
    expect(within(tabs).getByRole("link", { name: "Bills & income" })).toHaveAttribute("href", "#budget/recurring");
    expect(await screen.findByRole("button", { name: "Previous month" })).toBeInTheDocument();
    unmount();

    vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/accounts" || path === "/api/recurring" ? [] : {})) as never);
    render(Budget, { sub: "recurring" });
    expect(screen.getByRole("link", { name: "Bills & income" })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByRole("button", { name: "Add" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Previous month" })).not.toBeInTheDocument();
  });
});
