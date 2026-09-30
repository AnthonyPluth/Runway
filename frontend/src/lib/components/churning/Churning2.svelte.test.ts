// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/categories.svelte", () => ({ loadCategories: vi.fn(async () => {}), categories: { list: [] }, catLabel: (x: string) => x, categoryGroups: () => [] }));

import { api } from "$lib/api";
import OwnerSelect from "$lib/components/OwnerSelect.svelte";
import { toast } from "svelte-sonner";
import Churning from "../../../pages/Churning.svelte";
import BestCard from "./BestCard.svelte";
import CardBenefits from "./CardBenefits.svelte";
import CardForm from "./CardForm.svelte";
import { benefit, card, churning, wish } from "./fixtures";
import Planned from "./Planned.svelte";
import Rewards from "./Rewards.svelte";
import Upcoming from "./Upcoming.svelte";
import type { UpcomingItem } from "./types";

const calls = (path: string | RegExp) =>
  vi.mocked(api).mock.calls.filter((c) => (typeof path === "string" ? c[0] === path : path.test(c[0] as string)));
const bodyOf = (call: unknown[]) => (call[1] as { body?: Record<string, unknown> } | undefined)?.body;

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({ ok: true } as never);
  vi.mocked(toast).mockReset();
  vi.mocked(toast.error).mockReset();
});

describe("owner select", () => {
  it("offers the people alphabetically, keeps an old name that's set, and leaves out Joint", () => {
    render(OwnerSelect, { owners: ["Alex", "Sam", "Joint"], value: "Pat" });
    expect(within(screen.getByRole("combobox")).getAllByRole("option").map((o) => o.textContent)).toEqual(["Alex", "Pat", "Sam"]);
  });
});

describe("card form", () => {
  const setup = (c = null as ReturnType<typeof card> | null) => {
    const d = churning();
    render(CardForm, { c, d, person: "", onclose: vi.fn(), onchanged: vi.fn() });
    return d;
  };

  it("uses a dropdown of the people for whose card it is, and groups currencies by program", () => {
    setup();
    const owner = screen.getByLabelText("Whose card");
    expect(owner.tagName).toBe("SELECT");
    expect(within(owner).getAllByRole("option").map((o) => o.textContent)).toEqual(["Alex", "Sam"]);
    const earns = screen.getByLabelText("Earns");
    expect([...earns.querySelectorAll("optgroup")].map((g) => g.label)).toEqual(["Bank points", "Airline miles", "Hotel points", "Cash back"]);
  });

  it("sends the earning rates, portal-only ones included, in the request that adds the card", async () => {
    setup();
    await userEvent.type(screen.getByLabelText("Card"), "Venture X");
    await userEvent.click(screen.getByRole("button", { name: "Add a rate" }));
    await userEvent.selectOptions(screen.getByLabelText("Category of rate 1"), "Hotels");
    await userEvent.type(screen.getByLabelText("Points per dollar on Hotels"), "10");
    await userEvent.click(screen.getByLabelText("Only through the issuer's travel portal"));
    await userEvent.type(screen.getByLabelText("The portal's name"), "Capital One Travel");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(calls("/api/churning/cards")).toHaveLength(1));
    const body = bodyOf(calls("/api/churning/cards")[0])!;
    expect(body).toMatchObject({ owner: "Alex", product: "Venture X", portal_name: "Capital One Travel" });
    expect(body.rates).toEqual([{ category: "*", multiplier: 1, portal_only: false }, { category: "Hotels", multiplier: 10, portal_only: true }]);
    expect(body).not.toHaveProperty("base_rate");   // it travels inside rates
  });

  it("shows the server's message when the rates are refused", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Pick a category for each earning rate"));
    setup();
    await userEvent.type(screen.getByLabelText("Card"), "Venture X");
    await userEvent.click(screen.getByRole("button", { name: "Add a rate" }));
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Pick a category for each earning rate");
    expect(toast.error).toHaveBeenCalledWith("Pick a category for each earning rate");
  });

  it("saves the whole list of rates when a row of an existing card changes", async () => {
    setup(card({ rates: [{ category: "Travel", multiplier: 5, portal_only: false }] }));
    const mult = screen.getByLabelText("Points per dollar on Travel");
    await userEvent.clear(mult);
    await userEvent.type(mult, "6");
    await userEvent.tab();
    await waitFor(() => expect(calls("/api/churning/cards/1")).toHaveLength(1));
    expect(bodyOf(calls("/api/churning/cards/1")[0])!.rates).toEqual([
      { category: "*", multiplier: 2, portal_only: false }, { category: "Travel", multiplier: 6, portal_only: false }]);
  });

  it("saves a plan and a hidden card as you change them", async () => {
    setup(card());
    await userEvent.selectOptions(screen.getByLabelText("What I'll do with it"), "product_change");
    await waitFor(() => expect(bodyOf(calls("/api/churning/cards/1").at(-1)!)).toEqual({ plan: "product_change" }));
    await userEvent.click(screen.getByLabelText("Don't show this card in Upcoming"));
    await waitFor(() => expect(bodyOf(calls("/api/churning/cards/1").at(-1)!)).toEqual({ hide_upcoming: true }));
    expect(screen.getByText(/the day before the next annual fee posts/)).toBeInTheDocument();
  });
});

