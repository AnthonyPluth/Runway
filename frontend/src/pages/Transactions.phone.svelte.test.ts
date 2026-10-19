// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));

import { api } from "$lib/api";
import { txFilters } from "$lib/filters.svelte";
import { viewport } from "$lib/phone.svelte";
import { tx } from "../test/fixtures";
import { lastList, resetTxPage, serve } from "../test/txPage";
import Transactions from "./Transactions.svelte";

beforeEach(() => { resetTxPage(); viewport.phone = true; });
afterEach(() => { cleanup(); viewport.phone = false; });

const waiting = () => [
  tx({ id: "a", payee: "Alpha", category: "Coffee", needs_review: 1, category_source: "ai", confidence: 0.6 }),
  tx({ id: "b", payee: "Bravo", category: "Groceries", needs_review: 1, category_source: "ai", confidence: 0.6 }),
];
const bills = (n: number) => Array.from({ length: n }, (_, i) => ({
  date: `2099-03-${10 + i}`, name: `Bill ${i + 1}`, amount: -10, kind: "recurring", key: `k${i}`, balance_after: 100, account_id: "a1", account: "Checking",
}));
/** The list, the review queue and the forecast, as the page asks for them; `overview` can fail. */
const open = (list = [tx({ id: "z", payee: "Zulu" })], queue = [] as ReturnType<typeof waiting>, overview: () => unknown = () => ({ events: bills(3) })) =>
  serve(list, list.length, (path) => {
    if (path.includes("review=1") && path.includes("limit=20")) return { items: queue, total: queue.length };
    if (path.startsWith("/api/overview")) return overview();
    return undefined;
  });
const card = () => document.querySelector<HTMLElement>("[data-review-card]");

describe("Transactions on a phone: Upcoming", () => {
  it("is one 'Upcoming · N' line, collapsed, with the transactions in view", async () => {
    open();
    render(Transactions);
    await screen.findByText("Zulu");
    const toggle = await screen.findByRole("button", { name: "Show upcoming, 3" });
    expect(screen.getByText("Upcoming · 3")).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Bill 1")).not.toBeInTheDocument();
  });

  it("opens and closes with the button", async () => {
    open();
    render(Transactions);
    await userEvent.click(await screen.findByRole("button", { name: "Show upcoming, 3" }));
    expect(screen.getByRole("button", { name: "Hide upcoming, 3" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Bill 1")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Hide upcoming, 3" }));
    expect(screen.queryByText("Bill 1")).not.toBeInTheDocument();
  });

  it("keeps the 'couldn’t load' notice and its Retry in view while nothing is shown", async () => {
    open(undefined, undefined, () => { throw new Error("down"); });
    render(Transactions);
    const note = await screen.findByTestId("upcoming-failed");
    expect(within(note).getByRole("button", { name: "Retry" })).toBeVisible();
    expect(screen.queryByText(/^Upcoming · /)).not.toBeInTheDocument();
  });
});

