// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import { categories } from "$lib/categories.svelte";
import { txFilters } from "$lib/filters.svelte";
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
    if (path.startsWith("/api/overview")) return { events: [] };
    if (path.startsWith("/api/transactions?")) return { items: list, total };
    return {};
  }) as never);
const lastList = () => vi.mocked(api).mock.calls.map((c) => c[0] as string).filter((p) => p.startsWith("/api/transactions?")).at(-1)!;

beforeEach(() => {
  closeRemember();   // the "always use this category" question is module state and would leak between tests
  vi.mocked(api).mockReset();
  vi.mocked(toast.success).mockClear(); vi.mocked(toast.error).mockClear();
  app.state = { connected: true, review_count: 3 };
  categories.list = [];
  Object.assign(txFilters.transactions, { q: "", account: "", category: "", month: "", scope: "" });
  Object.assign(txFilters.review, { q: "", account: "", category: "", month: "", scope: "" });
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

  it("says when nothing matches", async () => {
    serve([], 0);
    render(Transactions);
    expect(await screen.findByText("No transactions match.")).toBeInTheDocument();
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
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Saved"));
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
    const before = vi.mocked(api).mock.calls.filter((c) => String(c[0]).startsWith("/api/transactions?")).length;
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Category for Alpha" }), "Groceries");
    await waitFor(() => expect(vi.mocked(api).mock.calls.filter((c) => String(c[0]).startsWith("/api/transactions?")).length).toBe(before + 1));
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

    it("celebrates an empty queue, but not an empty search", async () => {
      serve([], 0);
      const { unmount } = render(Transactions, { page: "review" });
      expect(await screen.findByText(/All caught up/)).toBeInTheDocument();
      unmount();
      txFilters.review.q = "zzz";
      render(Transactions, { page: "review" });
      expect(await screen.findByText("No transactions match.")).toBeInTheDocument();
    });

    it("keeps AI suggestions disabled until an OpenRouter key is set", async () => {
      serve(rows(), 2);
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      expect(screen.getByRole("button", { name: "Suggest categories with AI" })).toBeDisabled();
    });
  });
});