describe("best card", () => {
  it("asks for portal rates when you'll book through the portal, and shows the portal note", async () => {
    const row = { id: 1, owner: "Alex", product: "Venture X", issuer: "capital_one", multiplier: 10, currency: "Capital One miles", cents: 1.4,
      return_pct: 14, value: null, bonus: null, needs_portal: true, portal_name: "Capital One Travel", portal_option: null,
      note: "Only when booked through Capital One Travel" };
    vi.mocked(api).mockImplementation((async (path: string) => ({ cards: [path.includes("portal=1") ? row : { ...row, multiplier: 2, needs_portal: false, note: null }] })) as never);
    render(BestCard, { person: "", version: 0, showOwner: false });
    await screen.findByText("Venture X");
    expect(calls(/best\?/).at(-1)![0]).not.toContain("portal=1");
    await userEvent.click(screen.getByLabelText("I'll book through the issuer's travel portal"));
    expect(await screen.findByText("Only when booked through Capital One Travel")).toBeInTheDocument();
    expect(screen.getByText("Via Capital One Travel")).toBeInTheDocument();
    expect(calls(/best\?/).at(-1)![0]).toContain("portal=1");
  });

  it("offers the better portal rate when portal isn't ticked", async () => {
    const row = { id: 1, owner: "Alex", product: "Venture X", issuer: "capital_one", multiplier: 2, currency: "miles", cents: 1.4, return_pct: 2.8,
      value: null, bonus: null, needs_portal: false, portal_name: "Capital One Travel",
      portal_option: { multiplier: 10, return_pct: 14, portal_name: "Capital One Travel", value: null, note: "" }, note: "10x if booked through Capital One Travel" };
    vi.mocked(api).mockResolvedValue({ cards: [row] } as never);
    render(BestCard, { person: "", version: 0, showOwner: false });
    expect(await screen.findByText("10x if booked through Capital One Travel")).toBeInTheDocument();
  });
});

