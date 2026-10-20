// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn().mockResolvedValue({}) }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import type { CardSummary, Overview as OverviewData } from "$lib/types";
import { tx } from "../../../test/fixtures";
import { toast } from "svelte-sonner";
import CardsTable from "./CardsTable.svelte";
import { forecastSheet, setupReview } from "./forecastSheet.svelte";
import SetupChecklist from "./SetupChecklist.svelte";
import ThisMonth from "./ThisMonth.svelte";

beforeEach(() => { vi.mocked(api).mockClear(); vi.mocked(toast.error).mockClear(); });
afterEach(() => { app.state = null; forecastSheet.open = false; setupReview.open = false; vi.useRealTimers(); });

describe("SetupChecklist", () => {
  const setup = (s: Partial<NonNullable<typeof app.state>["setup"]> = {}) => {
    app.state = { connected: false, setup: { bank: false, primary: false, recurring: false, budgets: false, dismissed: false, ...s } };
  };

  it("lists four steps with the first one to do highlighted", () => {
    setup();
    render(SetupChecklist);
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByRole("progressbar", { name: "Setup progress" })).toHaveAttribute("aria-valuenow", "0");
    expect(screen.getByText("0 of 4 done")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect" })).toHaveAttribute("href", "#setup/connections");
  });

  it("ticks off finished steps and stops describing them", () => {
    setup({ bank: true, primary: true });
    render(SetupChecklist);
    expect(screen.getByText("2 of 4 done")).toBeInTheDocument();
    expect(screen.getAllByLabelText("Done")).toHaveLength(2);
    expect(screen.queryByRole("link", { name: "Connect" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add" })).toHaveAttribute("href", "#recurring");
    expect(screen.getByRole("link", { name: "Budget" })).toHaveAttribute("href", "#budget");
  });

  it("chooses the forecast account in the forecast settings, right there on Overview", async () => {
    setup({ bank: true });
    render(SetupChecklist);
    expect(screen.queryByRole("link", { name: "Choose" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Choose" }));
    expect(forecastSheet.open).toBe(true);
  });

  it("greys out every step after the first until a bank is connected, on the welcome screen", () => {
    setup();
    render(SetupChecklist, { welcome: true });
    expect(screen.getByRole("link", { name: "Connect" })).toHaveAttribute("href", "#setup/connections");
    for (const name of ["Choose", "Add", "Budget"]) {
      const button = screen.getByRole("button", { name });
      expect(button).toBeDisabled();
      expect(button).toHaveAccessibleDescription("after your bank connects");
      expect(button.closest("li")).toHaveClass("opacity-50");
    }
    expect(screen.queryByRole("link", { name: "Add" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Budget" })).toBeNull();
  });

  it("says what each step means on a short line under it, until it's done", () => {
    setup({ bank: true });
    render(SetupChecklist);
    expect(screen.getByText("Choose your forecast account").nextElementSibling).toHaveTextContent("Where your pay lands and bills come out");
    expect(screen.getByText("Connect a bank").nextElementSibling).toBeNull();
    expect(screen.getByText("Connect a bank")).not.toHaveAttribute("title");
  });

  it("points at Settings → Accounts while accounts from Plaid wait for a decision", () => {
    app.state = { connected: true, plaid_undecided: 2, setup: { bank: true, primary: false, recurring: false, budgets: false, dismissed: false } };
    render(SetupChecklist);
    expect(screen.getByText("Choose your forecast account").nextElementSibling).toHaveTextContent("First add the 2 new accounts from Plaid in Settings → Accounts");
    expect(screen.getByRole("link", { name: "Review" })).toHaveAttribute("href", "#setup/accounts");
    expect(screen.queryByRole("button", { name: "Choose" })).toBeNull();
  });

  it("points the first step at them too when Plaid is connected but nothing is added yet", () => {
    app.state = { connected: true, plaid_undecided: 1, setup: { bank: false, primary: false, recurring: false, budgets: false, dismissed: false } };
    render(SetupChecklist);
    expect(screen.getByText("Connect a bank").nextElementSibling).toHaveTextContent("Add the 1 account Plaid found in Settings → Accounts");
    expect(screen.getByRole("link", { name: "Review" })).toHaveAttribute("href", "#setup/accounts");
    expect(screen.getByRole("button", { name: "Add" })).toHaveAccessibleDescription("after you add an account");
  });

  it("welcomes a new user on its own, without a Dismiss button", () => {
    setup();
    render(SetupChecklist, { welcome: true });
    expect(screen.getByText("Welcome to Runway")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Dismiss" })).not.toBeInTheDocument();
  });

  it("can be put away, remembering that on the server, with Undo", async () => {
    setup({ bank: true });
    vi.mocked(api).mockResolvedValue({});
    vi.mocked(toast).mockClear();
    render(SetupChecklist);
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(api).toHaveBeenCalledWith("/api/settings", { method: "POST", body: { setup_dismissed: true } });
    expect(api).toHaveBeenCalledWith("/api/state", { keep: true });
    const [message, opts] = vi.mocked(toast).mock.calls.at(-1) as [string, { action: { label: string; onClick: () => Promise<void> } }];
    expect(message).toBe("Setup checklist dismissed");
    expect(opts.action.label).toBe("Undo");
    await opts.action.onClick();
    expect(api).toHaveBeenCalledWith("/api/settings", { method: "POST", body: { setup_dismissed: false } });
  });

  it("shows the error when dismissing fails", async () => {
    setup();
    vi.mocked(api).mockRejectedValueOnce(new Error("Nope"));
    render(SetupChecklist);
    vi.mocked(toast).mockClear();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(toast.error).toHaveBeenCalledWith("Nope");
    expect(toast).not.toHaveBeenCalled();
  });
});

describe("SetupChecklist final review", () => {
  const done = { bank: true, primary: true, recurring: true, budgets: true, dismissed: false };
  const forecast = (extra: Partial<OverviewData> = {}): OverviewData => ({
    today: "2026-09-30", dates: ["2026-09-30"], total: [1000], low: { date: "2026-09-30", balance: 1000 },
    accounts: [{ id: "chk", name: "Everyday Checking", kind: "checking", balance: 1000 }],
    events: [
      { date: "2026-10-01", name: "Rent", amount: -1200, kind: "recurring", key: "r1" },
      { date: "2026-10-05", name: "Paycheck", amount: 2000, kind: "recurring", key: "r2" },
    ],
    cards: [], unlinked_cards: [], warnings: [], warning_links: [],
    budget: { monthly: 450, used: [{ category: "Groceries", amount: 450, account_id: "chk", account: "Checking", chosen: false }], skipped: [] },
    ...extra,
  });
  const finishing = async (props = {}, expand = true) => {
    app.state = { connected: true, setup: done };
    render(SetupChecklist, props);
    if (expand) await userEvent.click(screen.getByRole("button", { name: "Review your forecast" }));
  };

  it("collapses to one row when all four steps are done, expands in place and collapses again", async () => {
    await finishing({ fc: forecast() }, false);
    expect(screen.getByText("Set up · 4 of 4 done")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
    expect(screen.queryByText("Finish setting up")).toBeNull();
    expect(screen.queryByRole("link", { name: "Edit income" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Finish setup" })).toBeNull();
    expect(screen.getByRole("button", { name: "Review your forecast" })).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(screen.getByRole("button", { name: "Review your forecast" }));
    expect(screen.getByRole("button", { name: "Collapse setup" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("heading", { name: "Review your forecast" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Edit income" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Finish setup" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Collapse setup" }));
    expect(screen.getByText("Set up · 4 of 4 done")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Edit income" })).toBeNull();
  });

  it("opens in full when brought back from the forecast settings", () => {
    app.state = { connected: true, setup: { ...done, dismissed: true } };
    setupReview.open = true;
    render(SetupChecklist, { fc: forecast() });
    expect(screen.getByRole("link", { name: "Edit income" })).toBeInTheDocument();
    expect(screen.queryByText("Set up · 4 of 4 done")).toBeNull();
  });

  it("keeps the full card with three steps done", () => {
    app.state = { connected: true, setup: { ...done, budgets: false } };
    render(SetupChecklist, { fc: forecast() });
    expect(screen.getByText("Finish setting up")).toBeInTheDocument();
    expect(screen.getByText("3 of 4 done")).toBeInTheDocument();
    expect(screen.queryByText(/Set up · /)).toBeNull();
    expect(screen.queryByRole("button", { name: "Collapse setup" })).toBeNull();
  });

  it("shows the review once all four steps are done, with what the forecast uses and an Edit link for each", async () => {
    await finishing({ fc: forecast() });
    expect(screen.getByRole("heading", { name: "Review your forecast" })).toBeInTheDocument();
    expect(screen.getByText("4 of 4 done")).toBeInTheDocument();
    expect(screen.getByText("Everyday Checking")).toBeInTheDocument();
    expect(screen.getByText(/Paycheck, Oct 5/)).toBeInTheDocument();
    expect(screen.getByText("1 bill coming up")).toBeInTheDocument();
    expect(screen.getByText(/Rent, Oct 1/)).toBeInTheDocument();
    expect(screen.getByText("No card payments need attention")).toBeInTheDocument();
    expect(screen.getByText(/1 budget in the forecast, \$450\.00 a month/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Edit income" })).toHaveAttribute("href", "#recurring");
    expect(screen.getByRole("link", { name: "Edit bills" })).toHaveAttribute("href", "#recurring");
    expect(screen.getByRole("link", { name: "Edit card payments" })).toHaveAttribute("href", "#setup/accounts");
    expect(screen.getByRole("link", { name: "Edit budgets" })).toHaveAttribute("href", "#budget");
    await userEvent.click(screen.getByRole("button", { name: "Edit forecast account" }));
    expect(forecastSheet.open).toBe(true);
    expect(screen.queryByText(/safe|complete/i)).toBeNull();
  });

  it("does not show it while a step is still to do", () => {
    app.state = { connected: true, setup: { ...done, budgets: false } };
    render(SetupChecklist, { fc: forecast() });
    expect(screen.queryByRole("heading", { name: "Review your forecast" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Finish setup" })).toBeNull();
  });

  it("says specifically what is missing", async () => {
    await finishing({ fc: forecast({ accounts: [], events: [], budget: null, warning_links: [{ text: "Visa: choose which account pays it in Settings.", href: "#setup/accounts" }] }) });
    expect(screen.getByText("No forecast account chosen")).toBeInTheDocument();
    expect(screen.getByText("No income scheduled")).toBeInTheDocument();
    expect(screen.getByText("No bills scheduled")).toBeInTheDocument();
    expect(screen.getByText("Visa: choose which account pays it in Settings.")).toBeInTheDocument();
    expect(screen.getByText("No budgets in the forecast")).toBeInTheDocument();
  });

  it("says when the forecast is loading, or couldn't load, and tries again", async () => {
    const onretry = vi.fn();
    await finishing({ fc: null });
    expect(screen.getByText("Loading your forecast…")).toBeInTheDocument();
    cleanup();
    await finishing({ fc: null, fcError: new Error("Offline"), onretry });
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn’t load your forecast: Offline");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onretry).toHaveBeenCalled();
  });

  it("finishes with omissions, saves first, then brings the forecast into view, with Undo", async () => {
    const onfinish = vi.fn();
    vi.mocked(api).mockResolvedValue({});
    vi.mocked(toast).mockClear();
    await finishing({ fc: forecast({ events: [], budget: null }), onfinish });
    await userEvent.click(screen.getByRole("button", { name: "Finish setup" }));
    expect(api).toHaveBeenCalledWith("/api/settings", { method: "POST", body: { setup_dismissed: true } });
    expect(onfinish).toHaveBeenCalledOnce();
    const [message, opts] = vi.mocked(toast).mock.calls.at(-1) as [string, { action: { label: string; onClick: () => Promise<void> } }];
    expect(message).toBe("Setup checklist dismissed");
    await opts.action.onClick();
    expect(api).toHaveBeenCalledWith("/api/settings", { method: "POST", body: { setup_dismissed: false } });
  });

  it("stays, says why and offers another try when finishing fails", async () => {
    const onfinish = vi.fn();
    vi.mocked(api).mockRejectedValueOnce(new Error("Nope"));
    vi.mocked(toast).mockClear();
    await finishing({ fc: forecast(), onfinish });
    await userEvent.click(screen.getByRole("button", { name: "Finish setup" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn’t finish setup: Nope");
    expect(onfinish).not.toHaveBeenCalled();
    expect(toast).not.toHaveBeenCalled();
    vi.mocked(api).mockResolvedValue({});
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onfinish).toHaveBeenCalledOnce();
  });

  it("can be reopened with a step still to do, and Dismiss is still there early", async () => {
    app.state = { connected: true, setup: { ...done, budgets: false } };
    setupReview.open = true;
    render(SetupChecklist, { fc: forecast() });
    expect(screen.getByRole("heading", { name: "Review your forecast" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Finish setup" }));
    expect(setupReview.open).toBe(false);
  });

  it("leaves out the APR warning and ones put away on Overview", async () => {
    const apr = "Visa: the forecast carries part of its statements to the next one, but doesn’t count the interest on it: enter the card’s APR in Settings.";
    const away = "Amex: choose which account pays it in Settings.";
    await finishing({ fc: forecast({ dismissed_warnings: [away], warning_links: [
      { text: apr, href: "#setup/accounts" }, { text: away, href: "#setup/accounts" }] }) });
    expect(screen.getByText("No card payments need attention")).toBeInTheDocument();
  });

  it("Dismiss on a reopened review also closes the reopened flag, so the card can go", async () => {
    app.state = { connected: true, setup: { ...done, dismissed: true } };
    setupReview.open = true;
    vi.mocked(api).mockResolvedValue({});
    render(SetupChecklist, { fc: forecast() });
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(setupReview.open).toBe(false);
  });

  it("Undo after finishing a reopened review puts back dismissed and the reopened review", async () => {
    app.state = { connected: true, setup: { ...done, dismissed: true } };
    setupReview.open = true;
    vi.mocked(api).mockImplementation(async (path: string, opts?: unknown) => {
      if (path === "/api/settings") app.state = { ...app.state!, setup: { ...app.state!.setup!, dismissed: !!(opts as { body?: { setup_dismissed?: boolean } } | undefined)?.body?.setup_dismissed } };
      return {} as never;
    });
    vi.mocked(toast).mockClear();
    render(SetupChecklist, { fc: forecast() });
    await userEvent.click(screen.getByRole("button", { name: "Finish setup" }));
    expect(setupReview.open).toBe(false);
    const [, opts] = vi.mocked(toast).mock.calls.at(-1) as unknown as [string, { action: { onClick: () => Promise<void> } }];
    await opts.action.onClick();
    const saves = vi.mocked(api).mock.calls.filter(([p]) => p === "/api/settings");
    expect(saves.at(-1)).toEqual(["/api/settings", { method: "POST", body: { setup_dismissed: true } }]);
    expect(setupReview.open).toBe(true);
  });

  it("counts a card with a statement but no paying account as needing attention", async () => {
    await finishing({ fc: forecast({ warning_links: [{ text: "Amex: choose which account pays it in Settings.", href: "#setup/accounts" }] }) });
    expect(screen.getByText("1 card payment to look at")).toBeInTheDocument();
    expect(screen.getByText("Amex: choose which account pays it in Settings.")).toBeInTheDocument();
    expect(screen.queryByText("No card payments need attention")).toBeNull();
  });

  it("keeps the early Dismiss and leaves the welcome screen without a review", async () => {
    await finishing({ fc: forecast() });
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
    cleanup();
    app.state = { connected: false, setup: { ...done, bank: false } };
    render(SetupChecklist, { welcome: true });
    expect(screen.queryByRole("heading", { name: "Review your forecast" })).toBeNull();
  });
});

describe("ThisMonth", () => {
  it("leaves ignored transactions out of Recent", () => {
    for (let i = 0; i < 3; i++) vi.mocked(api).mockReturnValueOnce(new Promise(() => {}));
    render(ThisMonth);
    expect(api).toHaveBeenCalledWith("/api/transactions?limit=5&ignored=0");
  });

  const mcat = (name: string, budget: number, spent: number) => ({ name, parent: null, path: [name], depth: 0, top: name, has_children: false,
    budget, pay_with: null, usual_account: null, available: budget, spent, own_spent: spent, left: budget - spent });
  const monthData = (day: number, cats: ReturnType<typeof mcat>[]) => {
    vi.mocked(api)
      .mockResolvedValueOnce({ month: "2026-03", prev_month: "2026-02", this: [10], last: [12], spent: 640, last_same_point: 500, last_total: 1200 })
      .mockResolvedValueOnce({ month: "2026-03", days_in_month: 30, day, categories: cats, income: 0, uncategorized: 0, pay_accounts: [] })
      .mockResolvedValueOnce({ items: [], total: 0 });
  };

  it("links its heading to Budget, and draws the pace bar with its marker and wording", async () => {
    // $640 of $1,000 budgeted, half way through: $140 ahead of an even pace
    monthData(15, [mcat("Dining", 400, 300), mcat("Travel", 600, 340)]);
    const { container } = render(ThisMonth);
    expect(await screen.findByRole("link", { name: "Spending this month, $640. Open Budget" })).toHaveAttribute("href", "#budget");
    expect(screen.getByText("$140 over pace")).toBeInTheDocument();
    expect(container.querySelector("[data-pace-marker]")).toHaveStyle({ left: "50%" });
    expect(screen.getByRole("img", { name: "$640 of $1,000 budgeted, 50% through the month" })).toBeInTheDocument();
    expect(screen.getByText(/more than this point in Feb/)).toBeInTheDocument();
  });

  it("says under pace later in the month, and draws no bar when nothing is budgeted", async () => {
    monthData(24, [mcat("Dining", 400, 300), mcat("Travel", 600, 340)]);
    render(ThisMonth);
    expect(await screen.findByText("$160 under pace")).toBeInTheDocument();
    cleanup();
    monthData(15, []);
    const { container } = render(ThisMonth);   // the last month drawn is on screen until this one arrives
    await waitFor(() => expect(container.querySelector("[data-pace-marker]")).toBeNull());
    expect(screen.getByText(/more than this point in Feb/)).toBeInTheDocument();
  });

  const paceData = { month: "2026-03", prev_month: "2026-02", this: [10], last: [12], spent: 640, last_same_point: 500, last_total: 1200 };
  const budgetData = (day = 15) => ({ month: "2026-03", days_in_month: 30, day, categories: [mcat("Dining", 400, 300), mcat("Travel", 600, 340)],
    income: 0, uncategorized: 0, pay_accounts: [] });

  it("says the spending couldn't load, keeps Recent, and loads it on Try again", async () => {
    vi.mocked(api)
      .mockRejectedValueOnce(new Error("down"))
      .mockResolvedValueOnce(budgetData())
      .mockResolvedValueOnce({ items: [tx({ id: "a", amount: -12.5 })], total: 1 });
    const { container } = render(ThisMonth);
    expect(await screen.findByText("Couldn’t load spending")).toBeInTheDocument();
    expect(screen.getByText("−$12.50")).toBeInTheDocument();
    expect(container.querySelector("[data-pace-marker]")).toBeNull();
    vi.mocked(api).mockResolvedValueOnce(paceData).mockResolvedValueOnce(budgetData());
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("$140 over pace")).toBeInTheDocument();
    expect(screen.queryByText("Couldn’t load spending")).not.toBeInTheDocument();
    expect(api).toHaveBeenCalledTimes(5);   // each of the two requests once more, Recent not asked again
  });

  it("doesn't leave the last month's spending on screen when a reload fails", async () => {
    vi.mocked(api).mockResolvedValueOnce(paceData).mockResolvedValueOnce(budgetData()).mockResolvedValueOnce({ items: [], total: 0 });
    render(ThisMonth);
    expect(await screen.findByText("$140 over pace")).toBeInTheDocument();
    cleanup();   // Overview is drawn afresh after a change
    vi.mocked(api).mockRejectedValueOnce(new Error("down")).mockResolvedValueOnce(budgetData()).mockResolvedValueOnce({ items: [], total: 0 });
    render(ThisMonth);
    expect(await screen.findByText("Couldn’t load spending")).toBeInTheDocument();
    expect(screen.queryByText("$140 over pace")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Spending this month/ })).not.toBeInTheDocument();
  });

  it("keeps the pace bar when Recent can't load, and says so", async () => {
    vi.mocked(api).mockResolvedValueOnce(paceData).mockResolvedValueOnce(budgetData()).mockRejectedValueOnce(new Error("down"));
    const { container } = render(ThisMonth);
    expect(await screen.findByText("Couldn’t load recent transactions")).toBeInTheDocument();
    expect(await screen.findByText("$140 over pace")).toBeInTheDocument();
    expect(container.querySelector("[data-pace-marker]")).not.toBeNull();
  });

  it("signs Recent's amounts, and shows a dash, not 0%, for a budget with nothing spent", async () => {
    const cat = (name: string, budget: number, spent: number) => ({ name, parent: null, path: [name], depth: 0, top: name, has_children: false,
      budget, pay_with: null, usual_account: null, available: budget, spent, own_spent: spent, left: budget - spent });
    vi.mocked(api)
      .mockResolvedValueOnce({ month: "2026-03", prev_month: "2026-02", this: [10], last: [12], spent: 10, last_same_point: 12, last_total: 300 })
      .mockResolvedValueOnce({ month: "2026-03", days_in_month: 31, day: 10, categories: [cat("Dining", 200, 50), cat("Travel", 500, 0)], income: 0, uncategorized: 0, pay_accounts: [] })
      .mockResolvedValueOnce({ items: [tx({ id: "a", amount: -12.5 }), tx({ id: "b", payee: "Payroll", amount: 2000 })], total: 2 });
    render(ThisMonth);
    expect(await screen.findByText("−$12.50")).toBeInTheDocument();
    expect(screen.getByText("+$2,000.00")).toHaveClass("text-good");
    expect(screen.getByText("25%")).toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
    expect(screen.getByText("nothing spent yet")).toHaveClass("sr-only");
  });

  it("puts each Recent transaction's bank on its logo, as Transactions does", async () => {
    const was = app.state;
    app.state = { ...(was ?? {}), brands: { a1: { institution: "Chase", initial: "C" } } } as typeof app.state;
    try {
      vi.mocked(api)
        .mockResolvedValueOnce({ month: "2026-03", prev_month: "2026-02", this: [10], last: [12], spent: 10, last_same_point: 12, last_total: 300 })
        .mockResolvedValueOnce({ month: "2026-03", days_in_month: 31, day: 10, categories: [], income: 0, uncategorized: 0, pay_accounts: [] })
        .mockResolvedValueOnce({ items: [tx({ id: "a", account_id: "a1", account_name: "Sapphire", amount: -12.5 })], total: 1 });
      const { container } = render(ThisMonth);
      await screen.findByText("−$12.50");
      const badge = container.querySelector("[data-account-badge]");
      expect(badge).toHaveAttribute("title", "Sapphire");
      expect(badge).toHaveTextContent("C");
    } finally { app.state = was; }
  });
});

describe("CardsTable", () => {
  const card = (extra: Partial<CardSummary> = {}): CardSummary => ({
    id: "c1", name: "Sapphire", owed_now: 800, statement_key: "stmt-c1", statement_balance: 600, last_close: "2026-03-01", remaining: 600,
    due_date: "2026-03-26", minimum_payment: 35, ...extra,
  });
  const at = (iso: string) => vi.useFakeTimers({ toFake: ["Date"], now: new Date(`${iso}T12:00:00`) });

  it("explains how to enter cards' statements when there are none", () => {
    render(CardsTable, { onchanged: vi.fn(), cards: [] });
    expect(screen.getByText(/Enter each card’s latest statement/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings → Accounts" })).toHaveAttribute("href", "#setup/accounts");
  });

  it("leaves out cards with a $0 balance and nothing left to pay", () => {
    at("2026-03-10");
    render(CardsTable, { onchanged: vi.fn(), cards: [card(), card({ id: "c2", name: "Freedom", owed_now: 0, statement_balance: 0, remaining: 0 }),
      card({ id: "c3", name: "Venture", owed_now: 0, remaining: 50 }), card({ id: "c4", name: "Amex", owed_now: 0, remaining: 0, credit: 20 })] });
    expect(screen.getByText("Sapphire")).toBeInTheDocument();
    expect(screen.queryByText("Freedom")).toBeNull();
    expect(screen.getByText("Venture")).toBeInTheDocument();
    expect(screen.queryByText("Amex")).toBeNull();
  });

  it("lists cards in the order they're due, the paid ones last", () => {
    at("2026-03-10");
    render(CardsTable, { onchanged: vi.fn(), cards: [
      card({ id: "c1", name: "Sapphire", due_date: "2026-03-26" }),
      card({ id: "c2", name: "Venture", due_date: "2026-03-09" }),
      card({ id: "c3", name: "Amex", owed_now: 300, remaining: 0 }),
      card({ id: "c4", name: "Freedom", due_date: "2026-03-12" }),
    ] });
    const names = screen.getAllByText(/^(Sapphire|Venture|Amex|Freedom)$/).map((el) => el.textContent);
    expect(names).toEqual(["Venture", "Freedom", "Sapphire", "Amex"]);
  });

  it("says so when every card is at $0", () => {
    render(CardsTable, { onchanged: vi.fn(), cards: [card({ owed_now: 0, remaining: 0 })] });
    expect(screen.queryByText("Sapphire")).toBeNull();
    expect(screen.getByText("None of your cards owe anything right now.")).toBeInTheDocument();
  });

  it("says quietly when a statement was entered by hand", () => {
    at("2026-03-10");
    const { unmount } = render(CardsTable, { onchanged: vi.fn(), cards: [card()] });
    expect(screen.queryByText("entered by hand")).toBeNull();
    unmount();
    render(CardsTable, { onchanged: vi.fn(), cards: [card({ statement_source: "manual" })] });
    expect(screen.getByText("entered by hand")).toBeInTheDocument();
  });

  it("shows a card’s balance, what's due on its statement, the due date and minimum", () => {
    at("2026-03-10");
    render(CardsTable, { onchanged: vi.fn(), cards: [card()] });
    expect(screen.getByText(/balance \$800\.00/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "$600.00" })).toBeInTheDocument();
    expect(screen.getByText("due Mar 26")).not.toHaveClass("text-warning");
    expect(screen.getByText(/min \$35\.00/)).toBeInTheDocument();
  });

  it("highlights a payment due within a week", () => {
    at("2026-03-22");
    render(CardsTable, { onchanged: vi.fn(), cards: [card()] });
    expect(screen.getByText("due Mar 26")).toHaveClass("text-warning");
  });

  it("shows only what's still due after a part payment or credit, with the statement in its tooltip", () => {
    at("2026-03-10");
    render(CardsTable, { onchanged: vi.fn(), cards: [card({ remaining: 250, paid_since_close: 300, credits_since_close: 50 })] });
    const due = screen.getByRole("button", { name: "$250.00" });
    expect(due).toHaveAttribute("title", "Amount due · statement closed Mar\u00a01: $600.00 · paid $300.00 and credited $50.00 since · click to correct it");
    expect(screen.queryByText(/\$600\.00/)).toBeNull();
    expect(screen.queryByText(/left/)).toBeNull();
    expect(screen.getByText("due Mar 26")).toBeInTheDocument();
  });

  it("says Paid once nothing remains", () => {
    at("2026-03-10");
    render(CardsTable, { onchanged: vi.fn(), cards: [card({ remaining: 0, paid_since_close: 600 })] });
    expect(screen.getByText("Paid ✓")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "$0.00" })).toHaveAttribute("title", "Amount due · statement closed Mar\u00a01: $600.00 · paid $600.00 since · click to correct it");
    expect(screen.queryByText(/due Mar/)).toBeNull();
  });

  it("says how much of the statement a card that isn't paid in full pays, and what carries over", () => {
    at("2026-03-10");
    const { unmount } = render(CardsTable, { onchanged: vi.fn(), cards: [card({ pay_mode: "minimum", payment: 35, carried: 565 })] });
    expect(screen.getByText(/pays \$35\.00 of \$600\.00/)).toHaveAttribute("title", "The rest, $565.00, carries into the next statement");
    unmount();
    const over = render(CardsTable, { onchanged: vi.fn(), cards: [card({ pay_mode: "minimum", payment: 1000, carried: -400 })] });
    expect(screen.getByText(/pays \$1,000\.00 of \$600\.00/)).toHaveAttribute("title", "The extra $400.00 comes off the next statement");
    over.unmount();
    render(CardsTable, { onchanged: vi.fn(), cards: [card({ pay_mode: "full", payment: 600, carried: 0 })] });
    expect(screen.queryByText(/pays/)).toBeNull();
  });

  it("separates the notes with dots and spaces", () => {
    at("2026-03-10");
    render(CardsTable, { onchanged: vi.fn(), cards: [card({ statement_source: "manual" })] });
    const line = screen.getByText(/balance \$800\.00/).textContent!.replace(/\s+/g, " ");
    expect(line).toBe("balance $800.00 · entered by hand");
  });

  it("doesn't show an average spend a statement", () => {
    render(CardsTable, { onchanged: vi.fn(), cards: [card()] });
    expect(screen.queryByText(/a statement/)).not.toBeInTheDocument();
    expect(screen.getByText(/balance \$/)).toBeInTheDocument();
  });

  it("lets you correct the amount due, kept as the statement it implies, and undo that", async () => {
    at("2026-03-10");
    const onchanged = vi.fn();
    render(CardsTable, { onchanged, cards: [card({ statement_set: true, statement_balance: 610, statement_reported: 590, remaining: 260,
                                                   paid_since_close: 300.1, credits_since_close: 49.9 })] });
    expect(screen.getByText("set")).toHaveAttribute("title", "Entered by you · the bank’s statement leaves $240.00 due");
    expect(screen.getByRole("button", { name: "reset" })).toHaveAttribute("title", "Go back to the amount due on the bank's statement ($240.00)");
    await userEvent.click(screen.getByRole("button", { name: "$260.00" }));
    const box = screen.getByRole("spinbutton", { name: "Amount due" });
    await userEvent.clear(box);
    await userEvent.type(box, "0.1{Enter}");
    expect(api).toHaveBeenCalledWith("/api/overrides", { method: "POST", body: { key: "stmt-c1", amount: 350.1 } });
    expect(onchanged).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "reset" }));
    expect(api).toHaveBeenCalledWith("/api/overrides", { method: "DELETE", body: { key: "stmt-c1" } });
    expect(onchanged).toHaveBeenCalledTimes(2);
  });

  it("keeps an amount due of nothing, and saves nothing for the amount already shown", async () => {
    at("2026-03-10");
    render(CardsTable, { onchanged: vi.fn(), cards: [card({ remaining: 250, paid_since_close: 350 })] });
    await userEvent.click(screen.getByRole("button", { name: "$250.00" }));
    await userEvent.keyboard("{Enter}");
    expect(api).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "$250.00" }));
    const box = screen.getByRole("spinbutton", { name: "Amount due" });
    await userEvent.clear(box);
    await userEvent.type(box, "0{Enter}");
    expect(api).toHaveBeenCalledWith("/api/overrides", { method: "POST", body: { key: "stmt-c1", amount: 350 } });
  });

  it("says when an amount due couldn't be saved, and leaves the amount as it was", async () => {
    at("2026-03-10");
    vi.mocked(api).mockRejectedValueOnce(new Error("Couldn't save"));
    const onchanged = vi.fn();
    render(CardsTable, { onchanged, cards: [card({ remaining: 250, paid_since_close: 350 })] });
    await userEvent.click(screen.getByRole("button", { name: "$250.00" }));
    const box = screen.getByRole("spinbutton", { name: "Amount due" });
    await userEvent.clear(box);
    await userEvent.type(box, "100{Enter}");
    expect(toast.error).toHaveBeenCalledWith("Couldn't save");
    expect(onchanged).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "$250.00" })).toBeInTheDocument();
  });
});
