// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/categories.svelte", () => ({ loadCategories: vi.fn(async () => {}), categories: { list: [] }, catLabel: (x: string) => x, categoryGroups: () => [], catParentOf: () => null }));

import { api } from "$lib/api";
import { bodyOf, calls, card, churning, found, wish } from "$lib/components/churning/fixtures";
import type { BankBonus, ChurnCard } from "$lib/components/churning/types";
import Churning from "./Churning.svelte";

const serve = (over: Parameters<typeof churning>[0]) =>
  vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning(over))) as never);
const bankBonus = (over: Partial<BankBonus> = {}) => ({
  id: 1, owner: "Alex", bank: "Chase", account_type: "checking", account_id: null, opened_on: "2026-01-05", bonus: 300, dd_total: 500, dd_count: null,
  debit_count: null, min_balance: null, hold_until: null, other_reqs: null, deadline_days: 90, deadline: "2026-04-05", post_days: 30, manual_dd: null,
  manual_debits: null, status: "open", received_on: null, received_amount: null, closed_on: null, monthly_fee: 0, fee_waiver: null, early_close_fee: null,
  keep_open_days: null, repeat_months: null, once_per_lifetime: 0, eligible_on: null, notes: null,
  progress: { dd_total: 0, dd_count: 0, debits: 0, balance: null, balance_ok: null, source: "auto" }, state: "active", due: "2026-12-05", expected_on: null,
  safe_close_on: null, fee_reminder: null, eligibility: { status: "now", on: null, why: "", override: false }, ...over,
}) as unknown as BankBonus;

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({ ok: true } as never);
});

