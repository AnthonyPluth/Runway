// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn().mockResolvedValue({}) }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import { categories } from "$lib/categories.svelte";
import type { ForecastEvent, StatementEstimate } from "$lib/types";
import { toast } from "svelte-sonner";
import { category } from "../../../test/fixtures";
import EventsList from "./EventsList.svelte";

type Ev = ForecastEvent & { late_from?: string | null };
const estimate = (): StatementEstimate => ({ close: "2026-03-01", due: "2026-03-26", charged_so_far: 300,
  budgets: [{ category: "Groceries", amount: 500 }, { category: "Dining", amount: 266.67 }], budgets_total: 766.67, statement: 1066.67, total: 1066.67 });
const ev = (extra: Partial<Ev> = {}): Ev => ({ date: "2026-03-15", name: "Rent", amount: -1500, kind: "recurring", key: "k1", balance_after: 900, ...extra });
// `events` is also a Testing Library mount option, so props go under `props`.
const show = (events: Ev[], extra: Record<string, unknown> = {}) => render(EventsList, { props: { events, onchanged: vi.fn(), ...extra } });

beforeEach(() => {
  app.state = null;
  categories.list = [category("Housing", { icon: "🏠" }), category("Credit Card Payment", { icon: "💳" })];
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({});
  vi.mocked(toast).mockClear(); vi.mocked(toast.success).mockClear(); vi.mocked(toast.error).mockClear();
});

