// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/categories.svelte", () => ({ loadCategories: vi.fn(async () => {}), categories: { list: [] }, catLabel: (x: string) => x, categoryGroups: () => [], catParentOf: () => null }));

import { api } from "$lib/api";
import { bankBonus, benefit, bodyOf, calls, card, churning, found, wish } from "$lib/components/churning/fixtures";
import type { ChurnCard } from "$lib/components/churning/types";
import Churning from "./Churning.svelte";

const serve = (over: Parameters<typeof churning>[0]) =>
  vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning(over))) as never);
beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({ ok: true } as never);
});

describe("the Churning page", () => {
  it("shows 5/24 as 0/24 with a helpful note when there are no people yet, not a blank figure", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning({ people: [], owners: [], cards: [card()] }))) as never);
    render(Churning, { sub: "" });
    const label = await screen.findByText("5/24", { selector: "dt span" });
    const stat = label.closest<HTMLElement>("div")!;
    expect(within(stat).getByText("0/24")).toBeInTheDocument();
    expect(within(stat).getByText("Add cards you’ve opened in the last 24 months")).toBeInTheDocument();
  });

  it("keeps its data and place when you switch tabs (no reload)", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning({ cards: [card({ benefits: [benefit()] })] }))) as never);
    const { rerender } = render(Churning, { sub: "cards" });
    await screen.findByRole("button", { name: "Add a card" });
    const before = calls("/api/churning").length;
    await rerender({ sub: "benefits" });
    expect(await screen.findByText("Worth a year")).toBeInTheDocument();
    expect(calls("/api/churning").length).toBe(before);   // the same data, not fetched again
  });

  it("shows 0/24 with a prompt when there are no people yet", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning({ people: [], owners: [], cards: [card()] }))) as never);
    render(Churning, { sub: "cards" });
    expect(await screen.findByText("0/24")).toBeInTheDocument();
    expect(screen.getByText("Add cards you’ve opened in the last 24 months")).toBeInTheDocument();
  });

  it("flags a person who is over 5/24 in the stat strip", async () => {
    const five24 = { count: 6, under: false, under_on: "2027-01-10", next_fall_off: "2027-01-10" };
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning({ people: ["Alex"], cards: [card()], five24: { Alex: five24 } } as never))) as never);
    render(Churning, { sub: "cards" });
    expect(await screen.findByText("6/24")).toHaveClass("text-[var(--warning)]");
  });

  it("puts the tabs right under the tiles, and Upcoming, Planned, Best card and Rewards in the Overview tab", async () => {
    const full = churning({
      cards: [card()], wishlist: [wish()], rewards: { Alex: { currencies: [{ currency: "c1", name: "Capital One miles", earned: 100, bonuses: 0, balance: null, cents: 1.4, value: 1.4, balance_value: null }], value: 1.4, balance_value: 0 } },
      upcoming: [{ date: "2026-10-20", kind: "plan", card_id: 1, owner: "Alex", title: "Downgrade Venture X", detail: "", warn: false }],
    } as never);
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : full)) as never);
    render(Churning, { sub: "" });
    const tabs = await screen.findByRole("navigation", { name: "Churning sections" });
    const after = (a: Element, b: Element) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    expect(after(document.querySelector("dl")!, tabs)).toBe(true);   // the tiles, then the tabs
    expect(within(tabs).getAllByRole("link").map((a) => a.textContent!.trim())).toEqual(["Overview", "Cards", "Benefits", "Bank bonuses"]);
    expect(within(tabs).getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
    const heads = ["Upcoming", "Planned", "Best card for…", "Rewards"].map((t) => screen.getAllByText(t, { selector: "[data-slot=card-title]" })[0]);
    expect(after(tabs, heads[0])).toBe(true);
    for (let i = 1; i < heads.length; i++) expect(after(heads[i - 1], heads[i])).toBe(true);
    expect(screen.queryByText("Cards", { selector: "[data-slot=card-title]" })).toBeNull();   // those are in the Cards tab
  });

  it("routes #churning to the Overview, #churning/cards to the cards, and keeps the data when switching", async () => {
    serve({ cards: [card()], wishlist: [wish()] });
    const { rerender } = render(Churning, { sub: "" });
    await screen.findByText("Planned", { selector: "[data-slot=card-title]" });
    expect(screen.queryByRole("button", { name: "Add a card" })).toBeNull();
    const links = within(screen.getByRole("navigation", { name: "Churning sections" })).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["#churning", "#churning/cards", "#churning/benefits", "#churning/bank"]);
    await rerender({ sub: "cards" });
    expect(await screen.findByRole("button", { name: "Add a card" })).toBeInTheDocument();
    expect(screen.queryByText("Planned", { selector: "[data-slot=card-title]" })).toBeNull();
    expect(screen.getByRole("link", { name: "Cards" })).toHaveAttribute("aria-current", "page");
    await rerender({ sub: "something-else" });   // an unknown tab is the Overview
    expect(await screen.findByText("Planned", { selector: "[data-slot=card-title]" })).toBeInTheDocument();
  });

  it("has no Overview tab, and opens the cards, when there is nothing at all", async () => {
    serve({});
    render(Churning, { sub: "" });
    expect(await screen.findByRole("button", { name: "Add a card" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Overview" })).toBeNull();
    expect(screen.getByRole("link", { name: "Cards" })).toHaveAttribute("aria-current", "page");
  });

  it("shows only Found on your accounts and one Add a card button when there is nothing at all", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/churning/found" ? { drafts: [found()], dismissed: [] } : path.startsWith("/api/churning/best") ? { cards: [] } : churning())) as never);
    render(Churning, { sub: "cards" });
    await screen.findByTestId("found-cards");
    expect(screen.queryByTestId("getting-started")).toBeNull();
    expect(screen.queryByText("5/24", { selector: "dt span" })).toBeNull();   // no stat strip of zeros
    for (const t of ["Upcoming", "Planned", "Best card for…", "Rewards"]) expect(screen.queryByText(t)).toBeNull();
    expect(screen.getAllByRole("button", { name: "Add a card" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Add a card you’ve opened" })).toBeNull();
    expect(calls(/best\?/)).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Add a card" }));
    expect(await screen.findByPlaceholderText("e.g. Sapphire Preferred")).toBeInTheDocument();   // the new-card form opens
  });

  it("collapses empty sections to one line and hides Best card, and Rewards is a line, when there is one card and little else", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning({ cards: [card({ status: "closed" })] }))) as never);
    render(Churning, { sub: "" });
    await screen.findByText("nothing in the next six months");
    expect(screen.queryByTestId("getting-started")).toBeNull();
    expect(screen.getByRole("button", { name: "Plan a card or bonus" })).toBeInTheDocument();
    expect(screen.queryByText("Best card for…")).toBeNull();
    expect(screen.getByText("no points tracked yet")).toBeInTheDocument();
    expect(document.querySelectorAll("[data-slot=card-title]")).toHaveLength(0);   // all one-liners
  });

  it("keeps Best card, and Rewards once a card has earned something, when there is data", async () => {
    const rewards = { Alex: { currencies: [{ currency: "c1", name: "Capital One miles", earned: 0, bonuses: 0, balance: 5000, as_of: "2026-09-01", cents: 1.4, value: 0, balance_value: 70 }], value: 0, balance_value: 70 } };
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning({ cards: [card()], rewards } as never))) as never);
    render(Churning, { sub: "" });
    await screen.findByText("Best card for…");
    expect(screen.getByText("Rewards", { selector: "[data-slot=card-title]" })).toBeInTheDocument();
  });

  it("keeps Rewards as one line with an Add a balance action when nothing is tracked, and the action opens the balance entry", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning({ cards: [card()] }))) as never);
    render(Churning, { sub: "" });
    await screen.findByText("no points tracked yet");
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

  it("groups the bank bonus form into collapsed sections, each saying what's in it", async () => {
    serve({ cards: [card()], bank: [bankBonus({ dd_total: 500, deadline_days: 90, monthly_fee: 15 })] });
    render(Churning, { sub: "bank" });
    await userEvent.click(await screen.findByRole("button", { name: "Edit Chase" }));
    for (const id of ["requirements", "fees", "received"]) expect(screen.getByTestId(`section-${id}`)).not.toHaveAttribute("open");
    expect(screen.getByTestId("summary-requirements")).toHaveTextContent("$500 in direct deposits · 90 days");
    expect(screen.getByTestId("summary-fees")).toHaveTextContent("$15/month fee");
    expect(screen.getByTestId("summary-received")).toHaveTextContent("Nothing added");
  });

  it("groups the plan form into What you expect and Timing", async () => {
    serve({ cards: [card()], wishlist: [wish({ annual_fee: 95 })] });
    render(Churning, { sub: "" });
    await userEvent.click(await screen.findByRole("button", { name: "More actions for Sapphire Preferred" }));
    await userEvent.click(screen.getByRole("button", { name: "Edit Sapphire Preferred" }));
    expect(screen.getByTestId("summary-expect")).toHaveTextContent("$95 fee");
    expect(screen.getByTestId("summary-timing")).toHaveTextContent("Nothing added");
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
      render(Churning, { sub: "cards" });
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
      render(Churning, { sub: "cards" });
      await screen.findByRole("button", { name: "Add a card" });
      expect(screen.queryByTestId("found-cards")).toBeNull();
    });

    it("opens the add-card form pre-filled for review, without saving", async () => {
      serve();
      render(Churning, { sub: "cards" });
      await userEvent.click(await screen.findByRole("button", { name: "Add Sapphire Reserve" }));
      expect(await screen.findByLabelText(/^Card/)).toHaveValue("Sapphire Reserve");
      expect(screen.getByLabelText(/^Opened \(on or before\)/)).toHaveValue("2024-03-02");
      expect(calls("/api/churning/cards")).toHaveLength(0);
      await userEvent.click(screen.getByRole("button", { name: "Add" }));
      await waitFor(() => expect(calls("/api/churning/cards")).toHaveLength(1));
      expect(bodyOf(calls("/api/churning/cards")[0])).toMatchObject({ account_id: "acct-1", product: "Sapphire Reserve" });
    });

    it("dismisses a draft, and brings it back", async () => {
      serve();
      render(Churning, { sub: "cards" });
      await userEvent.click(await screen.findByRole("button", { name: "Dismiss Sapphire Reserve" }));
      await waitFor(() => expect(calls("/api/churning/found/acct-1/dismiss")).toHaveLength(1));
      await waitFor(() => expect(screen.queryByRole("button", { name: "Add Sapphire Reserve" })).toBeNull());
      const section = screen.getByTestId("found-cards");
      await userEvent.click(within(section).getByRole("button", { name: "Show" }));
      await userEvent.click(within(section).getByRole("button", { name: "Bring back Chase Sapphire Reserve (8814)" }));
      await waitFor(() => expect(calls("/api/churning/found/acct-1/undismiss")).toHaveLength(1));
      expect(await screen.findByRole("button", { name: "Add Sapphire Reserve" })).toBeInTheDocument();
    });

    it("hides the other person's drafts when one person is picked", async () => {
      serve();
      render(Churning, { sub: "cards" });
      await screen.findByTestId("found-cards");
      await userEvent.click(screen.getByRole("radio", { name: "Alex" }));
      expect(screen.queryByRole("button", { name: "Add CREDIT CARD (3392)" })).toBeNull();
      expect(screen.getByRole("button", { name: "Add Sapphire Reserve" })).toBeInTheDocument();
    });
  });

  it("tucks paid and closed bank bonuses behind an N paid or closed · Show line and keeps the active ones shown", async () => {
    serve({ cards: [card()], bank: [
      bankBonus({ id: 1, bank: "Chase" }),
      bankBonus({ id: 2, bank: "Citi", state: "received", status: "received", received_on: "2026-03-01" }),
      bankBonus({ id: 3, bank: "Ally", state: "closed", status: "closed" }),
    ] });
    render(Churning, { sub: "bank" });
    const group = await screen.findByTestId("done-bank");
    expect(within(group).getByText(/2 paid or closed/)).toBeInTheDocument();
    expect(screen.getByText("Chase checking")).toBeInTheDocument();
    expect(screen.queryByText("Citi checking")).toBeNull();
    expect(screen.queryByText("Ally checking")).toBeNull();
    await userEvent.click(within(group).getByRole("button", { name: "Show" }));
    expect(screen.getByText("Citi checking")).toBeInTheDocument();
    expect(screen.getByText("Ally checking")).toBeInTheDocument();
  });

  it("folds closed cards behind an N closed · Show line instead of a checkbox", async () => {
    serve({ cards: [card(), card({ id: 2, product: "Old Gold", status: "closed", closed_on: "2026-01-01" })] });
    render(Churning, { sub: "cards" });
    await screen.findByText("Venture X");
    expect(screen.queryByRole("checkbox", { name: "Show closed" })).toBeNull();
    expect(screen.queryByText("Old Gold")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(screen.getByText("Old Gold")).toBeInTheDocument();
  });

  it("no longer explains the banks' rules on the page, and tabs carry no counts", async () => {
    serve({ cards: [card()] });
    render(Churning, { sub: "cards" });
    await screen.findByText("Venture X");
    expect(screen.queryByText(/About "Bonus again"/)).toBeNull();
    expect(screen.getByRole("link", { name: "Cards" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Bank bonuses" })).toBeInTheDocument();
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
    render(Churning, { sub: "cards" });
    await screen.findByRole("button", { name: "Add a card" });
    expect(screen.queryByText(/^Earns /)).toBeNull();
  });

  it("opens a card you just applied for in the Cards tab (#churning/cards), where its form is", async () => {
    vi.mocked(api).mockImplementation((async (path: string, opts?: { method?: string }) => {
      if (path.endsWith("/applied")) return { kind: "card", id: 1, wish_id: 1 };
      if (opts?.method === "POST") return { ok: true };
      return path.startsWith("/api/churning/best") ? { cards: [] } : churning({ cards: [card()], wishlist: [wish()] });
    }) as never);
    location.hash = "#churning";
    render(Churning, { sub: "" });
    await userEvent.click(await screen.findByRole("button", { name: "I applied for Sapphire Preferred" }));
    await waitFor(() => expect(location.hash).toBe("#churning/cards"));
  });

  describe("loading and errors", () => {
    it("shows the page's shape, with its title, while loading", async () => {
      vi.mocked(api).mockReturnValue(new Promise(() => {}) as never);
      render(Churning, { sub: "" });
      expect(screen.getByRole("heading", { name: "Churning" })).toBeInTheDocument();
      expect(screen.getByLabelText("Loading Churning")).toHaveAttribute("aria-busy", "true");
    });

    it("says in plain words that it couldn't load, never the raw error, and Try again loads the page's data again", async () => {
      let fail = true;
      vi.mocked(api).mockImplementation((async (path: string) => {
        if (fail) throw new Error("HTTP 500: Traceback in /api/churning");
        return path.startsWith("/api/churning/best") ? { cards: [] } : churning({ cards: [card()] });
      }) as never);
      render(Churning, { sub: "cards" });
      expect(await screen.findByText(/Couldn’t load Churning/)).toBeInTheDocument();
      expect(screen.queryByText(/Traceback|HTTP 500|Something went wrong/)).toBeNull();
      expect(screen.queryByLabelText("Loading Churning")).toBeNull();
      const before = { page: calls("/api/churning").length, found: calls("/api/churning/found").length };
      fail = false;
      await userEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(await screen.findByRole("button", { name: "Add a card" })).toBeInTheDocument();
      expect(calls("/api/churning").length).toBe(before.page + 1);
      expect(calls("/api/churning/found").length).toBe(before.found + 1);   // the found cards too
    });

    it("says so when a reload fails, keeping what was shown but not letting it pass as current", async () => {
      let failing = false;
      vi.mocked(api).mockImplementation((async (path: string, opts?: { method?: string }) => {
        if (path === "/api/churning" && failing) throw new Error("offline");
        if (opts?.method === "POST") return { ok: true };
        return path.startsWith("/api/churning/best") ? { cards: [] } : churning({ cards: [card()], wishlist: [wish()] });
      }) as never);
      render(Churning, { sub: "" });
      await userEvent.click(await screen.findByRole("button", { name: "More actions for Sapphire Preferred" }));
      failing = true;
      await userEvent.click(screen.getByRole("button", { name: "Drop Sapphire Preferred" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t refresh this page, so what’s shown may be out of date.");
      expect(screen.getByText("Planned", { selector: "[data-slot=card-title]" })).toBeInTheDocument();   // still there, with the warning above it
      failing = false;
      await userEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "Try again" }));
      await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    });

    it("says in one muted line that the found cards couldn't be loaded, and tries again from there", async () => {
      let failing = true;
      vi.mocked(api).mockImplementation((async (path: string) => {
        if (path === "/api/churning/found") { if (failing) throw new Error("boom"); return { drafts: [found()], dismissed: [] }; }
        return path.startsWith("/api/churning/best") ? { cards: [] } : churning({ cards: [card()] });
      }) as never);
      render(Churning, { sub: "cards" });
      const note = await screen.findByTestId("found-failed");
      expect(note).toHaveTextContent("Couldn’t check your accounts for cards to add.");
      expect(note).toHaveClass("text-muted-foreground");
      expect(screen.getByRole("button", { name: "Add a card" })).toBeInTheDocument();   // the page itself is fine
      failing = false;
      await userEvent.click(within(note).getByRole("button", { name: "Try again" }));
      expect(await screen.findByTestId("found-cards")).toBeInTheDocument();
      expect(screen.queryByTestId("found-failed")).toBeNull();
    });
  });

  describe("the person switcher", () => {
    it("says Both for two people and Everyone for three or more", async () => {
      serve({ cards: [card()], people: ["Alex", "Sam"], owners: ["Alex", "Sam"] });
      const { unmount } = render(Churning, { sub: "cards" });
      await screen.findByRole("radio", { name: "Alex" });
      expect(screen.getByRole("radio", { name: "Both" })).toBeInTheDocument();
      unmount();
      serve({ cards: [card()], people: ["Alex", "Sam", "Kim"], owners: ["Alex", "Sam", "Kim"] });
      render(Churning, { sub: "cards" });
      await screen.findByRole("radio", { name: "Kim" });
      expect(screen.getByRole("radio", { name: "Everyone" })).toBeInTheDocument();
      expect(screen.queryByRole("radio", { name: "Both" })).toBeNull();
    });
  });

  describe("the annual fee tile", () => {
    const due = (over: Partial<ChurnCard>) => card({ fee_due: "2026-10-20", annual_fee: 95, ...over });
    it("counts the fees within 30 days, as its label says, the window the rows turn amber at", async () => {
      serve({ cards: [due({ id: 1, fee_due: "2026-10-28" }), due({ id: 2, fee_due: "2026-11-15" })] });   // today is 2026-09-30: 28 and 46 days
      render(Churning, { sub: "cards" });
      const label = await screen.findByText("Annual fees within 30 days", { selector: "dt span" });
      const tile = label.closest<HTMLElement>("div")!;
      expect(within(tile).getByText("$95")).toBeInTheDocument();
      expect(within(tile).getByText("1 card")).toBeInTheDocument();
      expect(screen.queryByText(/90 days/)).toBeNull();
    });

    it("shows the fee total and count, and never asks keep, downgrade or close", async () => {
      serve({ cards: [due({ id: 1, plan: "keep" }), due({ id: 2, plan: "undecided" })] });
      render(Churning, { sub: "cards" });
      expect(await screen.findByText("2 cards")).toBeInTheDocument();
      expect(screen.queryByText(/keep, downgrade or close/)).toBeNull();
      expect(screen.queryByText(/each with a plan/)).toBeNull();
    });
  });
});