describe("benefits", () => {
  const setup = (over = {}) => {
    const onchanged = vi.fn();
    const c = card({ annual_fee: 395, benefits: [benefit(over)], benefits_value: 300, net_fee: 95 });
    render(CardBenefits, { card: c, d: churning(), onchanged });
    return onchanged;
  };

  it("says what's left this period and marks the rest used, with an undo", async () => {
    vi.mocked(api).mockResolvedValue({ id: 7 } as never);
    const onchanged = setup({ used: 100, remaining: 200, used_count: 1 });
    expect(screen.getByText(/\$100 of \$300 used/)).toBeInTheDocument();
    expect(screen.getByText(/Benefits \$300\/yr · net fee \$95/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Mark Travel credit used" }));
    await waitFor(() => expect(calls("/api/churning/benefits/1/use")).toHaveLength(1));
    expect(bodyOf(calls("/api/churning/benefits/1/use")[0])).toEqual({});   // no amount: the rest
    expect(onchanged).toHaveBeenCalled();
    // The toast's Undo removes that very use.
    const opts = vi.mocked(toast).mock.calls.at(-1)![1] as { action: { label: string; onClick: () => void } };
    expect(opts.action.label).toBe("Undo");
    opts.action.onClick();
    await waitFor(() => expect(bodyOf(calls("/api/churning/benefits/1/unuse")[0])).toEqual({ use_id: 7 }));
  });

  it("marks a partial amount used, and undoes the latest use from the button", async () => {
    vi.mocked(api).mockResolvedValue({ id: 8 } as never);
    setup({ used: 100, remaining: 200, used_count: 1, uses: [{ id: 3, amount_used: 100, used_on: "2026-09-01" }] });
    await userEvent.type(screen.getByLabelText("Amount of Travel credit used (blank: the rest)"), "50");
    await userEvent.click(screen.getByRole("button", { name: "Mark Travel credit used" }));
    await waitFor(() => expect(bodyOf(calls("/api/churning/benefits/1/use")[0])).toEqual({ amount: 50 }));
    await userEvent.click(screen.getByRole("button", { name: "Undo the last use of Travel credit" }));
    await waitFor(() => expect(bodyOf(calls("/api/churning/benefits/1/unuse")[0])).toEqual({}));
    expect(screen.getByText("History (1)")).toBeInTheDocument();
  });

  it("disables Mark used once a credit is all used, and adds a preset", async () => {
    vi.mocked(api).mockResolvedValue({ id: 9 } as never);
    setup({ used: 300, remaining: 0, used_count: 1 });
    expect(screen.getByRole("button", { name: "Mark Travel credit used" })).toBeDisabled();
    expect(screen.getByText(/All \$300 used/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Add a benefit to Venture X"), "lounge");
    await waitFor(() => expect(bodyOf(calls("/api/churning/cards/1/benefits")[0])).toEqual({ preset: "lounge" }));
  });
});

describe("upcoming", () => {
  const item = (over: Partial<UpcomingItem>): UpcomingItem => ({ date: "2026-10-20", kind: "plan", card_id: 1, owner: "Alex", title: "Downgrade Venture X", detail: "", warn: false, ...over });
  const setup = (items: UpcomingItem[]) => {
    const onchanged = vi.fn();
    render(Upcoming, { items, cards: [], today: "2026-09-30", showOwner: false, onchanged });
    return onchanged;
  };

  it("labels the new kinds", () => {
    setup([item({}), item({ kind: "benefit", benefit_id: 4, title: "Travel credit: $200 left" }), item({ kind: "apply", card_id: null, title: "You can apply for Gold" }),
      item({ kind: "offer_ends", card_id: null, title: "Offer for Gold ends Oct 25" })]);
    for (const l of ["Your plan", "Card credit", "Apply", "Offer ends"]) expect(screen.getAllByText(l).length).toBeGreaterThan(0);
  });

  it("checks a plan off, tells what changed, and undoes it", async () => {
    vi.mocked(api).mockResolvedValue({ changes: ["Venture X is marked product-changed on 2026-09-30"] } as never);
    const onchanged = setup([item({})]);
    await userEvent.click(screen.getByRole("button", { name: 'Mark "Downgrade Venture X" done' }));
    await waitFor(() => expect(calls("/api/churning/cards/1/plan/done")).toHaveLength(1));
    const [msg, opts] = vi.mocked(toast).mock.calls.at(-1)! as [string, { description: string; action: { label: string; onClick: () => void } }];
    expect(msg).toBe("Done");
    expect(opts.description).toContain("Venture X is marked product-changed");
    expect(onchanged).toHaveBeenCalled();
    opts.action.onClick();
    await waitFor(() => expect(calls("/api/churning/cards/1/plan/undo")).toHaveLength(1));
  });

  it("marks a credit used from Upcoming", async () => {
    vi.mocked(api).mockResolvedValue({ id: 2 } as never);
    setup([item({ kind: "benefit", benefit_id: 4, title: "Travel credit: $200 left" })]);
    await userEvent.click(screen.getByRole("button", { name: 'Mark "Travel credit: $200 left" used' }));
    await waitFor(() => expect(calls("/api/churning/benefits/4/use")).toHaveLength(1));
  });

  it("snoozes a to-do", async () => {
    vi.mocked(api).mockResolvedValue({ snooze_until: "2026-10-07" } as never);
    setup([item({ kind: "task", task_id: 9, card_id: 1, title: "Call Citi" })]);
    await userEvent.click(screen.getByRole("button", { name: 'Snooze "Call Citi"' }));
    await userEvent.click(screen.getByRole("button", { name: "1 week" }));
    await waitFor(() => expect(bodyOf(calls("/api/churning/tasks/9/snooze")[0])).toEqual({ days: 7 }));
  });
});

describe("empty sections", () => {
  it("Upcoming is one line with an inline Add a to-do that opens the form", async () => {
    render(Upcoming, { items: [], cards: [card()], today: "2026-09-30", showOwner: false, onchanged: vi.fn() });
    expect(screen.getByText("nothing in the next six months")).toBeInTheDocument();
    expect(document.querySelector("[data-slot=card-title]")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Add a to-do" }));
    expect(screen.getByLabelText("What to do")).toBeInTheDocument();
  });

  it("Upcoming offers no to-do action when there is no open card to attach it to", () => {
    render(Upcoming, { items: [], cards: [], today: "2026-09-30", showOwner: false, onchanged: vi.fn() });
    expect(screen.queryByRole("button", { name: "Add a to-do" })).toBeNull();
  });

  it("Planned is one line with an inline action that opens the form", async () => {
    render(Planned, { d: churning({ wishlist: [] }), person: "", showOwner: false, onchanged: vi.fn(), onapplied: vi.fn() });
    expect(document.querySelector("[data-slot=card-title]")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Plan a card or bonus" }));
    expect(screen.getByText("Planned", { selector: "[data-slot=card-title]" })).toBeInTheDocument();
  });
});

describe("planned", () => {
  const setup = (wishlist = [wish()], scores = {}) => {
    const onchanged = vi.fn(), onapplied = vi.fn();
    render(Planned, { d: churning({ wishlist, scores }), person: "", showOwner: true, onchanged, onapplied });
    return { onchanged, onapplied };
  };

  it("lists what's in the way of a planned card, and says when nothing is", () => {
    setup([
      wish({ id: 1, product: "Sapphire Preferred", blockers: [{ kind: "five24", text: "Alex is at 5/24", date: "2026-10-21" }], earliest_apply: "2026-10-21",
        hints: ["Still spending toward the Gold bonus"] }),
      wish({ id: 2, product: "Platinum", ready: true, status: "ready", priority: 2 }),
    ]);
    const first = screen.getByRole("list", { name: "What's in the way of Sapphire Preferred" });
    expect(within(first).getByText(/Alex is at 5\/24/)).toBeInTheDocument();
    expect(screen.getByText(/Earliest you can apply/)).toBeInTheDocument();
    expect(screen.getByText("Still spending toward the Gold bonus")).toBeInTheDocument();
    expect(screen.getAllByText("Ready to apply")).toHaveLength(1);
    expect(screen.getByText("Sapphire Preferred").closest("li")).not.toHaveTextContent("Ready to apply");
  });

  it("shows the score against what a planned card wants", () => {
    setup([wish({ min_score: 740, blockers: [{ kind: "score", text: "Wants a score of 740", date: null }] })],
      { Alex: { owner: "Alex", score: 705, as_of: "2026-09-03", source: "Credit Karma", history: [] } });
    expect(screen.getByText("705 of 740 wanted")).toBeInTheDocument();
    expect(screen.getByText("Alex's credit score").parentElement).toHaveTextContent("705");
  });

  it("marks a planned card applied and opens the new card", async () => {
    vi.mocked(api).mockResolvedValue({ kind: "card", id: 12, wish_id: 1 } as never);
    const { onapplied } = setup();
    await userEvent.click(screen.getByRole("button", { name: "I applied for Sapphire Preferred" }));
    await waitFor(() => expect(calls("/api/churning/wishlist/1/applied")).toHaveLength(1));
    expect(onapplied).toHaveBeenCalledWith("card", 12);
  });

  it("links to where to apply, in a new tab, only when a link was entered", () => {
    setup([wish({ id: 1, apply_url: "https://creditcards.chase.com/apply" }), wish({ id: 2, product: "Gold" })]);
    const link = screen.getByRole("link", { name: "Open the application for Sapphire Preferred" });
    expect(link).toHaveAttribute("href", "https://creditcards.chase.com/apply");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(screen.queryByRole("link", { name: "Open the application for Gold" })).not.toBeInTheDocument();
  });

  it("collapses applied and dropped items under a toggle", async () => {
    setup([wish(), wish({ id: 2, product: "Old one", status: "dropped" }), wish({ id: 3, product: "Got it", status: "applied", applied_on: "2026-09-01" })]);
    expect(screen.queryByText("Old one")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Show applied and dropped \(2\)/ }));
    expect(screen.getByText("Old one")).toBeInTheDocument();
    expect(screen.getByText("Got it")).toBeInTheDocument();
  });

  it("moves an item up by renumbering priorities, and saves a score", async () => {
    const { onchanged } = setup([wish({ id: 1, priority: 1 }), wish({ id: 2, product: "Gold", priority: 2 })]);
    await userEvent.click(screen.getByRole("button", { name: "Move Gold up" }));
    await waitFor(() => expect(calls(/wishlist\/\d/)).toHaveLength(2));
    expect(calls(/wishlist\/\d/).map((c) => [c[0], bodyOf(c)])).toEqual([["/api/churning/wishlist/2", { priority: 1 }], ["/api/churning/wishlist/1", { priority: 2 }]]);
    await waitFor(() => expect(onchanged).toHaveBeenCalled());
  });

  it("saves a credit score", async () => {
    setup();
    await userEvent.click(screen.getAllByRole("button", { name: /credit score/ })[0]);
    await userEvent.type(screen.getByLabelText("Alex's score"), "720");
    await userEvent.click(screen.getByRole("button", { name: "Save score" }));
    await waitFor(() => expect(bodyOf(calls("/api/churning/scores")[0])).toMatchObject({ owner: "Alex", score: 720, as_of: "2026-09-30" }));
  });
});

describe("rewards", () => {
  const row = { currency: "aa", name: "American AAdvantage", earned: 0, bonuses: 0, balance: 40000, as_of: "2026-08-30", cents: 1.4, value: 0,
    balance_value: 560, earned_since: 1300, est_balance: 41300, est_value: 578.2 };
  const setup = () => {
    const d = churning({ rewards: { Alex: { currencies: [row], value: 0, balance_value: 560 }, Sam: { currencies: [], value: 0, balance_value: 0 } } });
    render(Rewards, { d, people: ["Alex"], onchanged: vi.fn() });
  };

  it("shows the balance as of its day and a labeled estimate of the balance now", () => {
    setup();
    expect(screen.getByLabelText("Day of Alex's American AAdvantage balance")).toHaveValue("2026-08-30");
    expect(screen.getByText(/Estimated now/)).toHaveTextContent("~41,300");
    expect(screen.getByText(/1,300 earned since/)).toBeInTheDocument();
    expect(screen.getByText("$560", { selector: "td" })).toBeInTheDocument();   // Worth stays the balance you entered
  });

  it("saves a new balance as of today unless you set its day", async () => {
    setup();
    const box = screen.getByLabelText("Alex's American AAdvantage balance");
    await userEvent.clear(box);
    await userEvent.type(box, "42000");
    await userEvent.tab();
    await waitFor(() => expect(bodyOf(calls("/api/churning/balances")[0])).toEqual({ owner: "Alex", currency: "aa", points: "42000", as_of: "2026-09-30" }));
  });

  it("groups the point values and says which are estimates and which are yours", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Point values" }));
    expect(screen.getByText("Airline miles")).toBeInTheDocument();
    expect(screen.getByText("your value")).toBeInTheDocument();      // AA is overridden
    expect(screen.getAllByText(/estimate \(as of Jun 2026\)/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Estimates, not official values/)).toBeInTheDocument();
  });
});

describe("the Churning page", () => {
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
});