describe("EventsList", () => {
  it("points to Recurring when nothing is scheduled", () => {
    show([]);
    expect(screen.getByRole("link", { name: "Recurring" })).toHaveAttribute("href", "#recurring");
  });

  it("shows each item's name, date, amount and the balance after it", () => {
    show([ev()]);
    expect(screen.getByText("Rent")).toBeInTheDocument();
    expect(screen.getByText(/Sun, Mar 15/)).toBeInTheDocument();
    expect(screen.getByText("projected balance $900.00")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "−$1,500.00" })).toBeInTheDocument();
  });

  it("marks a balance that goes negative", () => {
    show([ev({ balance_after: -20 })]);
    expect(screen.getByText("projected balance -$20.00")).toHaveClass("text-destructive");
  });

  it("shows income as a green plus amount", () => {
    show([ev({ name: "Pay", amount: 3000, kind: "recurring" })]);
    expect(screen.getByRole("button", { name: "+$3,000.00" })).toHaveClass("text-good");
  });

  describe("icon", () => {
    it("prefers the merchant's logo", () => {
      const { container } = show([ev({ logo: "/logos/landlord.png", category: "Housing" })]);
      expect(container.querySelector("img")).toHaveAttribute("src", "/logos/landlord.png");
    });

    it("uses the bank's logo for a card payment", () => {
      app.state = { connected: true, brands: { card1: { institution: "Chase", src: "/api/merchants/brand%3Achase/logo" } } };
      const { container } = show([ev({ kind: "card", card_id: "card1", name: "Chase Sapphire", key: undefined })]);
      const img = container.querySelector("img")!;
      expect(img).toHaveAttribute("src", "/api/merchants/brand%3Achase/logo");
      expect(img).toHaveAttribute("title", "Chase");
    });

    it("falls back to the category's emoji", () => {
      const { container } = show([ev({ category: "Housing" })]);
      expect(container.querySelector("img")).toBeNull();
      expect(container.textContent).toContain("🏠");
    });

    it("shows the Credit Card Payment emoji for a card with no bank logo", () => {
      const { container } = show([ev({ kind: "card", card_id: "x", key: undefined })]);
      expect(container.textContent).toContain("💳");
    });
  });

  describe("badges", () => {
    it("says when a payment that came in parts is only the rest", () => {
      show([ev({ name: "Paycheck", amount: 2849.6, paid_so_far: 2150.4, late_from: "2026-09-30" })]);
      expect(screen.getByText("rest")).toHaveAttribute("title", "$2,150.40 has come in already; this is the rest");
      expect(screen.getByText("late")).toHaveAttribute("title", "Was due 2026-09-30 and the rest hasn't shown up yet");
    });

    it("flags estimates, late items and edited amounts", () => {
      show([ev({ estimated: true, late_from: "2026-03-01" })]);
      // an estimate is an asterisk right after the amount, not a word or a badge
      const mark = screen.getByRole("img", { name: "estimate" });
      expect(mark).toHaveTextContent("*");
      expect(mark.parentElement).toHaveTextContent(/1,500\.00\*$/);
      expect(screen.queryByText("estimate")).not.toBeInTheDocument();
      expect(screen.getByText("late")).toHaveAttribute("title", "Was due 2026-03-01 and hasn't shown up yet");
    });

    it("drops the asterisk once you've changed the amount: it's yours, not an estimate", () => {
      // (the forecast doesn't mark an edited one as an estimate; this holds even if it did)
      show([ev({ estimated: true, overridden: true, original_amount: -1400, kind: "card", estimate: estimate() })]);
      expect(screen.getByText("edited")).toHaveAttribute("title", "Usually -$1,400.00");
      expect(screen.queryByRole("img", { name: "estimate" })).toBeNull();
      expect(screen.queryByRole("button", { name: "What this estimate is made of" })).toBeNull();
      expect(screen.getByRole("button", { name: "reset" })).toBeInTheDocument();
    });

    it("drops the asterisk from a recurring item with a learned amount once you've set this one", () => {
      show([ev({ name: "Power", estimated: true, overridden: true, original_amount: -120, amount: -140, recurring_id: 3 })]);
      expect(screen.getByText("edited")).toBeInTheDocument();
      expect(screen.queryByRole("img", { name: "estimate" })).toBeNull();
      expect(screen.getByRole("button", { name: "−$140.00" }).parentElement).not.toHaveTextContent("*");
    });

    it("lets a card with no statement yet (no key to edit it by) open its breakdown too", async () => {
      show([ev({ kind: "card", estimated: true, assumed_cycle: true, key: undefined, name: "New card statement", amount: -350,
        estimate: { close: "2026-02-28", due: "2026-03-25", assumed_cycle: true, owed_now: 40, budgets: [{ category: "Travel", amount: 310 }],
          budgets_total: 310, statement: 350, total: 350 } })]);
      const mark = screen.getByRole("button", { name: "What this estimate is made of" });
      await userEvent.click(mark);
      expect(document.getElementById(mark.getAttribute("aria-controls")!)).toHaveTextContent(/Owed on the card now \$40\.00/);
    });

    it("lists what a card statement's estimate is made of: in its tooltip, and under the row on a tap", async () => {
      show([ev({ kind: "card", estimated: true, name: "Visa statement", key: "cardclose:cc:2026-03-01", amount: -1066.67, estimate: estimate() })]);
      const mark = screen.getByRole("button", { name: "What this estimate is made of" });
      expect(mark).toHaveTextContent("*");
      expect(mark).toHaveClass("absolute", "left-full");
      expect(mark.title.split("\n")).toEqual(["Estimate for the Mar 1 statement", "Charged so far · $300.00",
        "Budgets on this card to Mar 1 · $766.67 (Groceries $500.00, Dining $266.67)", "= $1,066.67"].map((l) => l.replace(/Mar 1/g, "Mar\u00a01")));
      expect(mark).toHaveAttribute("aria-expanded", "false");
      expect(screen.queryByText("Charged so far")).toBeNull();   // collapsed by default
      await userEvent.click(mark);
      expect(mark).toHaveAttribute("aria-expanded", "true");
      const breakdown = document.getElementById(mark.getAttribute("aria-controls")!)!;
      expect(breakdown).toHaveTextContent(/Charged so far \$300\.00\s*Budgets on this card to/);
      expect(breakdown).toHaveTextContent("= $1,066.67");
      expect(api).not.toHaveBeenCalled();   // a tap on the asterisk doesn't edit the amount
      await userEvent.click(mark);
      expect(screen.queryByText("Charged so far")).toBeNull();
    });

    it("explains a card estimate differently from a recurring one", () => {
      show([ev({ kind: "card", estimated: true, key: undefined })]);
      expect(screen.getByRole("img", { name: "estimate" }).title).toMatch(/statement hasn't closed yet/);
    });

    it("says a card estimate is what's on the card plus its budgets and recurring charges, with no average", () => {
      show([ev({ kind: "card", estimated: true, key: undefined })]);
      const title = screen.getByRole("img", { name: "estimate" }).title;
      expect(title).toMatch(/your budgets paid with it and its recurring charges/);
      expect(title).not.toMatch(/average/);
    });

    it("links a recurring item's name to it in Recurring, with no repeat icon", () => {
      show([ev({ recurring_id: 7 })]);
      expect(screen.getByRole("link", { name: "Rent" })).toHaveAttribute("href", "#recurring?item=7");
      expect(screen.queryByRole("link", { name: "Open in Recurring" })).not.toBeInTheDocument();
    });

    it("has no Recurring link on a card payment", () => {
      show([ev({ kind: "card", key: undefined, name: "Visa statement" })]);
      expect(screen.queryByRole("link")).not.toBeInTheDocument();
    });

    it("hangs an estimate's asterisk past the amount, so amounts line up", () => {
      show([ev({ estimated: true })]);
      expect(screen.getByRole("img", { name: "estimate" })).toHaveClass("absolute", "left-full");
    });
  });

  describe("an annual fee", () => {
    const fee = (extra: Partial<Ev> = {}) => ev({ name: "Sapphire annual fee", amount: -95, kind: "fee", key: undefined, balance_after: undefined,
      category: "Fees & Interest", account_id: "cc", account: "Sapphire ••1234", paid_on: "2026-11-05", paid_from: "Checking", ...extra });

    it("says which card payment it's in instead of a balance, and can't be edited", () => {
      show([fee()]);
      expect(screen.getByText(/on Sapphire ••1234, paid with its Nov 5 payment/)).toBeInTheDocument();
      expect(screen.queryByText(/balance/)).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /95/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "Open in Recurring" })).not.toBeInTheDocument();
    });

    it("says when its card's payment isn't in the forecast", () => {
      show([fee({ paid_on: null, paid_from: null })]);
      expect(screen.getByText(/on Sapphire ••1234; its payment isn’t in the forecast/)).toBeInTheDocument();
    });

    it("says when its card isn't linked to an account", () => {
      show([fee({ account_id: null, account: null, paid_on: null, paid_from: null })]);
      expect(screen.getByText(/card not linked to an account, so not in the forecast/)).toBeInTheDocument();
    });
  });

  it("marks each item with its account's bank when several accounts' items are shown together", () => {
    app.state = { connected: true, brands: { chk: { institution: "Chase", initial: "C" } } };
    const { container } = show([ev({ account: "Checking", account_id: "chk" })], { accounts: true });
    const badge = container.querySelector("[data-account-badge]");
    expect(badge).toHaveAttribute("title", "Checking");                                      // on the logo's corner, as Transactions does
    expect(screen.queryByText("Checking", { selector: "div" })).toBeNull();                  // not a line of its own under the name
    expect(screen.getByText("Checking ·")).toBeInTheDocument();                              // and beside its projected balance
  });

  it("groups items by day under the date, with no dividers inside a day, and the projected balance once a day per account", () => {
    show([
      ev({ key: "a", name: "Taxes", account_id: "chk", balance_after: 5000 }),
      ev({ key: "b", name: "Card", account_id: "chk", balance_after: 2000 }),
      ev({ key: "c", name: "Rent", account_id: "sav", account: "Savings", balance_after: 700 }),
      ev({ key: "d", name: "Paycheck", date: "2026-03-16", amount: 3000, account_id: "chk", balance_after: 5000 }),
    ]);
    expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent!.replace(/\s/g, " "))).toEqual(["Sun, Mar 15", "Mon, Mar 16"]);
    expect(screen.getAllByText(/^projected balance /).map((b) => b.textContent))
      .toEqual(["projected balance $2,000.00", "projected balance $700.00", "projected balance $5,000.00"]);
    // a day is one block of the grouped list (its hairlines fall between blocks): the items of Mar 15 share one
    const day = document.querySelector("[data-day='2026-03-15']")!;
    expect(day.parentElement!.querySelectorAll(":scope > [data-day]")).toHaveLength(2);
    expect(day.textContent).toContain("Taxes");
    expect(day.textContent).toContain("Rent");
  });

  describe("limit", () => {
    const many = Array.from({ length: 10 }, (_, i) => ev({ name: `Bill ${i}`, key: `k${i}` }));

    it("shows the first few, then all of them on request", async () => {
      show(many, { limit: 3 });
      expect(screen.getAllByText(/^Bill \d$/)).toHaveLength(3);
      await userEvent.click(screen.getByRole("button", { name: /Show all 10/ }));
      expect(screen.getAllByText(/^Bill \d$/)).toHaveLength(10);
      expect(screen.queryByRole("button", { name: /Show all/ })).not.toBeInTheDocument();
    });

    it("collapses again with Show fewer", async () => {
      show(many, { limit: 3 });
      expect(screen.queryByRole("button", { name: "Show fewer" })).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: /Show all 10/ }));
      await userEvent.click(screen.getByRole("button", { name: "Show fewer" }));
      expect(screen.getAllByText(/^Bill \d$/)).toHaveLength(3);
      expect(screen.getByRole("button", { name: /Show all 10/ })).toBeInTheDocument();
    });

    it("has nothing to collapse when everything fits", () => {
      show(many.slice(0, 2), { limit: 3 });
      expect(screen.queryByRole("button", { name: "Show fewer" })).not.toBeInTheDocument();
    });
  });

  describe("changing one occurrence", () => {
    it("overrides just that date, keeping the sign of the original", async () => {
      show([ev()]);
      await userEvent.click(screen.getByRole("button", { name: "−$1,500.00" }));
      const input = screen.getByRole("spinbutton", { name: "Amount" });
      await userEvent.clear(input);
      await userEvent.type(input, "1400{Enter}");
      expect(api).toHaveBeenCalledWith("/api/overrides", { method: "POST", body: { key: "k1", amount: -1400 } });
      const [msg, opts] = lastToast();
      expect(msg).toBe("Changed for Mar\u00a015 only");
      expect(opts.action.label).toBe("Undo");   // not a recurring item's date: nothing to change from now on
      expect(opts.cancel).toBeUndefined();
    });

    type Btn = { label: string; onClick: () => Promise<void> };
    const lastToast = () => vi.mocked(toast).mock.calls.at(-1)! as unknown as [string, { description?: string; action: Btn; cancel?: Btn }];
    const edit = async (to: string) => {
      await userEvent.click(screen.getByRole("button", { name: /\$1,500\.00/ }));
      const input = screen.getByRole("spinbutton", { name: "Amount" });
      await userEvent.clear(input);
      await userEvent.type(input, `${to}{Enter}`);
      await vi.waitFor(() => expect(toast).toHaveBeenCalled());
    };
    const prev = { amount: -1500, amount_mode: "avg3", amount_since: "2026-01-01", amount_min: null, amount_max: null };
    const rent = () => ev({ key: "rec:3:2026-03-15", recurring_id: 3 });

    it("on a repeating item's date, asks whether it's from now on, and Undo takes the date's change back", async () => {
      const onchanged = vi.fn();
      show([rent()], { onchanged });
      await edit("1400");
      const [msg, opts] = lastToast();
      expect(msg).toBe("Changed for Mar\u00a015 only");
      expect(opts.action.label).toBe("From now on");
      expect(opts.cancel!.label).toBe("Undo");
      expect(onchanged).toHaveBeenCalledTimes(1);
      await opts.cancel!.onClick();
      expect(api).toHaveBeenLastCalledWith("/api/overrides", { method: "DELETE", body: { key: "rec:3:2026-03-15" } });
      expect(onchanged).toHaveBeenCalledTimes(2);
    });

    it("Undo puts back the date's earlier change, when it had one", async () => {
      show([ev({ key: "rec:3:2026-03-15", recurring_id: 3, overridden: true, amount: -1500, paid_so_far: -100 })]);
      await edit("1400");
      await lastToast()[1].cancel!.onClick();
      expect(api).toHaveBeenLastCalledWith("/api/overrides", { method: "POST", body: { key: "rec:3:2026-03-15", amount: -1600 } });
    });

    it("From now on makes it the item's amount (a fixed one, saying so when it was learned) and drops the date's change; Undo puts both back", async () => {
      vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/recurring/3/amount" ? { ok: true, previous: prev } : {})) as never);
      const onchanged = vi.fn();
      show([rent()], { onchanged });
      await edit("1400");
      await lastToast()[1].action.onClick();
      expect(api).toHaveBeenCalledWith("/api/recurring/3/amount", { method: "POST", body: { amount: -1400, key: "rec:3:2026-03-15" } });
      expect(onchanged).toHaveBeenCalledTimes(2);
      const [msg, opts] = lastToast();
      expect(msg).toBe("Rent is $1,400.00 from now on");
      expect(opts.description).toBe("A fixed amount now, not one from recent payments");
      await opts.action.onClick();   // Undo
      expect(api).toHaveBeenCalledWith("/api/recurring/3/amount", { method: "POST", body: { restore: prev } });
      expect(api).toHaveBeenLastCalledWith("/api/overrides", { method: "POST", body: { key: "rec:3:2026-03-15", amount: -1400 } });
      expect(onchanged).toHaveBeenCalledTimes(3);
    });

    it("says nothing more about the amount when the item already had a fixed one", async () => {
      vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/recurring/3/amount" ? { ok: true, previous: { ...prev, amount_mode: "fixed" } } : {})) as never);
      show([rent()]);
      await edit("1400");
      await lastToast()[1].action.onClick();
      expect(lastToast()[1].description).toBeUndefined();
    });

    it("shows the error when From now on fails", async () => {
      vi.mocked(api).mockImplementation((async (path: string) => { if (path.endsWith("/amount")) throw new Error("Offline"); return {}; }) as never);
      show([rent()]);
      await edit("1400");
      await lastToast()[1].action.onClick();
      expect(toast.error).toHaveBeenCalledWith("Offline");
    });

    it("on a one-time item, the change is the item's own, with Undo putting its amount back", async () => {
      vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/overrides" ? { ok: true, item: { id: 5, name: "Tax refund" }, previous: prev } : {})) as never);
      const onchanged = vi.fn();
      show([ev({ name: "Tax refund", key: "rec:5:2026-03-15", recurring_id: 5 })], { onchanged });
      await edit("1400");
      const [msg, opts] = lastToast();
      expect(msg).toBe("Tax refund is $1,400.00 now");
      expect(opts.action.label).toBe("Undo");
      expect(opts.cancel).toBeUndefined();
      await opts.action.onClick();
      expect(api).toHaveBeenLastCalledWith("/api/recurring/5/amount", { method: "POST", body: { restore: prev } });
      expect(onchanged).toHaveBeenCalledTimes(2);
    });

    it("keeps everything and shows the error when saving the change fails", async () => {
      vi.mocked(api).mockRejectedValueOnce(new Error("Offline"));
      const onchanged = vi.fn();
      show([rent()], { onchanged });
      await userEvent.click(screen.getByRole("button", { name: "−$1,500.00" }));
      await userEvent.type(screen.getByRole("spinbutton", { name: "Amount" }), "{Control>}a{/Control}1400{Enter}");
      await vi.waitFor(() => expect(toast.error).toHaveBeenCalledWith("Offline"));
      expect(onchanged).not.toHaveBeenCalled();
      expect(toast).not.toHaveBeenCalled();
    });

    it("loads the forecast again in place, without drawing the page afresh", async () => {
      const onchanged = vi.fn(), version = app.version;
      show([ev({ overridden: true, original_amount: -1400 })], { onchanged });
      await userEvent.click(screen.getByRole("button", { name: "−$1,500.00" }));
      const input = screen.getByRole("spinbutton", { name: "Amount" });
      await userEvent.clear(input);
      await userEvent.type(input, "1400{Enter}");
      await vi.waitFor(() => expect(onchanged).toHaveBeenCalledTimes(1));
      await userEvent.click(screen.getByRole("button", { name: "reset" }));
      await vi.waitFor(() => expect(onchanged).toHaveBeenCalledTimes(2));
      expect(app.version).toBe(version);
    });

    it("on the rest of one paid in parts, saves the whole occurrence: what you typed plus what came", async () => {
      show([ev({ amount: 2849.6, paid_so_far: 2150.4 })]);
      await userEvent.click(screen.getByRole("button", { name: "+$2,849.60" }));
      const input = screen.getByRole("spinbutton");
      await userEvent.clear(input);
      await userEvent.type(input, "3000{Enter}");
      expect(api).toHaveBeenCalledWith("/api/overrides", { method: "POST", body: { key: "k1", amount: 5150.4 } });
    });

    it("keeps an income positive", async () => {
      show([ev({ amount: 3000 })]);
      await userEvent.click(screen.getByRole("button", { name: "+$3,000.00" }));
      const input = screen.getByRole("spinbutton");
      await userEvent.clear(input);
      await userEvent.type(input, "3100{Enter}");
      expect(api).toHaveBeenCalledWith("/api/overrides", { method: "POST", body: { key: "k1", amount: 3100 } });
    });

    it("shows the error if it can't be saved", async () => {
      vi.mocked(api).mockRejectedValueOnce(new Error("Nope"));
      show([ev()]);
      await userEvent.click(screen.getByRole("button", { name: "−$1,500.00" }));
      const input = screen.getByRole("spinbutton");
      await userEvent.clear(input);
      await userEvent.type(input, "1{Enter}");
      expect(toast.error).toHaveBeenCalledWith("Nope");
    });

    it("can't be edited when the item has no key (a card statement)", () => {
      show([ev({ key: undefined })]);
      expect(screen.queryByRole("button", { name: /1,500/ })).not.toBeInTheDocument();
      expect(screen.getByText("−$1,500.00")).toBeInTheDocument();
    });

    it("calls a recurring date edited to $0 skipped (Recurring's Skip the next one), with the same reset", async () => {
      show([ev({ amount: 0, recurring_id: 3, overridden: true, original_amount: -1500 })]);
      expect(screen.getByText("skipped")).toHaveAttribute("title", "Usually -$1,500.00");
      expect(screen.queryByText("edited")).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "reset" }));
      expect(api).toHaveBeenCalledWith("/api/overrides", { method: "DELETE", body: { key: "k1" } });
    });

    it("resets an edited amount to the usual one", async () => {
      show([ev({ overridden: true, original_amount: -1400 })]);
      await userEvent.click(screen.getByRole("button", { name: "reset" }));
      expect(api).toHaveBeenCalledWith("/api/overrides", { method: "DELETE", body: { key: "k1" } });
      expect(toast.success).toHaveBeenCalledWith("Back to the usual amount");
    });
  });
});
