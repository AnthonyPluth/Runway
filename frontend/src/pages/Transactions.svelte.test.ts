// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import { categories } from "$lib/categories.svelte";
import { txFilters, txShow } from "$lib/filters.svelte";
import type { Tx } from "$lib/components/transactions/types";
import { closeRemember } from "$lib/components/transactions/remember.svelte";
import { toast } from "svelte-sonner";
import { category, tx } from "../test/fixtures";
import Transactions from "./Transactions.svelte";

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
    if (path.startsWith("/api/transactions?")) return { items: list, total };
    return {};
  }) as never);
const lastList = () => vi.mocked(api).mock.calls.map((c) => c[0] as string).filter((p) => p.startsWith("/api/transactions?") && !p.includes("ignored=only")).at(-1)!;

beforeEach(() => {
  closeRemember();   // the "always use this category" question is module state and would leak between tests
  vi.mocked(api).mockReset();
  vi.mocked(toast).mockClear(); vi.mocked(toast.success).mockClear(); vi.mocked(toast.error).mockClear();
  app.state = { connected: true, review_count: 3 };
  categories.list = [];
  Object.assign(txFilters.transactions, { q: "", account: "", category: "", month: "", scope: "" });
  Object.assign(txFilters.review, { q: "", account: "", category: "", month: "", scope: "" });
  txShow.ignored = false;
});

describe("Transactions page", () => {
  it("lists the transactions with their count, and the review tab's badge", async () => {
    serve(rows(), 2);
    render(Transactions);
    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Transactions 2");
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
    expect(await screen.findByText(/^Ignored ·/)).toBeInTheDocument();
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
    await userEvent.type(screen.getByRole("searchbox", { name: "Search merchant or description" }), "blue");
    await waitFor(() => expect(lastList()).toContain("q=blue"));
    expect(txFilters.transactions.q).toBe("blue");
  });

  it("filters by account", async () => {
    serve();
    render(Transactions);
    await screen.findByText("Alpha");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Account" }), "a1");
    await waitFor(() => expect(lastList()).toContain("account=a1"));
  });

  it("starts with a month filter set from elsewhere (a budget line), and clears it", async () => {
    Object.assign(txFilters.transactions, { month: "2026-03", scope: "budget", category: "Coffee" });
    serve();
    render(Transactions);
    expect(await screen.findByText(/March 2026 · accounts counted in Budget/)).toBeInTheDocument();
    expect(lastList()).toContain("month=2026-03");
    await userEvent.click(screen.getByRole("button", { name: "Show all dates" }));
    await waitFor(() => expect(lastList()).not.toContain("month=2026-03"));
    expect(screen.queryByText(/March 2026/)).not.toBeInTheDocument();
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
    Object.assign(txFilters.transactions, { q: "zzz", account: "a1", category: "Coffee", month: "2026-03", scope: "budget" });
    serve([], 0);
    render(Transactions);
    expect(await screen.findByText("No transactions match these filters.")).toBeInTheDocument();
    expect(screen.queryByText(/No transactions yet/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Clear filters" })).toHaveLength(2);   // beside the filters, and in the empty card
    await userEvent.click(screen.getAllByRole("button", { name: "Clear filters" })[1]);
    expect(txFilters.transactions).toEqual({ q: "", account: "", category: "", month: "", scope: "" });
    await waitFor(() => expect(lastList()).not.toContain("zzz"));
    expect(lastList()).toContain("q=&account=&category=&month=&scope=");
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
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
    expect(api).toHaveBeenCalledWith("/api/transactions/a/category", { method: "POST", body: { category: "Groceries" } });
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Coffee → Groceries", expect.objectContaining({ description: "Alpha", action: expect.objectContaining({ label: "Undo" }) })));
  });

  it("offers to remember the category for the merchant when the server suggests a rule", async () => {
    serve(rows(), 2, (path, o) => (path.endsWith("/category") && o?.method === "POST" ? { also_updated: 0, offer_rule: { merchant: "Alpha" } } : undefined));
    render(Transactions);
    await screen.findByText("Alpha");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
    expect(await screen.findByRole("dialog", { name: /Use this category/ })).toHaveTextContent("Always use Groceries for Alpha?");
  });

  it("reloads the list when the server also updated other transactions", async () => {
    serve(rows(), 2, (path, o) => (path.endsWith("/category") && o?.method === "POST" ? { also_updated: 4, offer_rule: null } : undefined));
    render(Transactions);
    await screen.findByText("Alpha");
    const before = vi.mocked(api).mock.calls.filter((c) => String(c[0]).startsWith("/api/transactions?") && !String(c[0]).includes("ignored=only")).length;
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
    await waitFor(() => expect(vi.mocked(api).mock.calls.filter((c) => String(c[0]).startsWith("/api/transactions?") && !String(c[0]).includes("ignored=only")).length).toBe(before + 1));
  });

  it("shows the error when saving a category fails", async () => {
    serve(rows(), 2, (path, o) => { if (path.endsWith("/category") && o?.method === "POST") throw new Error("Cannot save"); return undefined; });
    render(Transactions);
    await screen.findByText("Alpha");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Cannot save"));
  });

  describe("upcoming", () => {
    it("shows projected items that match the filters, on the All tab only", async () => {
      serve(rows(), 2, (path) => (path.startsWith("/api/overview") ? { events: [{ date: "2026-03-20", name: "Rent", amount: -1500, kind: "recurring", key: "k", balance_after: 1, account_id: "a1", account: "Checking" }] } : undefined));
      render(Transactions);
      expect(await screen.findByRole("heading", { name: "Upcoming · projected" })).toBeInTheDocument();
      expect(screen.getByText("Rent")).toBeInTheDocument();
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
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("2 to go");
      expect(api).not.toHaveBeenCalledWith(expect.stringContaining("/api/overview"));
    });

    it("removes a transaction from the list once it's categorized, counting down", async () => {
      serve(rows(), 2, (path, o) => (path.endsWith("/category") && o?.method === "POST" ? { also_updated: 0, offer_rule: null } : undefined));
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      await userEvent.selectOptions(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
      await waitFor(() => expect(screen.queryByText("Alpha")).not.toBeInTheDocument());
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("1 to go");
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
      await userEvent.selectOptions(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
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
      await userEvent.selectOptions(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
      await waitFor(() => expect(screen.getByRole("combobox", { name: "Category for Bravo" })).toHaveFocus());
    });

    it("moves focus to the row before when the last one is done, and to 'All caught up' when none are left", async () => {
      queue(rows());
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      await userEvent.selectOptions(screen.getByRole("combobox", { name: "Category for Bravo" }), "Coffee");
      await waitFor(() => expect(screen.getByRole("combobox", { name: "Category for Alpha" })).toHaveFocus());
      await userEvent.selectOptions(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
      await waitFor(() => expect(screen.getByText(/All caught up/)).toHaveFocus());
    });

    it("celebrates an empty queue, but not an empty search", async () => {
      serve([], 0);
      const { unmount } = render(Transactions, { page: "review" });
      expect(await screen.findByText(/All caught up/)).toBeInTheDocument();
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

    it("keeps AI suggestions disabled until an OpenRouter key is set", async () => {
      serve(rows(), 2);
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      expect(screen.getByRole("button", { name: "Suggest categories with AI" })).toBeDisabled();
    });
  });
});
