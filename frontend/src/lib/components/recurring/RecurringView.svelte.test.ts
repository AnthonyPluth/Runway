// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (importOriginal) => ({ ...(await importOriginal<typeof import("$lib/api")>()), api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import { isoDay } from "$lib/format";
import type { DismissedSuggestion, RecurringItem, Suggestion } from "$lib/components/recurring/types";
import type { Account } from "$lib/types";
import { toast } from "svelte-sonner";
import Recurring from "./RecurringView.svelte";

const accounts: Account[] = [{ id: "a1", name: "Checking", kind: "checking" }, { id: "a2", name: "Old", kind: "checking", hidden: true }];
const item = (extra: Partial<RecurringItem> = {}): RecurringItem => ({
  id: 1, name: "Rent", account_id: "a1", amount: -1500, frequency: "monthly", anchor_date: "2026-03-01", active: 1, matched_count: 0, next_date: "2026-04-01", ...extra,
});
const suggestion = (extra: Partial<Suggestion> = {}): Suggestion => ({ key: "a1|netflix|monthly", account_id: "a1", name: "Netflix", match: "NETFLIX", amount: -15.49, frequency: "monthly", anchor_date: "2026-03-05", count: 6, ...extra });
const gone = (extra: Partial<DismissedSuggestion> = {}): DismissedSuggestion => ({ key: "a1|hulu|monthly", account_id: "a1", account_name: "Checking", match: "hulu", frequency: "monthly", ...extra });
const serve = (items: RecurringItem[], suggestions: Suggestion[] = [], more: Record<string, unknown> = {}) =>
  vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
    if (path in more) return more[path] as never;
    if (path === "/api/accounts") return accounts as never;
    if (path === "/api/recurring" && !opts?.method) return items as never;
    if (path === "/api/recurring/suggestions") return suggestions as never;
    if (path === "/api/recurring/suggestions/dismissed") return [] as never;
    return { linked: 0 } as never;
  });

beforeEach(() => { vi.mocked(api).mockReset(); app.state = { connected: true }; vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(new Date(2026, 2, 20, 12)); });
afterEach(() => { vi.useRealTimers(); });

const AMOUNT = { selector: 'input[name="amount"]' };

