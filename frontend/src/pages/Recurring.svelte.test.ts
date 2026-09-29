// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import type { RecurringItem, Suggestion } from "$lib/components/recurring/types";
import type { Account } from "$lib/types";
import { toast } from "svelte-sonner";
import Recurring from "./Recurring.svelte";

const accounts: Account[] = [{ id: "a1", name: "Checking", kind: "checking" }, { id: "a2", name: "Old", kind: "checking", hidden: 1 }];
const item = (extra: Partial<RecurringItem> = {}): RecurringItem => ({
  id: 1, name: "Rent", account_id: "a1", amount: -1500, frequency: "monthly", active: 1, matched_count: 0, next_date: "2026-04-01", ...extra,
});
const suggestion = (extra: Partial<Suggestion> = {}): Suggestion => ({ account_id: "a1", name: "Netflix", match: "NETFLIX", amount: -15.49, frequency: "monthly", anchor_date: "2026-03-05", count: 6, ...extra });
const serve = (items: RecurringItem[], suggestions: Suggestion[] = [], more: Record<string, unknown> = {}) =>
  vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
    if (path in more) return more[path] as never;
    if (path === "/api/accounts") return accounts as never;
    if (path === "/api/recurring" && !opts?.method) return items as never;
    if (path === "/api/recurring/suggestions") return suggestions as never;
    return { linked: 0 } as never;
  });

beforeEach(() => { vi.mocked(api).mockReset(); app.state = { connected: true }; });

