// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));

import { api } from "$lib/api";
import { app, route } from "$lib/app.svelte";
import { categories } from "$lib/categories.svelte";
import { txFilters, txShow } from "$lib/filters.svelte";
import type { Tx } from "$lib/components/transactions/types";
import { endBatch } from "$lib/undoBatch";
import { pickCategory } from "../test/pick";
import { toast } from "svelte-sonner";
import { category, tx } from "../test/fixtures";
import Transactions from "./Transactions.svelte";

const none = () => ({ q: "", account: "", category: "", from: "", to: "", min: "", max: "", kind: "" as const, scope: "" });
const summary = () => document.querySelector("[data-summary]");
const accounts = [{ id: "a1", name: "Checking", kind: "checking" }, { id: "inv", name: "Brokerage", kind: "investment" }];
const rows = (): Tx[] => [tx({ id: "a", payee: "Alpha", category: "Coffee" }), tx({ id: "b", payee: "Bravo", category: "Groceries" })];
type Handler = (path: string, opts?: { method?: string; body?: unknown }) => unknown;
const serve = (list: Tx[] = rows(), total = list.length, extra: Handler = () => undefined) =>
  vi.mocked(api).mockImplementation((async (path: string, opts?: { method?: string; body?: unknown }) => {
    const custom = extra(path, opts);
    if (custom !== undefined) return custom;
    if (path === "/api/categories") return [category("Coffee"), category("Groceries")];
    if (path === "/api/accounts") return accounts;
    if (path === "/api/recurring") return [];
    if (path === "/api/state") return { connected: true, review_count: 3 };   // refreshState after a change
    if (path.startsWith("/api/overview")) return { events: [] };
    if (path.startsWith("/api/transactions?")) return { items: list, total, sum: list.reduce((n, t) => n + t.amount, 0) };
    return {};
  }) as never);
const lastList = () => vi.mocked(api).mock.calls.map((c) => c[0] as string).filter((p) => p.startsWith("/api/transactions?") && !p.includes("ignored=only")).at(-1)!;

beforeEach(() => {
  endBatch();   // the Undo toast changes join is module state and would leak between tests
  vi.mocked(api).mockReset();
  vi.mocked(toast).mockClear(); vi.mocked(toast.success).mockClear(); vi.mocked(toast.error).mockClear();
  app.state = { connected: true, review_count: 3 };
  categories.list = [];
  Object.assign(txFilters.transactions, none());
  Object.assign(txFilters.review, none());
  txShow.ignored = false;
  route.query = ""; route.page = "transactions";   // the address's filters are module state too
  history.replaceState(null, "", "/#transactions");
});