describe("the Churning page", () => {
  it("links to its assumptions, once, beside the title", async () => {
    serve({ cards: [card()] });
    render(Churning, { sub: "" });
    await screen.findByRole("button", { name: "Add a card" });
    expect(screen.getAllByRole("link", { name: "Assumptions" })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Assumptions" })).toHaveAttribute("href", "#setup/assumptions/churning");
  });

  it("shows 5/24 as 0/24 with a helpful note when there are no people yet, not a blank figure", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning({ people: [], owners: [] }))) as never);
    render(Churning, { sub: "" });
    const label = await screen.findByText("5/24", { selector: "dt span" });
    const stat = label.closest<HTMLElement>("div")!;
    expect(within(stat).getByText("0/24")).toBeInTheDocument();
    expect(within(stat).getByText("Add cards you’ve opened in the last 24 months")).toBeInTheDocument();
  });

  it("keeps its data and place when you switch tabs (no reload)", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning({ cards: [card()] }))) as never);
    const { rerender } = render(Churning, { sub: "" });
    await screen.findByRole("button", { name: "Add a card" });
    const before = calls("/api/churning").length;
    await rerender({ sub: "benefits" });
    expect(await screen.findByText("Worth a year")).toBeInTheDocument();
    expect(calls("/api/churning").length).toBe(before);   // the same data, not fetched again
  });

  it("shows 0/24 with a prompt when there are no people yet", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning({ people: [], owners: [] }))) as never);
    render(Churning);
    expect(await screen.findByText("0/24")).toBeInTheDocument();
    expect(screen.getByText("Add cards you’ve opened in the last 24 months")).toBeInTheDocument();
  });

  it("flags a person who is over 5/24 in the stat strip", async () => {
    const five24 = { count: 6, under: false, under_on: "2027-01-10", next_fall_off: "2027-01-10" };
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning({ people: ["Alex"], five24: { Alex: five24 } } as never))) as never);
    render(Churning);
    expect(await screen.findByText("6/24")).toHaveClass("text-[var(--warning)]");
  });

  it("puts the Cards section last, after Upcoming, Planned, Best card and Rewards", async () => {
    const full = churning({
      cards: [card()], wishlist: [wish()], rewards: { Alex: { currencies: [{ currency: "c1", name: "Capital One miles", earned: 100, bonuses: 0, balance: null, cents: 1.4, value: 1.4, balance_value: null }], value: 1.4, balance_value: 0 } },
      upcoming: [{ date: "2026-10-20", kind: "plan", card_id: 1, owner: "Alex", title: "Downgrade Venture X", detail: "", warn: false }],
    } as never);
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : full)) as never);
    render(Churning);
    await screen.findByRole("button", { name: "Add a card" });
    const titles = ["Upcoming", "Planned", "Best card for…", "Rewards", "Cards"];
    const heads = titles.map((t) => screen.getAllByText(t, { selector: "[data-slot=card-title]" })[0]);
    for (let i = 1; i < heads.length; i++) expect(heads[i - 1].compareDocumentPosition(heads[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows one getting-started block instead of the empty sections when there is nothing at all", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning())) as never);
    render(Churning);
    const block = await screen.findByTestId("getting-started");
    expect(within(block).getByRole("button", { name: "Add a card you’ve opened" })).toBeInTheDocument();
    expect(within(block).getByRole("button", { name: "Add a bank bonus" })).toBeInTheDocument();
    for (const t of ["Upcoming", "Planned", "Best card for…", "Rewards"]) expect(screen.queryByText(t)).toBeNull();
    expect(screen.getByRole("button", { name: "Add a card" })).toBeInTheDocument();   // the Cards tab and its button stay
    expect(calls(/best\?/)).toHaveLength(0);
    await userEvent.click(within(block).getByRole("button", { name: "Add a card you’ve opened" }));
    expect(await screen.findByPlaceholderText("e.g. Sapphire Preferred")).toBeInTheDocument();   // the new-card form opens
  });

  it("collapses empty sections to one line and hides Best card, and Rewards is a line, when there is one card and little else", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning({ cards: [card({ status: "closed" })] }))) as never);
    render(Churning);
    await screen.findByRole("button", { name: "Add a card" });
    expect(screen.queryByTestId("getting-started")).toBeNull();
    expect(screen.getByText("nothing in the next six months")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Plan a card or bonus" })).toBeInTheDocument();
    expect(screen.queryByText("Best card for…")).toBeNull();
    expect(screen.getByText("no points tracked yet")).toBeInTheDocument();
    expect(document.querySelectorAll("[data-slot=card-title]")).toHaveLength(1);   // only Cards
  });

  it("keeps Best card, and Rewards once a card has earned something, when there is data", async () => {
    const rewards = { Alex: { currencies: [{ currency: "c1", name: "Capital One miles", earned: 0, bonuses: 0, balance: 5000, as_of: "2026-09-01", cents: 1.4, value: 0, balance_value: 70 }], value: 0, balance_value: 70 } };
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning({ cards: [card()], rewards } as never))) as never);
    render(Churning);
    await screen.findByRole("button", { name: "Add a card" });
    expect(screen.getByText("Best card for…")).toBeInTheDocument();
    expect(screen.getByText("Rewards", { selector: "[data-slot=card-title]" })).toBeInTheDocument();
  });

  it("keeps Rewards as one line with an Add a balance action when nothing is tracked, and the action opens the balance entry", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning({ cards: [card()] }))) as never);
    render(Churning);
    await screen.findByRole("button", { name: "Add a card" });
    expect(screen.getByText("no points tracked yet")).toBeInTheDocument();
    expect(screen.queryByText("Rewards", { selector: "[data-slot=card-title]" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Add a balance" }));
    expect(screen.getByText("Rewards", { selector: "[data-slot=card-title]" })).toBeInTheDocument();
    expect(screen.queryByText("no points tracked yet")).toBeNull();
    expect(screen.getByLabelText("Add a balance for Alex")).toBeInTheDocument();
  });

  it("collapses the empty Bank bonuses tab to one line and hides Bonus money by year", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning({ cards: [card()] }))) as never);
    render(Churning, { sub: "bank" });
    await screen.findByText("none yet");
    expect(screen.queryByText("Bank bonuses", { selector: "[data-slot=card-title]" })).toBeNull();
    expect(screen.queryByText("Bonus money by year")).toBeNull();
    expect(screen.queryByText("No bank bonuses received yet.")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Add a bank bonus" }));
    expect(await screen.findByText("Bank bonuses", { selector: "[data-slot=card-title]" })).toBeInTheDocument();   // the form opens in its card
  });

  it("shows Bonus money by year once a bank bonus was received", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning({ cards: [card()], bank_income: { Alex: { "2026": 300 } } }))) as never);
    render(Churning, { sub: "bank" });
    expect(await screen.findByText("Bonus money by year")).toBeInTheDocument();
  });

  describe("Found on your accounts", () => {
    const serve = (drafts = [found(), found({ account_id: "acct-2", account_name: "CREDIT CARD (3392)", product: "", owner: "Sam", issuer: "other", annual_fee: null, opened_on: null })], dismissed: { account_id: string; name: string }[] = []) => {
      let current = { drafts, dismissed };
      vi.mocked(api).mockImplementation((async (path: string, opts?: { method?: string }) => {
        if (path === "/api/churning/found") return current;
        if (path.startsWith("/api/churning/found/") && opts?.method === "POST") {
          const [, , , , id, what] = path.split("/");
          const gone = current.drafts.find((x) => x.account_id === decodeURIComponent(id));
          current = what === "dismiss" && gone
            ? { drafts: current.drafts.filter((x) => x !== gone), dismissed: [...current.dismissed, { account_id: gone.account_id, name: gone.account_name }] }
            : { drafts: [found(), ...current.drafts.filter((x) => x.account_id !== "acct-1")], dismissed: [] };
          return { ok: true };
        }
        return path.startsWith("/api/churning/best") ? { cards: [] } : churning({ cards: [card()] });
      }) as never);
    };

    it("lists each account as a draft with Add and Dismiss", async () => {
      serve();
      render(Churning);
      const section = await screen.findByTestId("found-cards");
      expect(within(section).getByText("Found on your accounts")).toBeInTheDocument();
      expect(within(section).getByText("Sapphire Reserve")).toBeInTheDocument();
      expect(within(section).getByText(/\$795 annual fee/)).toBeInTheDocument();
      expect(within(section).getByText(/opened on or before Mar 2, 2024/)).toBeInTheDocument();
      expect(within(section).getByText("CREDIT CARD (3392)")).toBeInTheDocument();   // no product to show: the account's name
      expect(within(section).getAllByRole("button", { name: /^Add / })).toHaveLength(2);
    });

    it("shows nothing when there is nothing found", async () => {
      serve([]);
      render(Churning);
      await screen.findByRole("button", { name: "Add a card" });
      expect(screen.queryByTestId("found-cards")).toBeNull();
    });

    it("opens the add-card form pre-filled for review, without saving", async () => {
      serve();
      render(Churning);
      await userEvent.click(await screen.findByRole("button", { name: "Add Sapphire Reserve" }));
      expect(await screen.findByLabelText("Card")).toHaveValue("Sapphire Reserve");
      expect(screen.getByLabelText("Opened (on or before)")).toHaveValue("2024-03-02");
      expect(calls("/api/churning/cards")).toHaveLength(0);
      await userEvent.click(screen.getByRole("button", { name: "Add" }));
      await waitFor(() => expect(calls("/api/churning/cards")).toHaveLength(1));
      expect(bodyOf(calls("/api/churning/cards")[0])).toMatchObject({ account_id: "acct-1", product: "Sapphire Reserve" });
    });

    it("dismisses a draft, and brings it back", async () => {
      serve();
      render(Churning);
      await userEvent.click(await screen.findByRole("button", { name: "Dismiss Sapphire Reserve" }));
      await waitFor(() => expect(calls("/api/churning/found/acct-1/dismiss")).toHaveLength(1));
      await waitFor(() => expect(screen.queryByRole("button", { name: "Add Sapphire Reserve" })).toBeNull());
      const section = screen.getByTestId("found-cards");
      await userEvent.click(within(section).getByText("1 dismissed"));
      await userEvent.click(within(section).getByRole("button", { name: "Bring back Chase Sapphire Reserve (8814)" }));
      await waitFor(() => expect(calls("/api/churning/found/acct-1/undismiss")).toHaveLength(1));
      expect(await screen.findByRole("button", { name: "Add Sapphire Reserve" })).toBeInTheDocument();
    });

    it("hides the other person's drafts when one person is picked", async () => {
      serve();
      render(Churning);
      await screen.findByTestId("found-cards");
      await userEvent.click(screen.getByRole("radio", { name: "Alex" }));
      expect(screen.queryByRole("button", { name: "Add CREDIT CARD (3392)" })).toBeNull();
      expect(screen.getByRole("button", { name: "Add Sapphire Reserve" })).toBeInTheDocument();
    });
  });

  it("tucks paid and closed bank bonuses into a collapsed group and keeps the active ones shown", async () => {
    serve({ cards: [card()], bank: [
      bankBonus({ id: 1, bank: "Chase" }),
      bankBonus({ id: 2, bank: "Citi", state: "received", status: "received", received_on: "2026-03-01" }),
      bankBonus({ id: 3, bank: "Ally", state: "closed", status: "closed" }),
    ] });
    render(Churning, { sub: "bank" });
    const group = (await screen.findByTestId("done-bank")) as HTMLDetailsElement;
    expect(within(group).getByText("2 paid or closed")).toBeInTheDocument();
    expect(group.open).toBe(false);
    expect(screen.getByText("Chase checking").closest("details")).toBeNull();
    expect(screen.getByText("Citi checking").closest("details")).toBe(group);
    expect(screen.getByText("Ally checking").closest("details")).toBe(group);
    await userEvent.click(within(group).getByText("2 paid or closed"));
    expect(group.open).toBe(true);
  });

  it("has no paid group when every bank bonus is still active", async () => {
    serve({ cards: [card()], bank: [bankBonus()] });
    render(Churning, { sub: "bank" });
    await screen.findByText("Chase checking");
    expect(screen.queryByTestId("done-bank")).toBeNull();
  });

  it("no longer shows the bank bonuses in the year tile, and keeps Bonus money by year", async () => {
    serve({ cards: [card()], bank_income: { Alex: { "2026": 300 } } });
    render(Churning, { sub: "bank" });
    expect(await screen.findByText("Bonus money by year")).toBeInTheDocument();
    expect(screen.queryByText(/Bank bonuses in 2026/)).toBeNull();
  });

  it("doesn't list each category's earning rate on a card row", async () => {
    serve({ cards: [card({ rates: [{ category: "Travel", multiplier: 5 }] })] });
    render(Churning);
    await screen.findByRole("button", { name: "Add a card" });
    expect(screen.queryByText(/^Earns /)).toBeNull();
  });

  describe("the annual fee tile", () => {
    const due = (over: Partial<ChurnCard>) => card({ fee_due: "2026-10-20", annual_fee: 95, ...over });
    it("shows the fee total and count, and never asks keep, downgrade or close", async () => {
      serve({ cards: [due({ id: 1, plan: "keep" }), due({ id: 2, plan: "undecided" })] });
      render(Churning);
      expect(await screen.findByText("2 cards")).toBeInTheDocument();
      expect(screen.queryByText(/keep, downgrade or close/)).toBeNull();
      expect(screen.queryByText(/each with a plan/)).toBeNull();
    });
  });
});
