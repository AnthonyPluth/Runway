// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (importOriginal) => ({ ...(await importOriginal<typeof import("$lib/api")>()), api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import { isoDay } from "$lib/format";
import type { RecurringItem, Suggestion } from "$lib/components/recurring/types";
import type { Account } from "$lib/types";
import { toast } from "svelte-sonner";
import Recurring from "./RecurringView.svelte";

const accounts: Account[] = [{ id: "a1", name: "Checking", kind: "checking" }, { id: "a2", name: "Old", kind: "checking", hidden: 1 }];
const item = (extra: Partial<RecurringItem> = {}): RecurringItem => ({
  id: 1, name: "Rent", account_id: "a1", amount: -1500, frequency: "monthly", anchor_date: "2026-03-01", active: 1, matched_count: 0, next_date: "2026-04-01", ...extra,
});
const suggestion = (extra: Partial<Suggestion> = {}): Suggestion => ({ key: "a1|netflix|monthly", account_id: "a1", name: "Netflix", match: "NETFLIX", amount: -15.49, frequency: "monthly", anchor_date: "2026-03-05", count: 6, ...extra });
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
    const addButton = () => screen.getAllByRole("button", { name: /^(Add|Adding…)$/ }).at(-1)!;   // the form's; the page header has one too

    it("adds an item and says how many past transactions it matched; money out is the default, stored negative", async () => {
      await open();
      await userEvent.type(screen.getByRole("textbox", { name: /Name/ }), "Gym");
      await userEvent.type(screen.getByRole("spinbutton", { name: /Amount/ }), "120");
      await userEvent.click(addButton());
      await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Added · matched 3 past transactions"));
      expect(posted()[0][1]).toMatchObject({ method: "POST", body: { name: "Gym", account_id: "a1", amount: -120, frequency: "monthly", anchor_date: isoDay(), active: 1 } });
    });

    it("stores money in as a positive amount, and ignores a minus you type", async () => {
      await open();
      await userEvent.type(screen.getByRole("textbox", { name: /Name/ }), "Paycheck");
      await userEvent.click(screen.getByRole("radio", { name: "Money in" }));
      await userEvent.type(screen.getByRole("spinbutton", { name: /Amount/ }), "3100.50");
      await userEvent.click(addButton());
      await waitFor(() => expect(posted()).toHaveLength(1));
      expect((posted()[0][1] as { body: { amount: number } }).body.amount).toBe(3100.5);
    });

    it("uses the direction you pick even if you pick it after typing the amount", async () => {
      await open();
      await userEvent.type(screen.getByRole("textbox", { name: /Name/ }), "Paycheck");
      await userEvent.type(screen.getByRole("spinbutton", { name: /Amount/ }), "-40");
      await userEvent.click(screen.getByRole("radio", { name: "Money in" }));
      await userEvent.click(addButton());
      await waitFor(() => expect(posted()).toHaveLength(1));
      expect((posted()[0][1] as { body: { amount: number } }).body.amount).toBe(40);
    });

    it("starts on money out and today's date, with the amount asking for a plain positive number", async () => {
      await open();
      expect(screen.getByRole("radio", { name: "Money out" })).toBeChecked();
      expect(screen.getByLabelText(/Next date/)).toHaveValue(isoDay());
      expect(screen.getByRole("spinbutton", { name: /Amount/ })).toHaveAttribute("placeholder", "120.00");
      expect(screen.getByRole("textbox", { name: /Name/ })).toHaveAttribute("aria-required", "true");
    });

    it("keeps account, amount to forecast and merchant text under More options, with a line of help each", async () => {
      await open();
      expect(screen.getByText("More options")).toBeInTheDocument();
      expect(screen.getByText("Text on the bank statement, e.g. COMED. One per line to match any of them; blank uses the name.")).toBeInTheDocument();
      expect(screen.getByRole("combobox", { name: /Amount to forecast/ })).toHaveDescription(/Use the recent payments/);
      expect(screen.getByRole("combobox", { name: /Account/ })).toHaveValue("a1");
    });

    it("says what's missing next to each field instead of only in a toast, and doesn't send anything", async () => {
      await open();
      await userEvent.click(addButton());
      const name = screen.getByRole("textbox", { name: /Name/ });
      const amount = screen.getByRole("spinbutton", { name: /Amount/ });
      expect(name).toHaveAttribute("aria-invalid", "true");
      expect(name).toHaveAccessibleDescription("Enter a name, like Paycheck or Rent.");
      expect(amount).toHaveAttribute("aria-invalid", "true");
      expect(amount).toHaveAccessibleDescription("Enter an amount, like 120.00.");
      expect(name).toHaveFocus();
      expect(posted()).toHaveLength(0);
      await userEvent.type(name, "Gym");
      expect(name).not.toHaveAttribute("aria-invalid");   // and it clears as you fix it
    });

    it("needs the dates for a schedule of specific dates", async () => {
      await open();
      await userEvent.selectOptions(screen.getByRole("combobox", { name: /How often/ }), "dates");
      await userEvent.type(screen.getByRole("textbox", { name: /Name/ }), "Property tax");
      await userEvent.type(screen.getByRole("spinbutton", { name: /Amount/ }), "2000");
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
      await userEvent.type(screen.getByRole("spinbutton", { name: /Amount/ }), "30");
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
      await userEvent.type(screen.getByRole("spinbutton", { name: /Amount/ }), "30");
      await userEvent.click(addButton());
      await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Name, account and frequency are required"));
      expect(addButton()).toBeEnabled();
    });
  });

  describe("suggestions", () => {
    const form = () => within(screen.getByRole("heading", { name: "Add a recurring item" }).closest<HTMLElement>("[data-slot=card]")!);
    const posts = (path: string) => vi.mocked(api).mock.calls.filter((c) => c[0] === path && (c[1] as { method?: string } | undefined)?.method === "POST");

    it("lists what's spotted in your history, and Add fills the form to adjust rather than adding at once", async () => {
      serve([item()], [suggestion()]);
      render(Recurring);
      expect(await screen.findByText("Netflix")).toBeInTheDocument();
      expect(screen.getByText("monthly · 6× · last Mar 5")).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Add a recurring item" })).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Add Netflix" }));
      expect(await screen.findByRole("heading", { name: "Add a recurring item" })).toBeInTheDocument();
      expect(form().getByRole("textbox", { name: /Name/ })).toHaveValue("Netflix");
      expect(form().getByRole("spinbutton", { name: /Amount/ })).toHaveValue(15.49);
      expect(form().getByRole("radio", { name: "Money out" })).toBeChecked();
      expect(form().getByLabelText(/Next date/)).toHaveValue("2026-03-05");
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
      await userEvent.click(await screen.findByRole("button", { name: "Add Acme Payroll" }));
      expect(await form().findByRole("radio", { name: "Money in" })).toBeChecked();
      expect(form().getByRole("spinbutton", { name: /Amount/ })).toHaveValue(3100);
    });

    it("dismisses a suggestion with Not recurring, and it stays gone", async () => {
      serve([item()], [suggestion(), suggestion({ key: "a1|gym|monthly", name: "Gym", match: "GYM" })]);
      render(Recurring);
      await userEvent.click(await screen.findByRole("button", { name: "Netflix is not recurring" }));
      await waitFor(() => expect(screen.queryByText("Netflix")).not.toBeInTheDocument());
      expect(screen.getByText("Gym")).toBeInTheDocument();
      expect(posts("/api/recurring/suggestions/dismiss")[0][1]).toMatchObject({ method: "POST", body: { key: "a1|netflix|monthly" } });
    });

    it("keeps a suggestion when dismissing it fails", async () => {
      serve([item()], [suggestion()]);
      render(Recurring);
      await screen.findByText("Netflix");
      vi.mocked(api).mockRejectedValueOnce(new Error("Boom"));
      await userEvent.click(screen.getByRole("button", { name: "Netflix is not recurring" }));
      await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Boom"));
      expect(screen.getByText("Netflix")).toBeInTheDocument();
    });

    it("puts suggestions above the empty state when there are no items", async () => {
      serve([], [suggestion()]);
      render(Recurring);
      const spotted = await screen.findByRole("heading", { name: "Spotted in your history" });
      const empty = screen.getByText(/No recurring items yet/);
      expect(spotted.compareDocumentPosition(empty) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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

    it("shows money out or in by the sign of its amount, and the amount without it", async () => {
      serve([item(), item({ id: 2, name: "Paycheck", amount: 3000 })]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      await userEvent.click(screen.getByText("Paycheck"));
      const [pay, rent] = screen.getAllByRole("spinbutton", { name: /Amount/ });   // money in is listed first
      expect(rent).toHaveValue(1500);
      expect(pay).toHaveValue(3000);
      const [payOut, rentOut] = screen.getAllByRole("radio", { name: "Money out" });
      expect(rentOut).toBeChecked();
      expect(payOut).not.toBeChecked();
      expect(screen.getAllByRole("radio", { name: "Money in" })[0]).toBeChecked();
    });

    it("saves the amount with its sign kept, and flipping money in or out saves the flipped amount", async () => {
      serve([item()], [], { "/api/recurring/1": { linked: 0 } });
      render(Recurring);
      await userEvent.click(await screen.findByText("Rent"));
      const amount = screen.getAllByRole("spinbutton", { name: /Amount/ })[0];
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
      expect(screen.getByText("monthly · next May 1 · 3 matched")).toBeInTheDocument();
    });

    it("offers the recent payments' amount when they all missed a fixed amount, and saves it when you take it", async () => {
      serve([item({ name: "Paycheck", amount: 5000, frequency: "semimonthly", dates: "15,31", suggested_amount: 2100.47 })], [], { "/api/recurring/1": { linked: 0 } });
      render(Recurring);
      await userEvent.click(await screen.findByText("Paycheck"));
      expect(screen.getByText("The last payments were about $2,100, not $5,000.")).toBeInTheDocument();
      expect(api).not.toHaveBeenCalledWith("/api/recurring/1", expect.anything());   // nothing changes until you say so
      await userEvent.click(screen.getByRole("button", { name: "Use $2,100" }));
      await waitFor(() => expect(api).toHaveBeenCalledWith("/api/recurring/1", expect.objectContaining({ method: "POST", body: expect.objectContaining({ amount: 2100, amount_mode: "fixed" }) })));
      expect(screen.getAllByRole("spinbutton", { name: /Amount/ })[0]).toHaveValue(2100);
      expect(screen.queryByText(/The last payments were about/)).not.toBeInTheDocument();
    });

    it("shows the range where the server moved it with the new amount", async () => {
      serve([item({ name: "Paycheck", amount: 5000, frequency: "semimonthly", dates: "15,31", suggested_amount: 2100.47, amount_min: 3500, amount_max: 6500 })],
        [], { "/api/recurring/1": { linked: 1, amount_min: 1470, amount_max: 2730 } });
      render(Recurring);
      await userEvent.click(await screen.findByText("Paycheck"));
      expect(screen.getByLabelText("Smallest amount")).toHaveValue(3500);
      await userEvent.click(screen.getByRole("button", { name: "Use $2,100" }));
      await waitFor(() => expect(screen.getByLabelText("Smallest amount")).toHaveValue(1470));
      expect(screen.getByLabelText("Largest amount")).toHaveValue(2730);
    });

    it("stops offering the amount once you type your own", async () => {
      serve([item({ name: "Paycheck", amount: 5000, frequency: "semimonthly", dates: "15,31", suggested_amount: 2100.47 })]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Paycheck"));
      const amount = screen.getAllByRole("spinbutton", { name: /Amount/ })[0];
      await userEvent.clear(amount);
      await userEvent.type(amount, "2500");
      expect(screen.queryByText(/The last payments were about/)).not.toBeInTheDocument();   // not "about $2,100, not $2,500"
      await userEvent.clear(amount);
      await userEvent.type(amount, "5000");
      expect(screen.getByText("The last payments were about $2,100, not $5,000.")).toBeInTheDocument();
    });

    it("keeps offering the amount when saving it fails, with the old amount back", async () => {
      serve([item({ name: "Paycheck", amount: 5000, frequency: "semimonthly", dates: "15,31", suggested_amount: 2100.47 })]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Paycheck"));
      vi.mocked(api).mockRejectedValueOnce(new Error("Offline"));
      await userEvent.click(screen.getByRole("button", { name: "Use $2,100" }));
      await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Offline"));
      expect(screen.getAllByRole("spinbutton", { name: /Amount/ })[0]).toHaveValue(5000);
      expect(screen.getByRole("button", { name: "Use $2,100" })).toBeInTheDocument();
    });

    it("saves an amount range, and says when its ends are the wrong way round", async () => {
      serve([item({ name: "Paycheck", amount: 5000, amount_min: 3500, amount_max: 6500 })]);
      render(Recurring);
      await userEvent.click(await screen.findByText("Paycheck"));
      const lo = screen.getAllByRole("spinbutton", { name: "Smallest amount" })[0], hi = screen.getAllByRole("spinbutton", { name: "Largest amount" })[0];
      expect(lo).toHaveValue(3500);
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
      await userEvent.tab();   // blank: any amount up from $1,500
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
      // How each got linked (nothing for one from before Runway kept it).
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