describe("Transactions on a phone: filters", () => {
  it("puts category, dates and kind/amount behind one Filters button", async () => {
    open();
    render(Transactions);
    await screen.findByText("Zulu");
    const button = screen.getByRole("button", { name: "Filters" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("searchbox", { name: "Search transactions" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Filter transactions" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add" })).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Category" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /More filters/ })).not.toBeInTheDocument();
    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("combobox", { name: "Category" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Dates:/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "More filters" })).toBeInTheDocument();
  });

  it("counts the active filters, lists each with an ✕, and clearing restores the list", async () => {
    Object.assign(txFilters.transactions, { category: "Coffee", kind: "out", min: "5" });
    open();
    render(Transactions);
    await screen.findByText("Zulu");
    expect(screen.getByRole("button", { name: "Filters (3 on)" })).toHaveTextContent("3");
    for (const clear of ["Show every category", "Show every kind", "Any amount"]) expect(screen.getByRole("button", { name: clear })).toBeInTheDocument();
    expect(lastList()).toContain("category=Coffee");
    await userEvent.click(screen.getByRole("button", { name: "Show every category" }));
    await waitFor(() => expect(lastList()).not.toContain("category="));
    expect(screen.getByRole("button", { name: "Filters (2 on)" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Filters" })).toBeInTheDocument());
    expect(lastList()).not.toMatch(/kind=|min=|category=/);
  });

  it("keeps the desktop layout: the filters in the row, no Filters button", async () => {
    viewport.phone = false;
    open();
    render(Transactions);
    await screen.findByText("Zulu");
    expect(screen.queryByRole("button", { name: "Filters" })).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Category" })).toBeInTheDocument();
  });
});

describe("Transactions on a phone: simplified", () => {
  it("has no All, To review or Pending chips, keeps Accounts, and the review card still links to the Review tab", async () => {
    open(waiting(), waiting());
    render(Transactions);
    await screen.findByText("2 to review");
    const chips = within(document.querySelector<HTMLElement>("[data-chips]")!);
    expect(chips.getByText("Accounts")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pending" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "All" })).not.toBeInTheDocument();
    expect(chips.queryByRole("link", { name: /^To review/ })).not.toBeInTheDocument();   // the To review tab is not a chip
    expect(screen.getByRole("link", { name: "See all" })).toHaveAttribute("href", "#review");
    expect(screen.getByRole("button", { name: "Filters" })).toBeInTheDocument();
  });

  it("shows no trash button on a row, and Delete transaction is in the opened details", async () => {
    open();
    render(Transactions);
    await screen.findByText("Zulu");
    expect(screen.queryByRole("button", { name: /^Delete Zulu/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("Zulu"));
    expect(await screen.findByRole("button", { name: "Delete transaction" })).toBeInTheDocument();
  });
});

describe("Transactions on a phone: the review card", () => {
  it("starts as 'N to review' with Review next and See all, and no card", async () => {
    open(waiting(), waiting());
    render(Transactions);
    await screen.findByText("2 to review");
    expect(card()).toBeNull();
    expect(screen.getByRole("link", { name: "See all" })).toHaveAttribute("href", "#review");
    expect(screen.getByRole("button", { name: "Review next" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "Review next" })).not.toHaveAttribute("aria-controls");   // nothing to point at while collapsed
  });

  it("Review next reveals the existing card, and it decides as before", async () => {
    open(waiting(), waiting());
    render(Transactions);
    await userEvent.click(await screen.findByRole("button", { name: "Review next" }));
    expect(screen.getByRole("button", { name: "Review next" })).toHaveAttribute("aria-expanded", "true");
    expect(within(card()!).getByText("To review · 1 of 2")).toBeInTheDocument();
    await userEvent.click(within(card()!).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/transactions/a/accept", { method: "POST" }));
  });
});

describe("Transactions on a phone: the review bar", () => {
  type Observed = { cb: IntersectionObserverCallback; el: Element };
  let observed: Observed[] = [];
  beforeEach(() => {
    observed = [];
    vi.stubGlobal("IntersectionObserver", class {
      cb: IntersectionObserverCallback;
      constructor(cb: IntersectionObserverCallback) { this.cb = cb; }
      observe(el: Element) { observed.push({ cb: this.cb, el }); }
      disconnect() { /* the stub keeps nothing to release */ }
      unobserve() { /* nor to stop */ }
    });
  });
  afterEach(() => { vi.unstubAllGlobals(); });
  /** The card's observer reports it in or out of view. */
  const scrollCard = (visible: boolean) => {
    const o = observed.find((x) => x.el.hasAttribute("data-review-top"))!;
    o.cb([{ isIntersecting: visible, target: o.el } as IntersectionObserverEntry], {} as IntersectionObserver);
  };
  const bar = () => document.querySelector<HTMLElement>("[data-review-bar]");

  it("shows the counter, Skip and Accept once the card has scrolled away, and Accept there takes the item out", async () => {
    let queue = [waiting()[0]];
    serve([tx({ id: "z", payee: "Zulu" })], 1, (path, o) => {
      if (path.includes("review=1") && path.includes("limit=20")) return { items: queue, total: queue.length };
      if (path.endsWith("/accept") && o?.method === "POST") { queue = []; return { ok: true, was: [] }; }
      return undefined;
    });
    render(Transactions);
    await screen.findByText("1 to review");
    expect(bar()).toBeNull();
    await waitFor(() => expect(observed.some((x) => x.el.hasAttribute("data-review-top"))).toBe(true));
    scrollCard(false);
    await waitFor(() => expect(bar()).not.toBeNull());
    const b = within(bar()!);
    expect(b.getByText("1 of 1")).toBeInTheDocument();
    expect(b.getByRole("button", { name: "Skip" })).toBeDisabled();
    scrollCard(true);
    await waitFor(() => expect(bar()).toBeNull());
    scrollCard(false);
    await waitFor(() => expect(bar()).not.toBeNull());
    await userEvent.click(within(bar()!).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/transactions/a/accept", { method: "POST" }));
    await waitFor(() => expect(screen.queryByText("1 to review")).not.toBeInTheDocument());
    expect(bar()).toBeNull();
  });

  it("is not there with nothing to review", async () => {
    open();
    render(Transactions);
    await screen.findByText("Zulu");
    expect(bar()).toBeNull();
  });
});
