// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
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
  app.state = { connected: true };
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

  it("puts each row's account on its emoji: its own, else its parent's, else the one used most", async () => {
    app.state = { connected: true, brands: { c1: { initial: "V" }, c2: { initial: "A" }, b1: { initial: "C" } } };
    serve(month({
      pay_accounts: [{ id: "c1", name: "Visa", kind: "credit" }, { id: "c2", name: "Amex", kind: "credit" }, { id: "b1", name: "Checking", kind: "checking" }],
      categories: [
        cat("Groceries", { budget: 500, spent: 200, left: 300, has_children: true, pay_with: "c1" }),
        cat("Produce", { parent: "Groceries", path: ["Groceries", "Produce"], depth: 1, top: "Groceries", budget: 50, spent: 50, left: 0, usual_account: "b1" }),
        cat("Bakery", { parent: "Groceries", path: ["Groceries", "Bakery"], depth: 1, top: "Groceries", budget: 30, spent: 10, left: 20, pay_with: "c2" }),
        cat("Gas", { spent: 80, usual_account: "b1" }),
        cat("Pets", { spent: 20 }),
      ],
    }));
    const { container } = render(Budget);
    await screen.findByText("Budgeted");
    const badge = (name: string) => {
      const link = screen.getByRole("link", { name });
      return link.parentElement!.querySelector("[data-account-badge]")?.getAttribute("title") ?? null;
    };
    expect([badge("Groceries"), badge("Produce"), badge("Bakery"), badge("Gas"), badge("Pets")]).toEqual(["Visa", "Visa", "Amex", "Checking", null]);
    expect(container.querySelectorAll("[data-account-badge]")).toHaveLength(4);
  });

  it("puts the even-pace marker halfway through today", async () => {
    serve(month({ day: 2 }));
    render(Budget);
    const marks = await screen.findAllByTitle("Where you'd be at an even pace today");
    expect(marks[0].style.left).toBe(`${((1.5 / 31) * 100).toFixed(1)}%`);
  });

  it("keeps the marker short of the end on the month's last day, and has none for a finished month", async () => {
    serve(month({ day: 31 }));
    const { unmount } = render(Budget);
    const marks = await screen.findAllByTitle("Where you'd be at an even pace today");
    expect(marks[0].style.left).toBe(`${((30.5 / 31) * 100).toFixed(1)}%`);
    unmount();
    vi.setSystemTime(new Date("2026-04-02T12:00:00"));
    render(Budget);
    await screen.findByText("Budgeted");
    expect(screen.queryByTitle("Where you'd be at an even pace today")).not.toBeInTheDocument();
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

  it("says when saving a subcategory's budget raised its parent's, in the same toast", async () => {
    vi.mocked(toast.success).mockClear();
    vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
      if (opts?.method === "POST") return { ok: true, raised: [{ category: "Groceries", amount: 550 }] } as never;
      return (path.startsWith("/api/budget?") ? month() : []) as never;
    });
    render(Budget);
    await screen.findByText("Budgeted");
    await userEvent.type(screen.getByLabelText("Budget for Produce"), "350");
    await userEvent.tab();
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Budget saved · Groceries raised to $550"));
    expect(toast.success).toHaveBeenCalledTimes(1);
  });

  describe("income", () => {
    const withIncome = (budget: number | null) => month({
      income: 3150,
      income_rows: [cat("Income", { budget, spent: 3150, own_spent: 3150, left: budget == null ? null : budget - 3150, expected: 3150 }),
        cat("Side gigs", { spent: 0 })],
    });

    it("has its own group above the budgets, and the strip says what's come in of what's expected", async () => {
      serve(withIncome(6300));
      render(Budget);
      const group = await screen.findByRole("heading", { name: "Income" });
      expect(group.compareDocumentPosition(screen.getByRole("heading", { name: "Budgets" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(screen.getByText("$3,150 to come · $3,150 scheduled")).toBeInTheDocument();
      expect(screen.getByText("of $6,300 expected")).toBeInTheDocument();
      // the spending totals leave it out
      expect(screen.getByText("$600")).toBeInTheDocument();
      expect(screen.getByText("$300 left · ▲ $60 over in 1 budget")).toBeInTheDocument();
      // an income category without a budget can be given one from the list at the bottom
      const income = within(screen.getByLabelText("Category to budget")).getByRole("group", { name: "Income" });
      expect(within(income).getAllByRole("option").map((o) => o.textContent)).toEqual(["Side gigs"]);
    });

    it("keeps the strip's money in as it was without an income budget", async () => {
      serve(withIncome(null));
      render(Budget);
      await screen.findByRole("heading", { name: "Income" });   // money came in: its row offers a box for what's expected
      expect(screen.getByLabelText("Budget for Income")).toHaveAttribute("placeholder", "Expected");
      expect(screen.queryByText(/expected$/)).not.toBeInTheDocument();
    });

    it("isn't there with no income budget and nothing come in", async () => {
      serve(month({ income: 0, income_rows: [cat("Income")] }));
      render(Budget);
      await screen.findByText("Budgeted");
      expect(screen.queryByRole("heading", { name: "Income" })).not.toBeInTheDocument();
    });
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

  it("puts the month's income in the strip with the totals", async () => {
    serve(month());
    render(Budget);
    const label = await screen.findByText("Money in");
    expect(label.closest("div")).toHaveTextContent("Money in $4,000");
    expect(screen.queryByText(/Money in this month/)).not.toBeInTheDocument();
  });
});

describe("Budget page", () => {
  it("has no sub-tab strip now that Recurring is a page of its own, and shows the month picker", async () => {
    serve(month());
    render(Budget);
    expect(screen.queryByRole("navigation", { name: "Budget" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Recurring|Bills/ })).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Previous month" })).toBeInTheDocument();
  });

  it("asks you to connect a bank first, keeping the heading", async () => {
    app.state = { connected: false };
    serve(month());
    render(Budget);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Budget");
    expect(screen.getByText("Connect a bank to set a budget")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect a bank" })).toHaveAttribute("href", "#setup/connections");
    expect(screen.queryByText("Budgeted")).not.toBeInTheDocument();
  });
});
