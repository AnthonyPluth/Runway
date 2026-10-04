// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));

import { api } from "$lib/api";
import { app, route } from "$lib/app.svelte";
import { txFilters } from "$lib/filters.svelte";
import { pickCategory } from "../test/pick";
import { toast } from "svelte-sonner";
import { category, tx } from "../test/fixtures";
import { accounts, lastList, none, resetTxPage, rows, serve, summary } from "../test/txPage";
import Transactions from "./Transactions.svelte";

beforeEach(resetTxPage);

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

    it("takes the old projected items down, with a Retry, when the forecast can't be loaded again", async () => {
      let fail = false;
      const ev = { events: [{ date: "2026-03-20", name: "Rent", amount: -1500, kind: "recurring", key: "k", balance_after: 1, account_id: "a1", account: "Checking" }] };
      serve(rows(), 2, (path) => { if (path.startsWith("/api/overview")) { if (fail) throw new Error("Down"); return ev; } return undefined; });
      render(Transactions);
      await userEvent.click(await screen.findByRole("button", { name: "−$1,500.00" }));
      fail = true;
      const input = screen.getByRole("spinbutton", { name: "Amount" });
      await userEvent.clear(input);
      await userEvent.type(input, "1400{Enter}");
      expect(await screen.findByTestId("upcoming-failed")).toHaveTextContent("Couldn’t load what’s coming up.");
      expect(screen.queryByText("Rent")).not.toBeInTheDocument();
      fail = false;
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(await screen.findByText("Rent")).toBeInTheDocument();
      expect(screen.queryByTestId("upcoming-failed")).not.toBeInTheDocument();
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

    it("shows a budget's link what's still coming in it, recurring charges on cards included", async () => {
      route.query = "category=Food&from=2026-03-01&to=2026-03-31&scope=budget";
      app.state = { connected: true, review_count: 3, brands: { c1: { institution: "Travel Bank", initial: "T" } } };
      const ev = (date: string, name: string, category: string, extra = {}) =>
        ({ date, name, amount: -20, kind: "recurring", key: `k-${name}`, category, ...extra });
      serve(rows(), 2, (path) => path === "/api/categories" ? [category("Food"), category("Groceries", { parent: "Food" }), category("Coffee")]
        : path.startsWith("/api/overview") ? {
          events: [ev("2026-03-20", "Farm box", "Groceries", { balance_after: 900, account_id: "a1", account: "Checking" }),
            ev("2026-03-21", "Beans", "Coffee", { balance_after: 880, account_id: "a1", account: "Checking" })],
          charges: [ev("2026-03-22", "Meal kit", "Food", { account_id: "c1", account: "Travel Card" }),
            ev("2026-04-22", "Meal kit", "Food", { account_id: "c1", account: "Travel Card" })],   // next month's
        } : undefined);
      render(Transactions);
      const group = (await screen.findByRole("heading", { name: "Upcoming · projected" })).closest("section") ?? document.body;
      await within(group as HTMLElement).findByText("Farm box");   // a subcategory's
      const kit = within(group as HTMLElement).getByText("Meal kit");
      expect(within(group as HTMLElement).getAllByText("Meal kit")).toHaveLength(1);
      expect(within(group as HTMLElement).queryByText("Beans")).not.toBeInTheDocument();
      // the card is its bank's badge on the row's logo (named in its tooltip), and a charge to it doesn't move a projected balance
      expect(kit.closest(".cell")!.querySelector("[data-account-badge]")).toHaveAttribute("title", "Travel Card");
      expect(kit.closest(".cell")).not.toHaveTextContent("Travel Card");
      expect(within(group as HTMLElement).getAllByText(/projected balance/)).toHaveLength(1);
    });

    it("leaves out projected items from other accounts when filtered to one", async () => {
      txFilters.transactions.account = "a1";
      serve(rows(), 2, (path) => (path.startsWith("/api/overview") ? { events: [{ date: "2026-03-20", name: "Other rent", amount: -5, kind: "recurring", key: "k", balance_after: 1, account_id: "zzz" }] } : undefined));
      render(Transactions);
      await screen.findByText("Alpha");
      expect(screen.queryByText("Other rent")).not.toBeInTheDocument();
    });
  });

});