describe("Transactions page", () => {
  it("lists the transactions with their count, and the review tab's badge", async () => {
    serve(rows(), 2);
    render(Transactions);
    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1).toHaveAccessibleName("Transactions");
    expect(summary()).toHaveTextContent("2 transactions · −$25.00 net");   // the count, with what they add up to, under the filters
    expect(screen.getByRole("link", { name: /To review/ })).toHaveTextContent("3");
    expect(lastList()).toContain("limit=100");
    expect(lastList()).not.toContain("review=1");
  });

  it("hides what's marked Ignore behind an \"N ignored · Show\" line, which Clear filters leaves alone", async () => {
    serve(rows(), 2, (path) => (path.startsWith("/api/transactions?") && path.includes("ignored=only") ? { items: [], total: 4 } : undefined));
    render(Transactions);
    await screen.findByText("Alpha");
    expect(lastList()).toContain("ignored=0");
    expect(await screen.findByText(/4 ignored/)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Show ignored" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show ignored transactions" }));
    await waitFor(() => expect(lastList()).not.toContain("ignored="));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Account" }), "a1");
    await userEvent.click(await screen.findByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(lastList()).not.toContain("account=a1"));
    expect(lastList()).not.toContain("ignored=");
    expect(screen.getByRole("button", { name: "Hide ignored transactions" })).toHaveAttribute("aria-expanded", "true");
  });

  it("shows no ignored line when nothing is ignored", async () => {
    serve(rows(), 2, (path) => (path.includes("ignored=only") ? { items: [], total: 0 } : undefined));
    render(Transactions);
    await screen.findByText("Alpha");
    expect(screen.queryByText(/ignored/)).not.toBeInTheDocument();
  });

  it("still offers to show ignored transactions when they couldn't be counted", async () => {
    serve(rows(), 2, (path) => (path.includes("ignored=only") ? Promise.reject(new Error("offline")) : undefined));
    render(Transactions);
    await screen.findByText("Alpha");
    await waitFor(() => expect(summary()).toHaveTextContent("· Ignored Show"));
    await userEvent.click(screen.getByRole("button", { name: "Show ignored transactions" }));
    await waitFor(() => expect(lastList()).not.toContain("ignored="));
  });

  it("doesn't offer investment accounts in the account filter", async () => {
    serve();
    render(Transactions);
    await screen.findByText("Alpha");
    const opts = screen.getAllByRole("option").map((o) => o.textContent);
    expect(opts).toContain("Checking");
    expect(opts).not.toContain("Brokerage");
  });

  it("searches a moment after you stop typing", async () => {
    serve();
    render(Transactions);
    await screen.findByText("Alpha");
    await userEvent.type(screen.getByRole("searchbox", { name: "Search transactions" }), "blue");
    await waitFor(() => expect(lastList()).toContain("q=blue"));
    expect(txFilters.transactions.q).toBe("blue");
    expect(location.hash).toBe("#transactions?q=blue");   // in the address, for a reload, Back or a bookmark
  });

  it("filters by account", async () => {
    serve();
    render(Transactions);
    await screen.findByText("Alpha");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Account" }), "a1");
    await waitFor(() => expect(lastList()).toContain("account=a1"));
  });

  it("starts with a month filter set from elsewhere (a budget line), and clears it", async () => {
    Object.assign(txFilters.transactions, { from: "2026-03-01", to: "2026-03-31", scope: "budget", category: "Coffee" });
    serve();
    render(Transactions);
    expect(await screen.findByRole("button", { name: "Dates: March 2026 · Budget accounts" })).toBeInTheDocument();
    expect(lastList()).toContain("from=2026-03-01&to=2026-03-31&scope=budget");
    await userEvent.click(screen.getByRole("button", { name: "Show all dates" }));
    await waitFor(() => expect(lastList()).not.toContain("from="));
    expect(lastList()).not.toContain("scope=");
    expect(screen.getByRole("button", { name: "Dates: All dates" })).toBeInTheDocument();
  });

  it("asks you to connect a bank first, without the list or its filters", async () => {
    app.state = { connected: false };
    serve();
    render(Transactions);
    expect(screen.getByText("Connect a bank to see your transactions")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect a bank" })).toHaveAttribute("href", "#setup/connections");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Transactions");
    expect(screen.getByRole("link", { name: /To review/ })).toBeInTheDocument();
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
  });

  it("says there are no transactions yet once a bank is connected, with the sync status", async () => {
    app.state = { connected: true, syncing: true };
    serve([], 0);
    render(Transactions);
    expect(await screen.findByText("No transactions yet. The first sync brings in months of history.")).toBeInTheDocument();
    expect(screen.getByText("Syncing…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
  });

  it("says when the filters match nothing, and clears them all", async () => {
    Object.assign(txFilters.transactions, { q: "zzz", account: "a1", category: "Coffee", from: "2026-03-01", to: "2026-03-31", scope: "budget" });
    serve([], 0);
    render(Transactions);
    expect(await screen.findByText("No transactions match these filters.")).toBeInTheDocument();
    expect(screen.queryByText(/No transactions yet/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Clear filters" })).toHaveLength(2);   // beside the filters, and in the empty card
    await userEvent.click(screen.getAllByRole("button", { name: "Clear filters" })[1]);
    expect(txFilters.transactions).toEqual(none());
    await waitFor(() => expect(lastList()).not.toContain("zzz"));
    expect(lastList()).toBe("/api/transactions?ignored=0&limit=100&offset=0");
    expect(location.hash).toBe("#transactions");
    expect(await screen.findByText(/No transactions yet/)).toBeInTheDocument();
    expect(screen.getByRole("searchbox")).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
  });

  it("offers Clear filters beside the filters whenever one is on, and reloads the full list", async () => {
    serve();
    render(Transactions);
    await screen.findByText("Alpha");
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Account" }), "a1");
    await userEvent.click(await screen.findByRole("button", { name: "Clear filters" }));
    expect(txFilters.transactions.account).toBe("");
    await waitFor(() => expect(lastList()).not.toContain("account=a1"));
    expect(screen.getByRole("combobox", { name: "Account" })).toHaveValue("");
  });

  it("shows the error with a way to retry", async () => {
    serve();
    vi.mocked(api).mockImplementation((async (path: string) => {
      if (path.startsWith("/api/transactions?")) throw new Error("Nope");
      if (path === "/api/categories" || path === "/api/recurring") return [];
      if (path === "/api/accounts") return accounts;
      return { events: [] };
    }) as never);
    render(Transactions);
    expect(await screen.findByText("Something went wrong: Nope")).toBeInTheDocument();
  });

  it("saves a category change and marks the row as chosen by you", async () => {
    serve(rows(), 2, (path, o) => (path.endsWith("/category") && o?.method === "POST" ? { also_updated: 0, offer_rule: null } : undefined));
    render(Transactions);
    await screen.findByText("Alpha");
    await pickCategory(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
    expect(api).toHaveBeenCalledWith("/api/transactions/a/category", { method: "POST", body: { category: "Groceries" } });
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Coffee → Groceries", expect.objectContaining({ description: "Alpha", action: expect.objectContaining({ label: "Undo" }) })));
  });

  it("under a category filter, changes only a split one's part in it, and loads the list again", async () => {
    txFilters.transactions.category = "Groceries";
    const split = tx({ id: "s", payee: "Market", amount: -100, is_split: 1, splits: [{ category: "Groceries", amount: -60 }, { category: "Coffee", amount: -40 }],
      match: { amount: -60, categories: ["Groceries"] } });
    serve([split], 1, (path, opts) => (opts?.method === "POST" ? { was: [] } : undefined));
    render(Transactions);
    await screen.findByText("Market");
    const loads = vi.mocked(api).mock.calls.filter((c) => String(c[0]).startsWith("/api/transactions?")).length;
    await pickCategory(screen.getByRole("combobox", { name: "Category for the Groceries part of Market" }), "Coffee");
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/transactions/s/category", { method: "POST", body: { category: "Coffee", only: "Groceries" } }));
    await waitFor(() => expect(vi.mocked(api).mock.calls.filter((c) => String(c[0]).startsWith("/api/transactions?")).length).toBeGreaterThan(loads));
    expect(vi.mocked(toast).mock.calls.at(-1)?.[0]).toBe("Groceries → Coffee");
  });

  it("offers to remember the category for the merchant when the server suggests a rule", async () => {
    serve(rows(), 2, (path, o) => (path.endsWith("/category") && o?.method === "POST" ? { also_updated: 0, offer_rule: { merchant: "Alpha" } } : undefined));
    render(Transactions);
    await screen.findByText("Alpha");
    await pickCategory(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
    // In the change's toast: "Always for Alpha" its main button, Undo beside it.
    await waitFor(() => expect(toast).toHaveBeenLastCalledWith("Groceries", expect.objectContaining({
      action: expect.objectContaining({ label: "Always for Alpha" }), cancel: expect.objectContaining({ label: "Undo" }) })));
  });

  it("reloads the list when the server also updated other transactions", async () => {
    serve(rows(), 2, (path, o) => (path.endsWith("/category") && o?.method === "POST" ? { also_updated: 4, offer_rule: null } : undefined));
    render(Transactions);
    await screen.findByText("Alpha");
    const before = vi.mocked(api).mock.calls.filter((c) => String(c[0]).startsWith("/api/transactions?") && !String(c[0]).includes("ignored=only")).length;
    await pickCategory(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
    await waitFor(() => expect(vi.mocked(api).mock.calls.filter((c) => String(c[0]).startsWith("/api/transactions?") && !String(c[0]).includes("ignored=only")).length).toBe(before + 1));
  });

  it("shows the error when saving a category fails", async () => {
    serve(rows(), 2, (path, o) => { if (path.endsWith("/category") && o?.method === "POST") throw new Error("Cannot save"); return undefined; });
    render(Transactions);
    await screen.findByText("Alpha");
    await pickCategory(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Cannot save"));
  });

  describe("filters in the address", () => {
    it("opens with the filters the address has", async () => {
      route.query = "q=rent&from=2026-01-01&to=2026-01-31&min=50&kind=out";
      serve();
      render(Transactions);
      await screen.findByText("Alpha");
      expect(lastList()).toContain("q=rent&from=2026-01-01&to=2026-01-31&min=50&kind=out");
      expect(screen.getByRole("searchbox")).toHaveValue("rent");
      expect(screen.getByRole("button", { name: "Dates: January 2026" })).toBeInTheDocument();
      expect(screen.getByText("Money out")).toBeInTheDocument();   // a chip for each filter behind More
      expect(screen.getByText("$50 or more")).toBeInTheDocument();
    });

    it("writes the filters it has into a plain address (the sidebar's)", async () => {
      txFilters.transactions.category = "Coffee";
      serve();
      render(Transactions);
      await screen.findByText("Alpha");
      expect(location.hash).toBe("#transactions?category=Coffee");
    });

    it("loads other filters when the address changes (Back, Forward)", async () => {
      serve();
      render(Transactions);
      await screen.findByText("Alpha");
      route.query = "account=a1";
      await waitFor(() => expect(lastList()).toContain("account=a1"));
      expect(screen.getByRole("combobox", { name: "Account" })).toHaveValue("a1");
    });

    it("leaves Review's filters out of the address", async () => {
      txFilters.review.q = "zzz";
      serve();
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      expect(location.hash).toBe("#transactions");
    });
  });

  it("filters by a date range from the presets", async () => {
    serve();
    render(Transactions);
    await screen.findByText("Alpha");
    await userEvent.click(screen.getByRole("button", { name: "Dates: All dates" }));
    // (the panel is positioned by measuring, which jsdom can't, so role queries don't find what's in it here)
    await userEvent.click(await screen.findByText("This year"));
    const y = new Date().getFullYear();
    await waitFor(() => expect(lastList()).toContain(`from=${y}-01-01&to=${y}-12-31`));
    expect(screen.getByRole("button", { name: "Dates: This year" })).toBeInTheDocument();
  });

  it("filters by kind and amount from More, each a chip you can remove", async () => {
    serve();
    render(Transactions);
    await screen.findByText("Alpha");
    await userEvent.click(screen.getByRole("button", { name: "More filters" }));
    await userEvent.click(await screen.findByText("Transfers"));
    await waitFor(() => expect(lastList()).toContain("kind=transfer"));
    await userEvent.type(screen.getByLabelText("Smallest amount"), "20{Enter}");
    await waitFor(() => expect(lastList()).toContain("min=20"));
    expect(screen.getByRole("button", { name: "More filters (2 on)" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Any amount" }));
    await waitFor(() => expect(lastList()).not.toContain("min="));
    expect(lastList()).toContain("kind=transfer");
  });

  it("keeps the list and says so when loading it again fails", async () => {
    let fail = false;
    serve(rows(), 2, (path, o) => {
      if (path.startsWith("/api/transactions?") && !path.includes("ignored=only") && fail) throw new Error("offline");
      if (path.endsWith("/category") && o?.method === "POST") { fail = true; return { also_updated: 2, offer_rule: null, was: [] }; }
      return undefined;
    });
    render(Transactions);
    await screen.findByText("Alpha");
    await pickCategory(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
    expect(await screen.findByText("Couldn’t refresh")).toBeInTheDocument();
    expect(screen.getByText("Alpha")).toBeInTheDocument();   // the list stays
    expect(summary()).toHaveClass("opacity-50");             // its numbers marked as not up to date
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.queryByText("Couldn’t refresh")).not.toBeInTheDocument());
    expect(summary()).not.toHaveClass("opacity-50");
  });

  it("offers to try again when the page's setup fails", async () => {
    let fail = true;
    serve(rows(), 2, (path) => { if (path === "/api/accounts" && fail) throw new Error("offline"); return undefined; });
    render(Transactions);
    expect(await screen.findByText("Something went wrong: offline")).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Alpha")).toBeInTheDocument();
  });

  it("opens a transaction's details in the sheet from its row", async () => {
    serve();
    render(Transactions);
    await screen.findByText("Alpha");
    await userEvent.click(screen.getByRole("button", { name: "Details for Alpha" }));
    expect(await screen.findByRole("dialog", { name: "Alpha" })).toBeInTheDocument();
  });

  it("adds a transaction from the button beside the heading", async () => {
    serve();
    render(Transactions);
    await screen.findByText("Alpha");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a transaction" });
    expect(within(dialog).getByRole("combobox", { name: "Account" })).toHaveValue("a1");
  });

  describe("upcoming", () => {
    it("shows projected items that match the filters, on the All tab only", async () => {
      serve(rows(), 2, (path) => (path.startsWith("/api/overview") ? { events: [{ date: "2026-03-20", name: "Rent", amount: -1500, kind: "recurring", key: "k", balance_after: 1, account_id: "a1", account: "Checking" }] } : undefined));
      render(Transactions);
      expect(await screen.findByRole("heading", { name: "Upcoming · projected" })).toBeInTheDocument();
      expect(screen.getByText("Rent")).toBeInTheDocument();
    });

    it("changes a projected amount and loads the forecast again in place, keeping the list", async () => {
      let amount = -1500;
      serve(rows(), 2, (path, o) => (path.startsWith("/api/overview") ? { events: [{ date: "2026-03-20", name: "Rent", amount, kind: "recurring", key: "k", balance_after: 1, account_id: "a1", account: "Checking" }] }
        : path === "/api/overrides" && o?.method === "POST" ? (amount = -1400, { ok: true }) : undefined));
      const version = app.version;
      render(Transactions);
      await userEvent.click(await screen.findByRole("button", { name: "−$1,500.00" }));
      const input = screen.getByRole("spinbutton", { name: "Amount" });
      await userEvent.clear(input);
      await userEvent.type(input, "1400{Enter}");
      expect(await screen.findByRole("button", { name: "−$1,400.00" })).toBeInTheDocument();
      expect(screen.getByText("Alpha")).toBeInTheDocument();
      expect(app.version).toBe(version);   // the page wasn't drawn afresh
    });

    it("leaves out projected items from other accounts when filtered to one", async () => {
      txFilters.transactions.account = "a1";
      serve(rows(), 2, (path) => (path.startsWith("/api/overview") ? { events: [{ date: "2026-03-20", name: "Other rent", amount: -5, kind: "recurring", key: "k", balance_after: 1, account_id: "zzz" }] } : undefined));
      render(Transactions);
      await screen.findByText("Alpha");
      expect(screen.queryByText("Other rent")).not.toBeInTheDocument();
    });
  });

  describe("To review", () => {
    it("asks for only the transactions to review, with the count as 'to go'", async () => {
      serve(rows(), 2);
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      expect(lastList()).toContain("review=1");
      expect(lastList()).not.toContain("ignored=");   // review shows everything that needs a decision
      expect(screen.getByText("2 to go")).toBeInTheDocument();
      expect(api).not.toHaveBeenCalledWith(expect.stringContaining("/api/overview"));
    });

    it("removes a transaction from the list once it's categorized, counting down", async () => {
      serve(rows(), 2, (path, o) => (path.endsWith("/category") && o?.method === "POST" ? { also_updated: 0, offer_rule: null } : undefined));
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      await pickCategory(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
      await waitFor(() => expect(screen.queryByText("Alpha")).not.toBeInTheDocument());
      expect(screen.getByText("1 to go")).toBeInTheDocument();
    });

    // A server that remembers: categorizing takes a transaction out of Review, restoring puts it back.
    const queue = (list: Tx[]) => {
      let open = list;
      serve(list, list.length, (path, o) => {
        if (path.startsWith("/api/transactions?")) return { items: open, total: open.length };
        if (path.endsWith("/category") && o?.method === "POST") {
          const id = path.split("/")[3];
          const was = open.filter((t) => t.id === id).map((t) => ({ id, category: t.category, category_source: "ai", confidence: 0.6, needs_review: 1, payee: t.payee, is_split: 0 }));
          open = open.filter((t) => t.id !== id);
          return { also_updated: 0, offer_rule: null, was };
        }
        if (path === "/api/transactions/bulk") { open = list; return { updated: 1 }; }
        return undefined;
      });
    };
    const undo = () => (vi.mocked(toast).mock.calls.at(-1)![1] as unknown as { action: { onClick: () => Promise<void> } }).action.onClick();

    it("undoes a category change: sends what it was and puts the transaction back in the queue", async () => {
      queue(rows());
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      await pickCategory(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
      await waitFor(() => expect(screen.queryByText("Alpha")).not.toBeInTheDocument());
      expect(toast).toHaveBeenLastCalledWith("Coffee → Groceries", expect.objectContaining({ description: "Alpha" }));
      await undo();
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { restore: [
        { id: "a", category: "Coffee", category_source: "ai", confidence: 0.6, needs_review: 1, payee: "Alpha", is_split: 0 }] } });
      expect(await screen.findByText("Alpha")).toBeInTheDocument();
      expect(toast).toHaveBeenLastCalledWith("Undone", undefined);
    });

    it("moves focus to the next row's category when one leaves the queue", async () => {
      queue(rows());
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      await pickCategory(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
      await waitFor(() => expect(screen.getByRole("combobox", { name: "Category for Bravo" })).toHaveFocus());
    });

    it("moves focus to the row before when the last one is done, and to 'All caught up' when none are left", async () => {
      queue(rows());
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      await pickCategory(screen.getByRole("combobox", { name: "Category for Bravo" }), "Coffee");
      await waitFor(() => expect(screen.getByRole("combobox", { name: "Category for Alpha" })).toHaveFocus());
      await pickCategory(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
      await waitFor(() => expect(screen.getByText(/All caught up/)).toHaveFocus());
    });

    it("celebrates an empty queue, but not an empty search", async () => {
      serve([], 0);
      const { unmount } = render(Transactions, { page: "review" });
      expect(await screen.findByText(/All caught up/)).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "See all transactions" })).toHaveAttribute("href", "#transactions");
      unmount();
      txFilters.review.q = "zzz";
      render(Transactions, { page: "review" });
      expect(await screen.findByText("No transactions match these filters.")).toBeInTheDocument();
    });

    it("asks you to connect a bank before there's anything to review", () => {
      app.state = { connected: false };
      serve();
      render(Transactions, { page: "review" });
      expect(screen.getByText("Connect a bank to review transactions")).toBeInTheDocument();
    });

    it("says AI suggestions need a key, with the way to Settings, until one is set", async () => {
      serve(rows(), 2);
      const { unmount } = render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      expect(screen.queryByRole("button", { name: "Suggest categories with AI" })).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Settings › Connections" })).toHaveAttribute("href", "#setup/connections");
      unmount();
      app.state = { connected: true, review_count: 3, has_api_key: true };
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      expect(screen.getByRole("button", { name: "Suggest categories with AI" })).toBeEnabled();
    });

    const waiting = (): Tx[] => [tx({ id: "a", payee: "Alpha", category: "Coffee", needs_review: 1, category_source: "ai", confidence: 0.95 }),
      tx({ id: "b", payee: "Bravo", category: "Groceries", needs_review: 1, category_source: "rule", confidence: 0.6 }),
      tx({ id: "c", payee: "Charlie", category: null, needs_review: 1 })];
    const wasOf = (id: string) => [{ id, category: "Coffee", category_source: "ai", confidence: 0.95, needs_review: 1, payee: "Alpha", is_split: 0 }];

    it("takes a row out of the queue before the server answers, and puts it back where it was if saving fails", async () => {
      let fail!: (e: Error) => void;
      serve(rows(), 2, (path, o) => (path.endsWith("/category") && o?.method === "POST" ? new Promise((_, no) => { fail = no; }) : undefined));
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      await pickCategory(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
      expect(screen.queryByText("Alpha")).not.toBeInTheDocument();   // straight away
      expect(screen.getByText("1 to go")).toBeInTheDocument();
      fail(new Error("Locked"));
      expect(await screen.findByText("Alpha")).toBeInTheDocument();
      expect(screen.getAllByRole("listitem").map((r) => r.dataset.tx)).toEqual(["a", "b"]);   // where it was
      expect(screen.getByText("2 to go")).toBeInTheDocument();
      expect(toast.error).toHaveBeenCalledWith("Couldn’t save Alpha", { description: "Locked" });
    });

    it("accepts a row's category, whoever set it, without changing it; Undo puts it back", async () => {
      serve(waiting(), 3, (path) => (path.endsWith("/accept") ? { ok: true, was: wasOf("b") } : undefined));
      render(Transactions, { page: "review" });
      await screen.findByText("Bravo");
      await userEvent.click(screen.getByRole("button", { name: "Accept Groceries for Bravo" }));
      expect(api).toHaveBeenCalledWith("/api/transactions/b/accept", { method: "POST" });
      await waitFor(() => expect(screen.queryByText("Bravo")).not.toBeInTheDocument());
      expect(toast).toHaveBeenLastCalledWith("Accepted Groceries", expect.objectContaining({ description: "Bravo" }));
      await (vi.mocked(toast).mock.calls.at(-1)![1] as unknown as { action: { onClick: () => Promise<void> } }).action.onClick();
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { restore: wasOf("b") } });
    });

    it("folds changes made one after another into one toast that undoes them all", async () => {
      serve(waiting(), 3, (path) => (path.endsWith("/accept") ? { ok: true, was: wasOf(path.split("/")[3]) } : undefined));
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      await userEvent.click(screen.getByRole("button", { name: "Accept Coffee for Alpha" }));
      await userEvent.click(screen.getByRole("button", { name: "Accept Groceries for Bravo" }));
      await waitFor(() => expect(vi.mocked(toast).mock.calls.at(-1)![0]).toBe("2 changed"));
      vi.mocked(api).mockClear();
      await (vi.mocked(toast).mock.calls.at(-1)![1] as unknown as { action: { onClick: () => Promise<void> } }).action.onClick();
      expect(vi.mocked(api).mock.calls.filter((c) => c[0] === "/api/transactions/bulk")).toHaveLength(2);
    });

    it("accepts every row it's at least 90% sure of at once, and can undo it", async () => {
      serve(waiting(), 3, (path) => (path === "/api/transactions/bulk" ? { updated: 1, was: wasOf("a") } : undefined));
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      await userEvent.click(screen.getByRole("button", { name: /Accept all ≥ 90%/ }));
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { ids: ["a"], reviewed: true } });
      await waitFor(() => expect(screen.queryByText("Alpha")).not.toBeInTheDocument());
      expect(screen.getByText("Bravo")).toBeInTheDocument();   // 60%: left for you
      expect(toast).toHaveBeenLastCalledWith("Accepted 1 transaction", expect.objectContaining({ action: expect.objectContaining({ label: "Undo" }) }));
    });

    it("asks first before accepting more than ten at once", async () => {
      const many = Array.from({ length: 11 }, (_, i) => tx({ id: `m${i}`, payee: `Shop ${i}`, category: "Coffee", needs_review: 1, confidence: 0.99 }));
      serve(many, 11);
      render(Transactions, { page: "review" });
      await screen.findByText("Shop 0");
      await userEvent.click(screen.getByRole("button", { name: /Accept all ≥ 90%/ }));
      expect(await screen.findByRole("dialog", { name: "Accept 11 transactions?" })).toBeInTheDocument();
      expect(api).not.toHaveBeenCalledWith("/api/transactions/bulk", expect.anything());
    });

    it("says 'Accept all suggestions' when there are no confidences to go by", async () => {
      serve([tx({ id: "a", payee: "Alpha", category: "Coffee", needs_review: 1, confidence: null })], 1);
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      expect(screen.getByRole("button", { name: /Accept all suggestions/ })).toBeInTheDocument();
    });

    describe("keys", () => {
      const rowOf = (id: string) => document.querySelector<HTMLElement>(`[data-tx="${id}"]`)!;
      it("moves a ring between rows with j and k, and lets go with Escape", async () => {
        serve(waiting(), 3);
        render(Transactions, { page: "review" });
        await screen.findByText("Alpha");
        expect(document.querySelector("[data-keys-hint]")).toHaveTextContent("j/k move · Enter accept · c category");
        await userEvent.keyboard("j");
        expect(rowOf("a")).toHaveAttribute("data-focused");
        await userEvent.keyboard("j{ArrowDown}");
        expect(rowOf("c")).toHaveAttribute("data-focused");
        await userEvent.keyboard("k");
        expect(rowOf("b")).toHaveAttribute("data-focused");
        expect(rowOf("b")).toHaveFocus();
        await userEvent.keyboard("{Escape}");
        expect(document.querySelector("[data-focused]")).toBeNull();
      });

      it("accepts with Enter, and opens the picker when there's nothing to accept", async () => {
        serve(waiting(), 3, (path) => (path.endsWith("/accept") ? { ok: true, was: wasOf("a") } : undefined));
        render(Transactions, { page: "review" });
        await screen.findByText("Alpha");
        await userEvent.keyboard("j{Enter}");
        expect(api).toHaveBeenCalledWith("/api/transactions/a/accept", { method: "POST" });
        await waitFor(() => expect(rowOf("b")).toHaveAttribute("data-focused"));   // on to the next
        await userEvent.keyboard("j");
        expect(rowOf("c")).toHaveAttribute("data-focused");
        await userEvent.keyboard("{Enter}");
        expect(await screen.findByRole("listbox")).toBeInTheDocument();
      });

      it("opens the picker with c, and marks Ignore with i or Transfer with t", async () => {
        serve(waiting(), 3, (path, o) => (path.endsWith("/category") && o?.method === "POST" ? { also_updated: 0, offer_rule: null, was: [] } : undefined));
        render(Transactions, { page: "review" });
        await screen.findByText("Alpha");
        categories.list.push(category("Ignore", { is_transfer: 1 }), category("Transfer", { is_transfer: 1 }));
        await userEvent.keyboard("jc");
        expect(await screen.findByRole("listbox")).toBeInTheDocument();
        await userEvent.keyboard("{Escape}");
        rowOf("a").focus();
        await userEvent.keyboard("i");
        expect(api).toHaveBeenCalledWith("/api/transactions/a/category", { method: "POST", body: { category: "Ignore" } });
        await waitFor(() => expect(rowOf("b")).toHaveFocus());
        await userEvent.keyboard("t");
        expect(api).toHaveBeenCalledWith("/api/transactions/b/category", { method: "POST", body: { category: "Transfer" } });
      });

      it("leaves the keys alone while you type in the search", async () => {
        serve(waiting(), 3);
        render(Transactions, { page: "review" });
        await screen.findByText("Alpha");
        await userEvent.type(screen.getByRole("searchbox", { name: "Search transactions" }), "jk");
        expect(document.querySelector("[data-focused]")).toBeNull();
      });
    });

    describe("group by merchant", () => {
      const same = (): Tx[] => [tx({ id: "a", payee: "Alpha", category: null, amount: -5 }), tx({ id: "b", payee: "Alpha", category: null, amount: -7 }),
        tx({ id: "c", payee: "Bravo", category: null })];
      it("shows one row per merchant with one picker for all of them, off until you turn it on", async () => {
        serve(same(), 3, (path) => (path === "/api/transactions/bulk" ? { updated: 2, was: [], offer_rule: { merchant: "Alpha", match: "alpha", also_updated: 0 } } : undefined));
        render(Transactions, { page: "review" });
        await screen.findAllByText("Alpha");
        expect(screen.getByRole("switch", { name: "Group by merchant" })).toHaveAttribute("aria-checked", "false");
        await userEvent.click(screen.getByRole("switch", { name: "Group by merchant" }));
        const list = screen.getByRole("list", { name: "Merchants to review" });
        expect(within(list).getAllByRole("listitem")).toHaveLength(2);
        expect(within(list).getByText("2 transactions · −$12.00")).toBeInTheDocument();
        await pickCategory(screen.getByRole("combobox", { name: "Category for 2 transactions from Alpha" }), "Groceries");
        expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { ids: ["a", "b"], category: "Groceries" } });
        await waitFor(() => expect(within(list).getAllByRole("listitem")).toHaveLength(1));
        expect(toast).toHaveBeenLastCalledWith("Alpha → Groceries", expect.objectContaining({ action: expect.objectContaining({ label: "Always for Alpha" }) }));
      });

      it("isn't offered when the AI groups by merchant itself", async () => {
        app.state = { connected: true, review_count: 3, has_api_key: true };
        serve(same(), 3);
        render(Transactions, { page: "review" });
        await screen.findAllByText("Alpha");
        expect(screen.queryByRole("switch", { name: "Group by merchant" })).not.toBeInTheDocument();
      });
    });
  });
});