describe("Recurring page", () => {
  it("splits items into money in and money out", async () => {
    serve([item(), item({ id: 2, name: "Paycheck", amount: 3000 })]);
    render(Recurring);
    const inn = await screen.findByRole("region", { name: "Money in" });
    const out = screen.getByRole("region", { name: "Money out" });
    expect(within(inn).getByText("Paycheck")).toBeInTheDocument();
    expect(within(inn).getByText("+$3,000.00")).toHaveClass("text-emerald-500");
    expect(within(out).getByText("Rent")).toBeInTheDocument();
    expect(within(out).getByText("−$1,500.00")).toBeInTheDocument();
  });

  it("classifies an item by the amount the forecast expects rather than its fixed amount", async () => {
    serve([item({ id: 3, name: "Refund-ish", amount: 0, expected_amount: 40, amount_mode: "avg3" })]);
    render(Recurring);
    expect(within(await screen.findByRole("region", { name: "Money in" })).getByText("Refund-ish")).toBeInTheDocument();
  });

  it("summarizes an item: how often, when it's next due and how many matched", async () => {
    serve([item({ matched_count: 4 })]);
    render(Recurring);
    expect(await screen.findByText("monthly · next Apr 1 · 4 matched")).toBeInTheDocument();
  });

  it("marks a paused item, and one that missed a payment", async () => {
    serve([item({ active: 0, missed: [{ key: "k", name: "Rent", amount: -1500, date: "2026-03-01", recurring_id: 1 }] })]);
    render(Recurring);
    expect(await screen.findByText("paused")).toBeInTheDocument();
    expect(screen.getByText("missed a payment")).toBeInTheDocument();
  });

  it("opens the add form by itself when there's nothing yet, with the primary account chosen", async () => {
    app.state = { connected: true, primary_account: "a1" };
    serve([]);
    render(Recurring);
    expect(await screen.findByRole("heading", { name: "Add a recurring item" })).toBeInTheDocument();
    expect(screen.getByText(/No recurring items yet/)).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /Account/ })).toHaveValue("a1");
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
  });

  it("offers hidden accounts nowhere but on the item that uses them", async () => {
    app.state = { connected: true };
    serve([]);
    render(Recurring);
    await screen.findByRole("heading", { name: "Add a recurring item" });
    expect(screen.queryByRole("option", { name: "Old" })).not.toBeInTheDocument();
  });

  it("keeps the add form closed when there are items, until you press Add", async () => {
    serve([item()]);
    render(Recurring);
    await screen.findByText("Rent");
    expect(screen.queryByRole("heading", { name: "Add a recurring item" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("heading", { name: "Add a recurring item" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("heading", { name: "Add a recurring item" })).not.toBeInTheDocument();
  });

  it("adds an item from the form and says how many past transactions it matched", async () => {
    app.state = { connected: true, primary_account: "a1" };
    vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
      if (path === "/api/accounts") return accounts as never;
      if (path === "/api/recurring" && opts?.method === "POST") return { linked: 3 } as never;
      return [] as never;
    });
    render(Recurring);
    await screen.findByRole("heading", { name: "Add a recurring item" });
    await userEvent.type(screen.getByRole("textbox", { name: /Name/ }), "Gym");
    await userEvent.click(screen.getAllByRole("button", { name: "Add" }).at(-1)!);   // the form's; the page header has one too
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Added · matched 3 past transactions"));
    const call = vi.mocked(api).mock.calls.find((c) => c[1] && (c[1] as { method?: string }).method === "POST")!;
    expect((call[1] as { body: Record<string, unknown> }).body).toMatchObject({ name: "Gym", account_id: "a1", frequency: "monthly", active: 1 });
  });

  describe("suggestions", () => {
    it("lists what's spotted in your history and adds one", async () => {
      serve([item()], [suggestion()]);
      render(Recurring);
      expect(await screen.findByText("Netflix")).toBeInTheDocument();
      expect(screen.getByText("monthly · 6× · last Mar 5")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Add Netflix" }));
      const post = vi.mocked(api).mock.calls.find((c) => c[0] === "/api/recurring" && (c[1] as { method?: string } | undefined)?.method === "POST")!;
      expect((post[1] as { body: Suggestion }).body).toMatchObject({ name: "Netflix", match: "NETFLIX" });
      await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Added · matched 0"));
    });

    it("doesn't look for any when no bank is connected", async () => {
      app.state = { connected: false };
      serve([item()], [suggestion()]);
      render(Recurring);
      await screen.findByText("Rent");
      expect(api).not.toHaveBeenCalledWith("/api/recurring/suggestions");
      expect(screen.queryByText("Netflix")).not.toBeInTheDocument();
    });
  });

  it("shows the error when loading fails", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Boom"));
    render(Recurring);
    expect(await screen.findByText("Something went wrong: Boom")).toBeInTheDocument();
  });

  describe("an item", () => {
    it("opens into its fields, and saves a change to one of them", async () => {
      serve([item()], [], { "/api/recurring/1": { linked: 2 } });
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      const name = screen.getAllByRole("textbox", { name: /Name/ })[0];
      await userEvent.clear(name);
      await userEvent.type(name, "Apartment");
      await userEvent.tab();
      await waitFor(() => expect(toast).toHaveBeenCalledWith("Saved · matched 2 more"));
      const post = vi.mocked(api).mock.calls.find((c) => c[0] === "/api/recurring/1")!;
      expect(post[1]).toMatchObject({ method: "POST", body: { name: "Apartment", account_id: "a1", active: 1 } });
    });

    it("doesn't save a schedule of specific dates until the dates are filled in", async () => {
      serve([item()]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      await userEvent.selectOptions(screen.getAllByRole("combobox", { name: /Frequency|How often/ })[0], "dates");
      expect(api).not.toHaveBeenCalledWith("/api/recurring/1", expect.anything());
    });

    it("removes an item after a second click, then reloads the page", async () => {
      serve([item()]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      await userEvent.click(screen.getByRole("button", { name: "Remove" }));
      await userEvent.click(screen.getByRole("button", { name: "Remove?" }));
      expect(api).toHaveBeenCalledWith("/api/recurring/1", { method: "DELETE" });
      await waitFor(() => expect(toast).toHaveBeenCalledWith("Removed"));
    });

    it("shows the transactions it matched, and hides them again", async () => {
      serve([item({ matched_count: 2 })], [], { "/api/transactions?recurring=1&limit=50": { items: [{ id: "t1", posted: "2026-03-01", description: "RENT PAYMENT", amount: -1500 }] } });
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      await userEvent.click(screen.getByRole("button", { name: "Show matched transactions" }));
      expect(await screen.findByText("RENT PAYMENT")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Hide matched transactions" }));
      expect(screen.queryByText("RENT PAYMENT")).not.toBeInTheDocument();
    });

    it("says when there are no matched transactions to show", async () => {
      serve([item({ matched_count: 1 })], [], { "/api/transactions?recurring=1&limit=50": { items: [] } });
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      await userEvent.click(screen.getByRole("button", { name: "Show matched transactions" }));
      expect(await screen.findByText("No matched transactions.")).toBeInTheDocument();
    });
  });
});