describe("Recurring page", () => {
  it("opens the item a link names (#recurring?item=2) and brings it into view", async () => {
    const seen = vi.fn();
    Element.prototype.scrollIntoView = seen;
    location.hash = "#recurring?item=2";
    serve([item(), item({ id: 2, name: "Paycheck", amount: 3000 })]);
    render(Recurring);
    await screen.findByText("Paycheck");
    expect(document.querySelector("[data-recurring='2']")).toHaveAttribute("open");
    expect(document.querySelector("[data-recurring='1']")).not.toHaveAttribute("open");
    expect(seen).toHaveBeenCalledTimes(1);
    location.hash = "";
  });

  it("splits items into money in and money out", async () => {
    serve([item(), item({ id: 2, name: "Paycheck", amount: 3000 })]);
    render(Recurring);
    const inn = await screen.findByRole("region", { name: "Money in" });
    const out = screen.getByRole("region", { name: "Money out" });
    expect(within(inn).getByText("Paycheck")).toBeInTheDocument();
    expect(within(inn).getByText("+$3,000.00")).toHaveClass("text-good");
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
    expect(await screen.findByText("monthly · in 12 days · 4 matched")).toBeInTheDocument();
  });

  it("says when one is late, in the warning color, and lists each group late first, then soonest due", async () => {
    serve([item({ id: 1, name: "Water", next_date: "2026-05-02" }), item({ id: 2, name: "Phone", next_date: "2026-03-21" }),
      item({ id: 3, name: "Rent", late_date: "2026-03-17", next_date: "2026-04-17" }), item({ id: 4, name: "Gym", active: 0, next_date: "2026-03-20" })]);
    render(Recurring);
    const out = await screen.findByRole("region", { name: "Money out" });
    expect([...out.querySelectorAll("[data-recurring]")].map((d) => d.getAttribute("data-recurring"))).toEqual(["3", "2", "1", "4"]);
    expect(within(out).getByText("3 days late")).toHaveClass("text-warning");
    expect(within(out).getByText("monthly · tomorrow")).not.toHaveClass("text-warning");
    expect(within(out).getByText(/^monthly · next May.2$/)).toBeInTheDocument();
  });

  it("says what each group comes to a month, at the amounts the forecast expects", async () => {
    serve([item({ amount: -1200 }), item({ id: 2, name: "Gym", amount: -30, expected_amount: -30, frequency: "weekly" }),
      item({ id: 3, name: "Pay", amount: 1500, frequency: "biweekly" }), item({ id: 4, name: "Old", amount: -500, active: 0 })]);
    render(Recurring);
    expect(within(await screen.findByRole("region", { name: "Money out" })).getByText("≈ $1,330 a month out")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Money in" })).getByText("≈ $3,250 a month in")).toBeInTheDocument();
  });

  it("badges a fixed amount the last payments all missed with what they came to", async () => {
    serve([item({ amount: -80, suggested_amount: -87.4 }), item({ id: 2, name: "Electric", amount_mode: "avg3", suggested_amount: -90 })]);
    render(Recurring);
    expect(await screen.findByText("Usually $87.40")).toBeInTheDocument();
    expect(screen.getAllByText(/^Usually/)).toHaveLength(1);
  });

  it("summarizes a one-time item by its date, before and after it comes", async () => {
    serve([item({ id: 1, name: "Tax refund", amount: 1240, frequency: "once", anchor_date: "2026-04-20", next_date: "2026-04-20" }),
      item({ id: 2, name: "Deposit back", amount: 500, frequency: "once", anchor_date: "2026-02-10", next_date: null, matched_count: 1 })]);
    render(Recurring);
    expect(await screen.findByText("one-time · Apr 20")).toBeInTheDocument();
    expect(screen.getByText("one-time · Feb 10 · 1 matched")).toBeInTheDocument();
  });

  it("marks a paused item, and one that missed a payment", async () => {
    serve([item({ active: 0 }), item({ id: 2, name: "Water", missed: [{ key: "rec:2:2026-03-01", name: "Water", amount: -40, date: "2026-03-01", recurring_id: 2 }] })]);
    render(Recurring);
    expect(await screen.findByText("Paused")).toBeInTheDocument();
    expect(screen.getByText("missed a payment")).toHaveClass("text-warning");
  });

  describe("needs attention", () => {
    const missed = { key: "rec:2:2026-03-02", name: "Water", amount: -40, date: "2026-03-02", recurring_id: 2 };

    it("lists missed payments at the top, how late each is, with Link a transaction and Skip this one", async () => {
      serve([item(), item({ id: 2, name: "Water", account_name: "Checking", missed: [missed] })]);
      render(Recurring);
      const group = await screen.findByRole("heading", { name: "Needs attention" });
      const list = group.closest("section")!;
      expect(within(list).getByText("Water")).toBeInTheDocument();
      expect(within(list).getByText("18 days late")).toBeInTheDocument();
      expect(within(list).getByText(/due Mar.2 · Checking/)).toBeInTheDocument();
      expect(group.compareDocumentPosition(screen.getByRole("region", { name: "Money out" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(within(list).getByRole("button", { name: "Link a transaction" })).toBeInTheDocument();
    });

    it("skipping one takes it off the list and off its item's line; Undo puts both back", async () => {
      serve([item({ id: 2, name: "Water", missed: [missed] })], [], { "/api/overrides": { ok: true } });
      render(Recurring);
      await userEvent.click(await screen.findByRole("button", { name: "Skip this one" }));
      await waitFor(() => expect(screen.queryByRole("heading", { name: "Needs attention" })).not.toBeInTheDocument());
      expect(screen.queryByText("missed a payment")).not.toBeInTheDocument();
      expect(api).toHaveBeenCalledWith("/api/overrides", { method: "POST", body: { key: missed.key, amount: 0 } });
      const [, opts] = vi.mocked(toast).mock.calls.at(-1)! as unknown as [string, { action: { onClick: () => Promise<void> } }];
      await opts.action.onClick();
      expect(await screen.findByRole("heading", { name: "Needs attention" })).toBeInTheDocument();
      expect(screen.getByText("missed a payment")).toBeInTheDocument();
    });

    it("links a payment from the list it offers", async () => {
      serve([item({ id: 2, name: "Water", missed: [missed] })], [], {
        "/api/recurring/2/candidates?date=2026-03-02": [{ id: "t9", posted: "2026-03-04", amount: -41.2, name: "City Water" }],
        "/api/transactions/t9/recurring": { ok: true } });
      render(Recurring);
      await userEvent.click(await screen.findByRole("button", { name: "Link a transaction" }));
      await userEvent.click(await screen.findByRole("button", { name: /^Link City Water on Mar.4$/ }));
      expect(api).toHaveBeenCalledWith("/api/transactions/t9/recurring", { method: "POST", body: { recurring_id: 2 } });
      await waitFor(() => expect(screen.queryByRole("heading", { name: "Needs attention" })).not.toBeInTheDocument());
    });
  });

  it("opens the add form by itself when there's nothing yet, with the primary account chosen", async () => {
    app.state = { connected: true, primary_account: "a1" };
    serve([]);
    render(Recurring);
    expect(await screen.findByRole("heading", { name: "Add a recurring item" })).toBeInTheDocument();
    expect(screen.queryByText(/No recurring items yet/)).not.toBeInTheDocument();
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

  describe("the logo", () => {
    it("shows the item's logo, and falls back to its category's icon without one", async () => {
      serve([item({ logo: "/api/merchants/m-rent/logo" }), item({ id: 2, name: "Water", logo: null })]);
      const { container } = render(Recurring);
      await screen.findByText("Rent");
      expect(container.querySelectorAll("img[src='/api/merchants/m-rent/logo']")).toHaveLength(1);
      expect(screen.getByRole("button", { name: "Logo for Water" })).toBeInTheDocument();
    });

    it("lets you choose it by the item's name with the same picker as Transactions, then refreshes the list", async () => {
      let list = [item()];
      serve(list, [], { "/api/merchants/logo-options?name=Rent": { choice: null, searchable: true, configured: true, candidates: [{ name: "Landlord", domain: "landlord.com" }], error: null } });
      const base = vi.mocked(api).getMockImplementation()!;
      vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
        if (path === "/api/recurring" && !opts?.method) return list as never;
        if (path === "/api/merchants/logo") { list = [item({ logo: "/api/merchants/site%3Alandlord.com/logo" })]; return { ok: true } as never; }
        return base(path, opts as never);
      });
      const { container } = render(Recurring);
      await userEvent.click(await screen.findByRole("button", { name: "Logo for Rent" }));
      expect(screen.queryByText("Landlord")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: /Landlord/ }));
      expect(api).toHaveBeenCalledWith("/api/merchants/logo", { method: "POST", body: { name: "Rent", website: "landlord.com" } });
      await waitFor(() => expect(container.querySelector("img[src='/api/merchants/site%3Alandlord.com/logo']")).toBeInTheDocument());
    });

    it("wears its account's bank as a small badge on the logo, and none without one or when the bank's mark is the icon", async () => {
      app.state = { connected: true, brands: { a1: { institution: "Chase", src: "/api/merchants/brand%3Achase/logo" } } };
      serve([item({ logo: "/api/merchants/m-rent/logo", account_name: "Checking" }), item({ id: 2, name: "Water", account_id: "gone", logo: "/api/merchants/m-w/logo" }),
        item({ id: 3, name: "Gym", logo: null })]);
      render(Recurring);
      await screen.findByText("Rent");
      const badges = document.querySelectorAll("[data-account-badge]");
      expect(badges).toHaveLength(1);
      expect(badges[0].closest("[data-recurring]")).toHaveAttribute("data-recurring", "1");
      expect(badges[0]).toHaveAttribute("title", "Checking");
      expect(badges[0].querySelector("img")).toHaveAttribute("src", "/api/merchants/brand%3Achase/logo");
    });

    it("doesn't open the item when you click its logo", async () => {
      serve([item()], [], { "/api/merchants/logo-options?name=Rent": { choice: null, searchable: false, configured: false, candidates: [], error: null } });
      const { container } = render(Recurring);
      await userEvent.click(await screen.findByRole("button", { name: "Logo for Rent" }));
      expect(container.querySelector("details")).not.toHaveAttribute("open");
      const panel = await screen.findByRole("dialog", { name: "Logo for Rent" });
      expect(container.querySelector("summary")!.contains(panel)).toBe(false);
      await userEvent.click(within(panel).getByText("Logo for Rent"));
      expect(container.querySelector("details")).not.toHaveAttribute("open");
      expect(screen.getByRole("dialog", { name: "Logo for Rent" })).toBeInTheDocument();
    });
  });

  describe("the add form", () => {
    const open = async (more: Record<string, unknown> = {}) => {
      app.state = { connected: true, primary_account: "a1" };
      vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
        if (path in more) return more[path] as never;
        if (path === "/api/accounts") return accounts as never;
        if (path === "/api/recurring" && opts?.method === "POST") return { linked: 3 } as never;
        return [] as never;
      });
      render(Recurring);
      await screen.findByRole("heading", { name: "Add a recurring item" });
    };
    const posted = () => vi.mocked(api).mock.calls.filter((c) => c[0] === "/api/recurring" && (c[1] as { method?: string } | undefined)?.method === "POST");
    const addButton = () => screen.getAllByRole("button", { name: /^(Add|Adding…)$/ }).at(-1)!;

    it("adds an item and says how many past transactions it matched; money out is the default, stored negative", async () => {
      await open();
      await userEvent.type(screen.getByRole("textbox", { name: /Name/ }), "Gym");
      await userEvent.type(screen.getByLabelText(/Amount/, AMOUNT), "120");
      await userEvent.click(addButton());
      await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Added · matched 3 past transactions"));
      expect(posted()[0][1]).toMatchObject({ method: "POST", body: { name: "Gym", account_id: "a1", amount: -120, frequency: "monthly", anchor_date: isoDay(), active: 1 } });
    });

    it("adds a one-time item on its date", async () => {
      await open();
      await userEvent.type(screen.getByRole("textbox", { name: /Name/ }), "Tax refund");
      await userEvent.click(screen.getByRole("radio", { name: "Money in" }));
      await userEvent.type(screen.getByLabelText(/Amount/, AMOUNT), "1240");
      await userEvent.selectOptions(screen.getByRole("combobox", { name: /How often/ }), "once");
      const day = screen.getByLabelText(/^Date/);
      await userEvent.clear(day);
      await userEvent.type(day, "2026-04-20");
      await userEvent.click(addButton());
      await waitFor(() => expect(posted()).toHaveLength(1));
      expect(posted()[0][1]).toMatchObject({ body: { name: "Tax refund", amount: 1240, frequency: "once", anchor_date: "2026-04-20" } });
    });

    it("stores money in as a positive amount, and ignores a minus you type", async () => {
      await open();
      await userEvent.type(screen.getByRole("textbox", { name: /Name/ }), "Paycheck");
      await userEvent.click(screen.getByRole("radio", { name: "Money in" }));
      await userEvent.type(screen.getByLabelText(/Amount/, AMOUNT), "3100.50");
      await userEvent.click(addButton());
      await waitFor(() => expect(posted()).toHaveLength(1));
      expect((posted()[0][1] as { body: { amount: number } }).body.amount).toBe(3100.5);
    });

    it("uses the direction you pick even if you pick it after typing the amount", async () => {
      await open();
      await userEvent.type(screen.getByRole("textbox", { name: /Name/ }), "Paycheck");
      await userEvent.type(screen.getByLabelText(/Amount/, AMOUNT), "-40");
      await userEvent.click(screen.getByRole("radio", { name: "Money in" }));
      await userEvent.click(addButton());
      await waitFor(() => expect(posted()).toHaveLength(1));
      expect((posted()[0][1] as { body: { amount: number } }).body.amount).toBe(40);
    });

    it("starts on money out and today's date, with the amount asking for a plain positive number", async () => {
      await open();
      expect(screen.getByRole("radio", { name: "Money out" })).toBeChecked();
      expect(screen.getByLabelText(/Repeats from/)).toHaveValue(isoDay());
      expect(screen.getByLabelText(/Amount/, AMOUNT)).toHaveAttribute("placeholder", "120.00");
      expect(screen.getByRole("textbox", { name: /Name/ })).toHaveAttribute("aria-required", "true");
    });

    it("keeps account, amount to forecast, merchant text and the end date under More options, says what's in it, and puts the help in tooltips", async () => {
      await open();
      expect(screen.getByText("More options")).toBeInTheDocument();
      expect(screen.getByText("Checking · always the amount above · matches the name")).toBeInTheDocument();
      expect(screen.getByRole("textbox", { name: /Merchant text/ })).toHaveAttribute("placeholder", "Text in the bank’s description");
      expect(screen.getByRole("textbox", { name: /Merchant text/ })).not.toHaveAttribute("title");
      expect(screen.getByLabelText("Ends on")).toHaveValue("");
      expect(screen.getByRole("combobox", { name: /Amount to forecast/ })).toHaveAttribute("title", expect.stringMatching(/^Use the recent payments/));
      expect(screen.getByRole("combobox", { name: /Account/ })).toHaveValue("a1");
    });

    it("drops the required-fields sentence: the stars mark them", async () => {
      await open();
      expect(screen.queryByText(/Fields marked \* are required/)).not.toBeInTheDocument();
    });

    it("says what's missing next to each field instead of only in a toast, and doesn't send anything", async () => {
      await open();
      await userEvent.click(addButton());
      const name = screen.getByRole("textbox", { name: /Name/ });
      const amount = screen.getByLabelText(/Amount/, AMOUNT);
      expect(name).toHaveAttribute("aria-invalid", "true");
      expect(name).toHaveAccessibleDescription("Enter a name, like Paycheck or Rent.");
      expect(amount).toHaveAttribute("aria-invalid", "true");
      expect(amount).toHaveAccessibleDescription("Enter an amount, like 120.00.");
      expect(name).toHaveFocus();
      expect(posted()).toHaveLength(0);
      await userEvent.type(name, "Gym");
      expect(name).not.toHaveAttribute("aria-invalid");
    });

    it("needs the dates for a schedule of specific dates", async () => {
      await open();
      await userEvent.selectOptions(screen.getByRole("combobox", { name: /How often/ }), "dates");
      await userEvent.type(screen.getByRole("textbox", { name: /Name/ }), "Property tax");
      await userEvent.type(screen.getByLabelText(/Amount/, AMOUNT), "2000");
      await userEvent.click(addButton());
      expect(screen.getByRole("textbox", { name: /Dates each year/ })).toHaveAccessibleDescription("List the dates, like Apr 15, Oct 15.");
      expect(posted()).toHaveLength(0);
    });

    it("can't be sent twice: Add is disabled and says Adding… while the request is out", async () => {
      let done!: (v: { linked: number }) => void;
      await open({});
      vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
        if (path === "/api/recurring" && opts?.method === "POST") return new Promise((res) => { done = res as typeof done; }) as never;
        return (path === "/api/accounts" ? accounts : []) as never;
      });
      await userEvent.type(screen.getByRole("textbox", { name: /Name/ }), "Gym");
      await userEvent.type(screen.getByLabelText(/Amount/, AMOUNT), "30");
      const btn = addButton();
      await userEvent.dblClick(btn);
      expect(posted()).toHaveLength(1);
      expect(addButton()).toBeDisabled();
      expect(addButton()).toHaveTextContent("Adding…");
      done({ linked: 0 });
      await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Added"));
      await waitFor(() => expect(addButton()).toBeEnabled());
    });

    it("shows a server error in a toast and lets you try again", async () => {
      await open();
      vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
        if (path === "/api/recurring" && opts?.method === "POST") throw new Error("Name, account and frequency are required");
        return [] as never;
      });
      await userEvent.type(screen.getByRole("textbox", { name: /Name/ }), "Gym");
      await userEvent.type(screen.getByLabelText(/Amount/, AMOUNT), "30");
      await userEvent.click(addButton());
      await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Name, account and frequency are required"));
      expect(addButton()).toBeEnabled();
    });
  });

  describe("suggestions", () => {
    const form = () => within(screen.getByRole("heading", { name: "Add a recurring item" }).closest<HTMLElement>("section")!);
    const posts = (path: string) => vi.mocked(api).mock.calls.filter((c) => c[0] === path && (c[1] as { method?: string } | undefined)?.method === "POST");

    const showSpotted = async () => userEvent.click(await screen.findByRole("button", { name: "Show" }));

    it("collapses what's spotted to a line under the items, and Show lists them", async () => {
      serve([item()], [suggestion(), suggestion({ key: "a1|acme|biweekly", name: "Acme Payroll", amount: 3100, frequency: "biweekly" })]);
      render(Recurring);
      expect(await screen.findByText(/2 spotted in your history/)).toBeInTheDocument();
      expect(screen.queryByText("Netflix")).not.toBeInTheDocument();
      await showSpotted();
      expect(screen.getByText("Netflix")).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Spotted in your history" }).compareDocumentPosition(screen.getByRole("region", { name: "Money out" }))
        & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
      await userEvent.click(screen.getByRole("button", { name: "Hide suggestions" }));
      expect(screen.queryByText("Netflix")).not.toBeInTheDocument();
    });

    it("says how often in words, and the range when the amounts vary", async () => {
      serve([], [suggestion({ amount: -13.5, amount_low: 12.49, amount_high: 15.49 }), suggestion({ key: "a1|acme|biweekly", name: "Acme Payroll", amount: 3100, amount_low: 3100, amount_high: 3100, frequency: "biweekly" })]);
      render(Recurring);
      expect(await screen.findByText("every 2 weeks · 6× · last Mar 5")).toBeInTheDocument();
      expect(screen.getByText("−$12–$15")).toBeInTheDocument();
      expect(screen.getByText("+$3,100.00")).toHaveClass("text-good");
    });

    it("adds one as it is with one click, and Undo removes it", async () => {
      let added = false;
      serve([], [suggestion()], { "/api/recurring/9": { ok: true } });
      const base = vi.mocked(api).getMockImplementation()!;
      vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
        if (path === "/api/recurring" && opts?.method === "POST") { added = true; return { id: 9, linked: 6 } as never; }
        if (path === "/api/recurring" && !opts?.method) return (added ? [item({ id: 9, name: "Netflix", amount: -15.49 })] : []) as never;
        return base(path, opts as never);
      });
      render(Recurring);
      await userEvent.click(await screen.findByRole("button", { name: "Add Netflix" }));
      expect((posts("/api/recurring")[0][1] as { body: unknown }).body).toMatchObject({ name: "Netflix", account_id: "a1", amount: -15.49, frequency: "monthly", anchor_date: "2026-03-05", match: "NETFLIX", amount_mode: "fixed", active: 1 });
      expect(await within(await screen.findByRole("region", { name: "Money out" })).findByText("Netflix")).toBeInTheDocument();
      const [msg, opts] = vi.mocked(toast).mock.calls.at(-1)! as unknown as [string, { action: { onClick: () => Promise<void> } }];
      expect(msg).toBe("Added Netflix · matched 6");
      added = false;
      await opts.action.onClick();
      expect(api).toHaveBeenCalledWith("/api/recurring/9", { method: "DELETE" });
      await waitFor(() => expect(screen.queryByRole("region", { name: "Money out" })).not.toBeInTheDocument());
    });

    it("shows the error when adding one fails, and keeps it", async () => {
      serve([], [suggestion()]);
      const base = vi.mocked(api).getMockImplementation()!;
      vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
        if (path === "/api/recurring" && opts?.method === "POST") throw new Error("Boom");
        return base(path, opts as never);
      });
      render(Recurring);
      await userEvent.click(await screen.findByRole("button", { name: "Add Netflix" }));
      await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Boom"));
      expect(screen.getByText("Netflix")).toBeInTheDocument();
    });

    it("lists what's spotted in your history, and Edit first fills the form to adjust rather than adding at once", async () => {
      serve([item()], [suggestion()]);
      render(Recurring);
      await showSpotted();
      expect(await screen.findByText("Netflix")).toBeInTheDocument();
      expect(screen.getByText("monthly · 6× · last Mar 5")).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Add a recurring item" })).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Edit Netflix first" }));
      expect(await screen.findByRole("heading", { name: "Add a recurring item" })).toBeInTheDocument();
      expect(form().getByRole("textbox", { name: /Name/ })).toHaveValue("Netflix");
      expect(form().getByLabelText(/Amount/, AMOUNT)).toHaveValue("15.49");
      expect(form().getByRole("radio", { name: "Money out" })).toBeChecked();
      expect(form().getByLabelText(/Repeats from/)).toHaveValue("2026-03-05");
      expect(form().getByRole("textbox", { name: /Merchant text/ })).toHaveValue("NETFLIX");
      expect(posts("/api/recurring")).toHaveLength(0);
      await userEvent.clear(form().getByRole("textbox", { name: /Name/ }));
      await userEvent.type(form().getByRole("textbox", { name: /Name/ }), "Streaming");
      await userEvent.click(form().getByRole("button", { name: "Add" }));
      await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Added"));
      expect((posts("/api/recurring")[0][1] as { body: unknown }).body).toMatchObject({ name: "Streaming", match: "NETFLIX", amount: -15.49, frequency: "monthly", anchor_date: "2026-03-05" });
    });

    it("prefills a paycheck as money in", async () => {
      serve([item()], [suggestion({ key: "a1|acme|biweekly", name: "Acme Payroll", amount: 3100, frequency: "biweekly" })]);
      render(Recurring);
      await showSpotted();
      await userEvent.click(await screen.findByRole("button", { name: "Edit Acme Payroll first" }));
      expect(await form().findByRole("radio", { name: "Money in" })).toBeChecked();
      expect(form().getByLabelText(/Amount/, AMOUNT)).toHaveValue("3100");
    });

    it("dismisses a suggestion with Not recurring, and it stays gone", async () => {
      serve([item()], [suggestion(), suggestion({ key: "a1|gym|monthly", name: "Gym", match: "GYM" })]);
      render(Recurring);
      await showSpotted();
      await userEvent.click(await screen.findByRole("button", { name: "Netflix is not recurring" }));
      await waitFor(() => expect(screen.queryByText("Netflix")).not.toBeInTheDocument());
      expect(screen.getByText("Gym")).toBeInTheDocument();
      expect(posts("/api/recurring/suggestions/dismiss")[0][1]).toMatchObject({ method: "POST", body: { key: "a1|netflix|monthly" } });
    });

    it("offers a dismissed suggestion back: N dismissed · Show lists them, Restore brings one back", async () => {
      let restored = false;
      const hulu = suggestion({ key: "a1|hulu|monthly", name: "Hulu", match: "hulu" });
      vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
        if (path === "/api/accounts") return accounts as never;
        if (path === "/api/recurring" && !opts?.method) return [item()] as never;
        if (path === "/api/recurring/suggestions") return (restored ? [hulu] : []) as never;
        if (path === "/api/recurring/suggestions/dismissed") return (restored ? [] : [gone(), gone({ key: "a1|gym|weekly", match: "gym", name: "Gym", frequency: "weekly" })]) as never;
        if (path === "/api/recurring/suggestions/restore") { restored = true; return { ok: true } as never; }
        return {} as never;
      });
      render(Recurring);
      expect(await screen.findByText("2 dismissed ·")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Restore hulu" })).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Show" }));
      expect(screen.getByText("monthly · Checking")).toBeInTheDocument();
      expect(screen.getByText("weekly · Checking")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Restore Gym" })).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Restore hulu" }));
      expect(await screen.findByText(/1 spotted in your history/)).toBeInTheDocument();
      expect(posts("/api/recurring/suggestions/restore")[0][1]).toMatchObject({ body: { key: "a1|hulu|monthly" } });
      expect(toast.success).toHaveBeenCalledWith("hulu can be suggested again");
      expect(screen.queryByText(/dismissed ·/)).not.toBeInTheDocument();
    });

    it("lists a suggestion you just dismissed, ready to restore", async () => {
      serve([item()], [suggestion()]);
      render(Recurring);
      await showSpotted();
      await userEvent.click(await screen.findByRole("button", { name: "Netflix is not recurring" }));
      expect(await screen.findByText("1 dismissed ·")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Show" }));
      expect(screen.getByRole("button", { name: "Restore Netflix" })).toBeInTheDocument();
    });

    it("says nothing when none are dismissed", async () => {
      serve([item()], [suggestion()]);
      render(Recurring);
      await showSpotted();
      await screen.findByText("Netflix");
      expect(screen.queryByText(/dismissed/)).not.toBeInTheDocument();
    });

    it("keeps a dismissed suggestion listed when restoring it fails", async () => {
      serve([item()], [], { "/api/recurring/suggestions/dismissed": [gone()] });
      render(Recurring);
      await userEvent.click(await screen.findByRole("button", { name: "Show" }));
      vi.mocked(api).mockRejectedValueOnce(new Error("Boom"));
      await userEvent.click(screen.getByRole("button", { name: "Restore hulu" }));
      await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Boom"));
      expect(screen.getByRole("button", { name: "Restore hulu" })).toBeInTheDocument();
    });

    it("keeps a suggestion when dismissing it fails", async () => {
      serve([item()], [suggestion()]);
      render(Recurring);
      await showSpotted();
      await screen.findByText("Netflix");
      vi.mocked(api).mockRejectedValueOnce(new Error("Boom"));
      await userEvent.click(screen.getByRole("button", { name: "Netflix is not recurring" }));
      await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Boom"));
      expect(screen.getByText("Netflix")).toBeInTheDocument();
    });

    it("puts suggestions above the add form when there are no items", async () => {
      serve([], [suggestion()]);
      render(Recurring);
      const spotted = await screen.findByRole("heading", { name: "Spotted in your history" });
      const adding = screen.getByRole("heading", { name: "Add a recurring item" });
      expect(spotted.compareDocumentPosition(adding) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it("doesn't look for any when no bank is connected", async () => {
      app.state = { connected: false };
      serve([item()], [suggestion()]);
      render(Recurring);
      await screen.findByText("Rent");
      expect(api).not.toHaveBeenCalledWith("/api/recurring/suggestions");
      expect(screen.queryByText(/spotted in your history/)).not.toBeInTheDocument();
    });

    it("goes on without them when they can't be looked up", async () => {
      serve([item()]);
      const base = vi.mocked(api).getMockImplementation()!;
      vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
        if (path.startsWith("/api/recurring/suggestions")) throw new Error("Boom");
        return base(path, opts as never);
      });
      render(Recurring);
      expect(await screen.findByText("Rent")).toBeInTheDocument();
      expect(screen.queryByText(/spotted/)).not.toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
  });

  it("shows a list-shaped placeholder while loading", async () => {
    let done!: (v: never) => void;
    serve([item()]);
    const base = vi.mocked(api).getMockImplementation()!;
    vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) =>
      path === "/api/recurring" ? new Promise((res) => { done = res; }) : base(path, opts as never));
    render(Recurring);
    expect(screen.getByRole("status", { name: "Loading recurring items" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Recurring");
    done([item()] as never);
    expect(await screen.findByText("Rent")).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "Loading recurring items" })).not.toBeInTheDocument();
  });

  it("says when loading fails, and Retry loads this page again", async () => {
    let fail = true;
    serve([item()]);
    const base = vi.mocked(api).getMockImplementation()!;
    vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
      if (path === "/api/recurring" && fail) throw new Error("Boom");
      return base(path, opts as never);
    });
    render(Recurring);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t load your recurring items.");
    expect(screen.queryByText(/Boom/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add" })).not.toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Rent")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("works without a bank: your items and Add, with a line saying what a bank adds", async () => {
    app.state = { connected: false };
    serve([item()]);
    render(Recurring);
    expect(await screen.findByText("Rent")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect a bank" })).toHaveAttribute("href", "#setup/connections");
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

    it("doesn't save a schedule of specific dates until the dates are filled in, and says so rather than flashing Saved", async () => {
      serve([item()]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      const freq = screen.getAllByRole("combobox", { name: /How often/ })[0];
      await userEvent.selectOptions(freq, "dates");
      expect(api).not.toHaveBeenCalledWith("/api/recurring/1", expect.anything());
      await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Not saved yet. List the dates, like Apr 15, Oct 15."));
      const dates = screen.getByRole("textbox", { name: /Dates each year/ });
      expect(dates).toHaveFocus();
      expect(dates).toHaveAttribute("aria-invalid", "true");
      expect(freq.closest("label")).not.toHaveClass("just-saved");
    });

    const posts = (path: string) => vi.mocked(api).mock.calls.filter((c) => c[0] === path && (c[1] as { method?: string } | undefined)?.method);
    const undoLast = async () => {
      const [, opts] = vi.mocked(toast).mock.calls.at(-1)! as unknown as [string, { action: { onClick: () => Promise<void> } }];
      await opts.action.onClick();
    };

    it("labels its date as the one it repeats from, and says when it's next due beside it", async () => {
      serve([item({ anchor_date: "2025-12-01", next_date: "2026-04-01" })]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      expect(screen.getByLabelText(/Repeats from/)).toHaveValue("2025-12-01");
      expect(screen.getByText(/^Next: Apr.1$/)).toBeInTheDocument();
    });

    it("pauses with a button, says Paused, and Undo resumes it", async () => {
      serve([item()], [], { "/api/recurring/1": { linked: 0 } });
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      await userEvent.click(screen.getByRole("button", { name: "Pause" }));
      await waitFor(() => expect(posts("/api/recurring/1")).toHaveLength(1));
      expect(posts("/api/recurring/1")[0][1]).toMatchObject({ body: { active: 0, name: "Rent" } });
      expect(screen.getByText("Paused")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Skip the next one" })).not.toBeInTheDocument();
      expect(vi.mocked(toast).mock.calls.at(-1)![0]).toBe("Paused Rent");
      await undoLast();
      expect(posts("/api/recurring/1")[1][1]).toMatchObject({ body: { active: 1 } });
      expect(screen.queryByText("Paused")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    });

    it("resumes a paused one", async () => {
      serve([item({ active: 0 })], [], { "/api/recurring/1": { linked: 0 } });
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      await userEvent.click(screen.getByRole("button", { name: "Resume" }));
      await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Rent is back in the forecast"));
      expect(posts("/api/recurring/1")[0][1]).toMatchObject({ body: { active: 1 } });
      expect(screen.queryByText("Paused")).not.toBeInTheDocument();
    });

    it("stays active when pausing fails", async () => {
      serve([item()]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      vi.mocked(api).mockRejectedValueOnce(new Error("Offline"));
      await userEvent.click(screen.getByRole("button", { name: "Pause" }));
      await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Offline"));
      expect(screen.queryByText("Paused")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Pause" })).toBeEnabled();
    });

    it("skips the next one only, as a $0 for that date, and Undo takes it back", async () => {
      let skipped = false;
      serve([item()], [], { "/api/overrides": { ok: true } });
      const base = vi.mocked(api).getMockImplementation()!;
      vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
        if (path === "/api/overrides") skipped = opts?.method === "POST";
        if (path === "/api/recurring" && !opts?.method) return [skipped ? item({ next_date: "2026-05-01", skipped: ["2026-04-01"] }) : item()] as never;
        return base(path, opts as never);
      });
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      await userEvent.click(screen.getByRole("button", { name: "Skip the next one" }));
      expect(api).toHaveBeenCalledWith("/api/overrides", { method: "POST", body: { key: "rec:1:2026-04-01", amount: 0 } });
      expect(await screen.findByText(/^Skipping Apr.1 ·/)).toBeInTheDocument();
      expect(screen.getByText(/^monthly · next May.1$/)).toBeInTheDocument();
      expect(vi.mocked(toast).mock.calls.at(-1)![0]).toBe("Skipped Rent on Apr\u00a01");
      await undoLast();
      expect(api).toHaveBeenCalledWith("/api/overrides", { method: "DELETE", body: { key: "rec:1:2026-04-01" } });
      await waitFor(() => expect(screen.queryByText(/^Skipping/)).not.toBeInTheDocument());
    });

    it("puts a skipped date back", async () => {
      serve([item({ next_date: "2026-05-01", skipped: ["2026-04-01"] })], [], { "/api/overrides": { ok: true } });
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      await userEvent.click(screen.getByRole("button", { name: "Put it back" }));
      expect(api).toHaveBeenCalledWith("/api/overrides", { method: "DELETE", body: { key: "rec:1:2026-04-01" } });
      await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Apr\u00a01 is back in the forecast"));
    });

    it("says so when skipping fails", async () => {
      serve([item()]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      vi.mocked(api).mockRejectedValueOnce(new Error("Offline"));
      await userEvent.click(screen.getByRole("button", { name: "Skip the next one" }));
      await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Offline"));
    });

    it("saves an end date, won't save one before it starts, and clears it", async () => {
      serve([item()], [], { "/api/recurring/1": { linked: 0 } });
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      const ends = screen.getByLabelText("Ends on");
      await userEvent.type(ends, "2026-12-31");
      await userEvent.tab();
      await waitFor(() => expect(posts("/api/recurring/1")).toHaveLength(1));
      expect(posts("/api/recurring/1")[0][1]).toMatchObject({ body: { end_date: "2026-12-31" } });
      await fireEvent.input(ends, { target: { value: "2026-01-01" } });
      await fireEvent.change(ends);
      await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Not saved yet. It ends before it starts."));
      expect(ends).toHaveAttribute("aria-invalid", "true");
      expect(posts("/api/recurring/1")).toHaveLength(1);
      await userEvent.click(screen.getByRole("button", { name: "Clear the end date" }));
      await waitFor(() => expect(posts("/api/recurring/1")).toHaveLength(2));
      expect(posts("/api/recurring/1")[1][1]).toMatchObject({ body: { end_date: "" } });
      expect(ends).toHaveValue("");
    });

    it("can follow the average of the last 3 payments instead of a fixed amount the payments missed", async () => {
      serve([item({ amount: -80, suggested_amount: -87.4 })], [], { "/api/recurring/1": { linked: 0 } });
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      await userEvent.click(screen.getByRole("button", { name: "Average of last 3" }));
      await waitFor(() => expect(posts("/api/recurring/1")).toHaveLength(1));
      expect(posts("/api/recurring/1")[0][1]).toMatchObject({ body: { amount_mode: "avg3", amount: -80 } });
      expect(screen.getByRole("combobox", { name: /Amount to forecast/ })).toHaveValue("avg3");
      expect(screen.queryByText(/The last payments were about/)).not.toBeInTheDocument();
    });

    it("shows money out or in by the sign of its amount, and the amount without it", async () => {
      serve([item(), item({ id: 2, name: "Paycheck", amount: 3000 })]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      await userEvent.click(screen.getByText("Paycheck"));
      const [pay, rent] = screen.getAllByLabelText(/Amount/, AMOUNT);
      expect(rent).toHaveValue("1500");
      expect(pay).toHaveValue("3000");
      const [payOut, rentOut] = screen.getAllByRole("radio", { name: "Money out" });
      expect(rentOut).toBeChecked();
      expect(payOut).not.toBeChecked();
      expect(screen.getAllByRole("radio", { name: "Money in" })[0]).toBeChecked();
    });

    it("saves the amount with its sign kept, and flipping money in or out saves the flipped amount", async () => {
      serve([item()], [], { "/api/recurring/1": { linked: 0 } });
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      const amount = screen.getByLabelText(/Amount/, AMOUNT);
      await userEvent.clear(amount);
      await userEvent.type(amount, "1600");
      await userEvent.tab();
      await waitFor(() => expect(vi.mocked(api).mock.calls.filter((c) => c[0] === "/api/recurring/1")).toHaveLength(1));
      expect(vi.mocked(api).mock.calls.find((c) => c[0] === "/api/recurring/1")![1]).toMatchObject({ body: { amount: -1600 } });
      await userEvent.click(screen.getByRole("radio", { name: "Money in" }));
      await waitFor(() => expect(vi.mocked(api).mock.calls.filter((c) => c[0] === "/api/recurring/1")).toHaveLength(2));
      expect(vi.mocked(api).mock.calls.filter((c) => c[0] === "/api/recurring/1")[1][1]).toMatchObject({ body: { amount: 1600 } });
    });

    it("refreshes its summary and its group after a save", async () => {
      let saved = false;
      vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
        if (path === "/api/accounts") return accounts as never;
        if (path === "/api/recurring/suggestions") return [] as never;
        if (path === "/api/recurring/1") { saved = true; return { linked: 0 } as never; }
        if (path === "/api/recurring" && !opts?.method) return [saved ? item({ amount: 1500, next_date: "2026-05-01", matched_count: 3 }) : item()] as never;
        return {} as never;
      });
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      expect(within(screen.getByRole("region", { name: "Money out" })).getByText("−$1,500.00")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("radio", { name: "Money in" }));
      expect(await within(await screen.findByRole("region", { name: "Money in" })).findByText("+$1,500.00")).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Money out" })).not.toBeInTheDocument();
      expect(screen.getByText(/^monthly · next May.1 · 3 matched$/)).toBeInTheDocument();
    });

    it("offers the recent payments' amount when they all missed a fixed amount, and saves it when you take it", async () => {
      serve([item({ name: "Paycheck", amount: 5000, frequency: "semimonthly", dates: "15,31", suggested_amount: 2100.47 })], [], { "/api/recurring/1": { linked: 0 } });
      render(Recurring);
      await userEvent.click(await screen.findByText("Paycheck"));
      expect(screen.getByText("The last payments were about $2,100.47, not $5,000.00.")).toBeInTheDocument();
      expect(api).not.toHaveBeenCalledWith("/api/recurring/1", expect.anything());
      await userEvent.click(screen.getByRole("button", { name: "Use $2,100.47" }));
      await waitFor(() => expect(api).toHaveBeenCalledWith("/api/recurring/1", expect.objectContaining({ method: "POST", body: expect.objectContaining({ amount: 2100.47, amount_mode: "fixed" }) })));
      expect(screen.getByLabelText(/Amount/, AMOUNT)).toHaveValue("2100.47");
      expect(screen.queryByText(/The last payments were about/)).not.toBeInTheDocument();
    });

    it("shows the range where the server moved it with the new amount", async () => {
      serve([item({ name: "Paycheck", amount: 5000, frequency: "semimonthly", dates: "15,31", suggested_amount: 2100.47, amount_min: 3500, amount_max: 6500 })],
        [], { "/api/recurring/1": { linked: 1, amount_min: 1470, amount_max: 2730 } });
      render(Recurring);
      await userEvent.click(await screen.findByText("Paycheck"));
      expect(screen.getByLabelText("Smallest amount")).toHaveValue("3500");
      await userEvent.click(screen.getByRole("button", { name: "Use $2,100.47" }));
      await waitFor(() => expect(screen.getByLabelText("Smallest amount")).toHaveValue("1470"));
      expect(screen.getByLabelText("Largest amount")).toHaveValue("2730");
    });

    it("stops offering the amount once you type your own", async () => {
      serve([item({ name: "Paycheck", amount: 5000, frequency: "semimonthly", dates: "15,31", suggested_amount: 2100.47 })]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Paycheck"));
      const amount = screen.getByLabelText(/Amount/, AMOUNT);
      await userEvent.clear(amount);
      await userEvent.type(amount, "2500");
      expect(screen.queryByText(/The last payments were about/)).not.toBeInTheDocument();
      await userEvent.clear(amount);
      await userEvent.type(amount, "5000");
      expect(screen.getByText("The last payments were about $2,100.47, not $5,000.00.")).toBeInTheDocument();
    });

    it("keeps offering the amount when saving it fails, with the old amount back", async () => {
      serve([item({ name: "Paycheck", amount: 5000, frequency: "semimonthly", dates: "15,31", suggested_amount: 2100.47 })]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Paycheck"));
      vi.mocked(api).mockRejectedValueOnce(new Error("Offline"));
      await userEvent.click(screen.getByRole("button", { name: "Use $2,100.47" }));
      await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Offline"));
      expect(screen.getByLabelText(/Amount/, AMOUNT)).toHaveValue("5000");
      expect(screen.getByRole("button", { name: "Use $2,100.47" })).toBeInTheDocument();
    });

    it("saves an amount range, and says when its ends are the wrong way round", async () => {
      serve([item({ name: "Paycheck", amount: 5000, amount_min: 3500, amount_max: 6500 })]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Paycheck"));
      const lo = screen.getAllByLabelText("Smallest amount")[0], hi = screen.getAllByLabelText("Largest amount")[0];
      expect(lo).toHaveValue("3500");
      expect(hi).toHaveAccessibleDescription("Leave blank to match any amount with the text.");
      await userEvent.clear(lo);
      await userEvent.type(lo, "1500");
      await userEvent.tab();
      await waitFor(() => expect(api).toHaveBeenCalledWith("/api/recurring/1", expect.objectContaining({ body: expect.objectContaining({ amount_min: 1500, amount_max: 6500 }) })));
      vi.mocked(api).mockClear();
      await userEvent.clear(hi);
      await userEvent.type(hi, "1000");
      await userEvent.tab();
      await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Not saved yet. The largest amount is smaller than the smallest."));
      expect(hi).toHaveAttribute("aria-invalid", "true");
      expect(api).not.toHaveBeenCalledWith("/api/recurring/1", expect.anything());
      await userEvent.clear(hi);
      await userEvent.tab();
      await waitFor(() => expect(api).toHaveBeenCalledWith("/api/recurring/1", expect.objectContaining({ body: expect.objectContaining({ amount_min: 1500, amount_max: null }) })));
    });

    it("keeps several merchant texts, one per line", async () => {
      serve([item({ name: "Paycheck", amount: 5000, match: "acme" })]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Paycheck"));
      const texts = screen.getAllByRole("textbox", { name: "Merchant text" })[0];
      await userEvent.type(texts, "{Enter}online transfer from savings");
      await userEvent.tab();
      await waitFor(() => expect(api).toHaveBeenCalledWith("/api/recurring/1", expect.objectContaining({ body: expect.objectContaining({ match: "acme\nonline transfer from savings" }) })));
    });

    it("doesn't offer an amount when there's no suggestion, or the amount already follows the payments", async () => {
      serve([item(), item({ id: 2, name: "Electric", amount_mode: "avg3", suggested_amount: -80 })]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      await userEvent.click(screen.getByText("Electric"));
      expect(screen.queryByText(/The last payments were about/)).not.toBeInTheDocument();
    });

    it("removes an item after a confirmation that says what it does, then reloads the page", async () => {
      serve([item({ matched_count: 14 })]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      await userEvent.click(screen.getByRole("button", { name: "Remove" }));
      const confirm = await screen.findByRole("dialog", { name: "Remove Rent?" });
      expect(confirm).toHaveTextContent("This unlinks 14 matched transactions; they stay in your history.");
      expect(api).not.toHaveBeenCalledWith("/api/recurring/1", { method: "DELETE" });
      await userEvent.click(within(confirm).getByRole("button", { name: "Remove" }));
      await waitFor(() => expect(api).toHaveBeenCalledWith("/api/recurring/1", { method: "DELETE" }));
      await waitFor(() => expect(toast).toHaveBeenCalledWith("Removed"));
    });

    it("keeps the item when the confirmation is cancelled, and says so when nothing is linked", async () => {
      serve([item({ matched_count: 0 })]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      await userEvent.click(screen.getByRole("button", { name: "Remove" }));
      const confirm = await screen.findByRole("dialog", { name: "Remove Rent?" });
      expect(confirm).toHaveTextContent("No transactions are linked to it.");
      await userEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(api).not.toHaveBeenCalledWith("/api/recurring/1", { method: "DELETE" });
    });

    it("shows the transactions it matched, and hides them again", async () => {
      serve([item({ matched_count: 3 })], [], { "/api/transactions?recurring=1&limit=50": { items: [
        { id: "t1", posted: "2026-03-01", description: "RENT PAYMENT", amount: -1500, recurring_linked_by: "you" },
        { id: "t2", posted: "2026-02-01", description: "RENT PMT", amount: -1500, recurring_linked_by: "auto" },
        { id: "t3", posted: "2026-01-01", description: "OLD RENT", amount: -1500, recurring_linked_by: null }] } });
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      await userEvent.click(screen.getByRole("button", { name: "Show matched transactions" }));
      expect(await screen.findByText("RENT PAYMENT")).toBeInTheDocument();
      const rows = screen.getAllByRole("row");
      expect(rows.map((r) => r.textContent)).toEqual([expect.stringContaining("RENT PAYMENTlinked by you"),
        expect.stringContaining("RENT PMTmatched automatically"), expect.stringMatching(/OLD RENT-?\$1,500/)]);
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
