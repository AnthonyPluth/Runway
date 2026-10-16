// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));

import { api } from "$lib/api";
import { txFilters } from "$lib/filters.svelte";
import type { Tx } from "$lib/components/transactions/types";
import { toast } from "svelte-sonner";
import { tx } from "../test/fixtures";
import { lastList, resetTxPage, serve } from "../test/txPage";
import Transactions from "./Transactions.svelte";

beforeEach(resetTxPage);

const waiting = (): Tx[] => [
  tx({ id: "a", payee: "Alpha", category: "Coffee", needs_review: 1, category_source: "ai", confidence: 0.6, posted: "2026-03-10" }),
  tx({ id: "b", payee: "Bravo", category: "Groceries", needs_review: 1, category_source: "ai", confidence: 0.6, posted: "2026-03-09" }),
];
const wasOf = (id: string) => [{ id, category: "Coffee", category_source: "ai", confidence: 0.6, needs_review: 1, payee: "Alpha", is_split: 0 }];
/** The list and the review queue both serve `list`, unless the queue is `queue`. */
const open = (list: Tx[], queue: Tx[] = list) => serve(list, list.length, (path, o) => {
  if (path.includes("review=1") && path.includes("limit=20")) return { items: queue, total: queue.length };
  if (path.endsWith("/accept") && o?.method === "POST") return { ok: true, was: wasOf(path.split("/")[3]) };
  if (path.endsWith("/category") && o?.method === "POST") return { also_updated: 0, offer_rule: null, was: wasOf(path.split("/")[3]) };
  return undefined;
});
const card = () => document.querySelector<HTMLElement>("[data-review-card]");

describe("Transactions: the review card", () => {
  it("shows only when something is waiting for review", async () => {
    open([tx({ id: "z", payee: "Zulu" })], []);
    render(Transactions);
    await screen.findByText("Zulu");
    expect(card()).toBeNull();
  });

  it("shows the first one, 'To review · 1 of N', with its suggestion, an alternative and Other…", async () => {
    open(waiting());
    render(Transactions);
    await waitFor(() => expect(card()).not.toBeNull());
    const c = within(card()!);
    expect(c.getByText("To review · 1 of 2")).toBeInTheDocument();
    expect(c.getByRole("link", { name: "See all" })).toHaveAttribute("href", "#review");
    expect(c.getByText("Alpha")).toBeInTheDocument();
    expect(c.getByRole("button", { name: /Coffee/ })).toHaveAttribute("aria-pressed", "true");
    expect(c.getByRole("button", { name: /Groceries/ })).toHaveAttribute("aria-pressed", "false");
    expect(c.getByRole("button", { name: "Skip" })).toBeEnabled();
  });

  it("is not on the To review tab, which has its own list", async () => {
    open(waiting());
    render(Transactions, { page: "review" });
    await screen.findByText("Alpha");
    expect(card()).toBeNull();
  });

  it("Accept keeps the suggested category through the accept action and moves to the next", async () => {
    open(waiting());
    render(Transactions);
    await waitFor(() => expect(card()).not.toBeNull());
    await userEvent.click(within(card()!).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/transactions/a/accept", { method: "POST" }));
    await waitFor(() => expect(within(card()!).getByText("To review · 1 of 1")).toBeInTheDocument());
    expect(within(card()!).getByText("Bravo")).toBeInTheDocument();
    expect(toast).toHaveBeenLastCalledWith("Accepted Coffee", expect.objectContaining({ description: "Alpha" }));
  });

  it("Accept applies the category picked instead, through the category save, and moves on", async () => {
    open(waiting());
    render(Transactions);
    await waitFor(() => expect(card()).not.toBeNull());
    await userEvent.click(within(card()!).getByRole("button", { name: /Groceries/ }));
    await userEvent.click(within(card()!).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/transactions/a/category", { method: "POST", body: { category: "Groceries" } }));
    expect(api).not.toHaveBeenCalledWith("/api/transactions/a/accept", expect.anything());
    await waitFor(() => expect(within(card()!).getByText("Bravo")).toBeInTheDocument());
  });

  it("Skip moves to the next without changing anything, and comes round again", async () => {
    open(waiting());
    render(Transactions);
    await waitFor(() => expect(card()).not.toBeNull());
    const before = vi.mocked(api).mock.calls.length;
    await userEvent.click(within(card()!).getByRole("button", { name: "Skip" }));
    expect(within(card()!).getByText("To review · 2 of 2")).toBeInTheDocument();
    expect(within(card()!).getByText("Bravo")).toBeInTheDocument();
    await userEvent.click(within(card()!).getByRole("button", { name: "Skip" }));
    expect(within(card()!).getByText("Alpha")).toBeInTheDocument();
    expect(vi.mocked(api).mock.calls.slice(before).filter((c) => (c[1] as { method?: string } | undefined)?.method === "POST")).toEqual([]);
  });

  it("Undo puts the accepted transaction back, as the list's accept does", async () => {
    open(waiting());
    render(Transactions);
    await waitFor(() => expect(card()).not.toBeNull());
    await userEvent.click(within(card()!).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(toast).toHaveBeenCalled());
    await (vi.mocked(toast).mock.calls.at(-1)![1] as unknown as { action: { onClick: () => Promise<void> } }).action.onClick();
    expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { restore: wasOf("a") } });
  });
});

describe("Transactions: the filter chips", () => {
  it("search is a full-width field, and the chips are All, To review · N, Pending and Accounts", async () => {
    open([tx({ id: "z", payee: "Zulu" })], []);
    render(Transactions);
    await screen.findByText("Zulu");
    expect(screen.getByRole("searchbox", { name: "Search transactions" })).toHaveClass("w-full");
    const chips = within(document.querySelector<HTMLElement>("[data-chips]")!);
    expect(chips.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    expect(chips.getByRole("link", { name: "To review · 3" })).toHaveAttribute("href", "#review");
    expect(chips.getByRole("button", { name: "Pending" })).toBeInTheDocument();
    expect(chips.getByText("Accounts")).toBeInTheDocument();
  });

  it("Accounts sets the existing account filter, and All clears the filters", async () => {
    open([tx({ id: "z", payee: "Zulu" })], []);
    render(Transactions);
    await screen.findByText("Zulu");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Account" }), "a1");
    expect(txFilters.transactions.account).toBe("a1");
    await waitFor(() => expect(lastList()).toContain("account=a1"));
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(screen.getByRole("button", { name: "All" }));
    expect(txFilters.transactions.account).toBe("");
    await waitFor(() => expect(lastList()).not.toContain("account="));
  });

  it("Pending shows only the pending rows loaded", async () => {
    open([tx({ id: "p", payee: "Pend", pending: 1 }), tx({ id: "q", payee: "Posted" })], []);
    render(Transactions);
    await screen.findByText("Posted");
    await userEvent.click(screen.getByRole("button", { name: "Pending" }));
    expect(screen.queryByText("Posted")).not.toBeInTheDocument();
    expect(screen.getByText("Pend")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "All" }));
    expect(await screen.findByText("Posted")).toBeInTheDocument();
  });

  it("search sets the existing q filter", async () => {
    open([tx({ id: "z", payee: "Zulu" })], []);
    render(Transactions);
    await screen.findByText("Zulu");
    await userEvent.type(screen.getByRole("searchbox", { name: "Search transactions" }), "zu");
    expect(txFilters.transactions.q).toBe("zu");
  });
});

describe("Transactions: the card follows the list, and the queue past 20", () => {
  const many = (n: number): Tx[] => Array.from({ length: n }, (_, i) => tx({ id: `q${i}`, payee: `Merchant ${i}`, category: "Coffee", needs_review: 1, posted: "2026-03-10" }));
  /** The server's queue: a decided one leaves it; `limit` and `offset` are honoured. */
  const server = (all: Tx[], extra: (path: string, o?: { method?: string }) => unknown = () => undefined) => {
    let left = [...all];
    const asked: string[] = [];
    serve(all, all.length, (path, o) => {
      const custom = extra(path, o);
      if (custom !== undefined) return custom;
      if (path.includes("review=1") && path.includes("limit=20")) {
        asked.push(path);
        const p = new URLSearchParams(path.split("?")[1]);
        return { items: left.slice(Number(p.get("offset")), Number(p.get("offset")) + 20), total: left.length };
      }
      if (path.endsWith("/accept") && o?.method === "POST") { const id = path.split("/")[3]; left = left.filter((t) => t.id !== id); return { ok: true, was: wasOf(id) }; }
      return undefined;
    });
    return asked;
  };

  it("Skip at the last one loaded asks for the next batch instead of wrapping", async () => {
    const asked = server(many(22));
    render(Transactions);
    await waitFor(() => expect(card()).not.toBeNull());
    for (let i = 0; i < 19; i++) await userEvent.click(within(card()!).getByRole("button", { name: "Skip" }));
    expect(within(card()!).getByText("Merchant 19")).toBeInTheDocument();
    await userEvent.click(within(card()!).getByRole("button", { name: "Skip" }));
    await waitFor(() => expect(within(card()!).getByText("Merchant 20")).toBeInTheDocument());
    expect(within(card()!).getByText("To review · 21 of 22")).toBeInTheDocument();
    expect(asked.at(-1)).toContain("offset=20");
  });

  it("keeps the card when what's loaded is all decided but more is waiting", async () => {
    const all = many(2);
    let first = true;
    // The first answer holds one of the two (as if only that much had loaded).
    const asked = server(all, (path) => {
      if (first && path.includes("review=1") && path.includes("limit=20")) { first = false; return { items: [all[0]], total: 2 }; }
      return undefined;
    });
    render(Transactions);
    await waitFor(() => expect(card()).not.toBeNull());
    await userEvent.click(within(card()!).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(within(card()!).getByText("Merchant 1")).toBeInTheDocument());
    expect(asked.length).toBeGreaterThan(0);
  });

  it("a row accepted in the list takes the card's copy away too", async () => {
    const all = many(2);
    server(all);
    render(Transactions);
    await waitFor(() => expect(card()).not.toBeNull());
    await userEvent.click(screen.getAllByRole("button", { name: /^Accept Coffee for Merchant 0/ })[0]);
    await waitFor(() => expect(within(card()!).queryByText("Merchant 0")).not.toBeInTheDocument());
    expect(within(card()!).getByText("Merchant 1")).toBeInTheDocument();
  });

  it("ignores a slow older answer that arrives after a newer one", async () => {
    const all = many(2);
    let slow!: (v: unknown) => void;
    let calls = 0;
    serve(all, 2, (path, o) => {
      if (path.includes("review=1") && path.includes("limit=20")) {
        // The first answer is held back; the next (after the accept) says only Merchant 1 is left.
        return ++calls === 1 ? new Promise((r) => { slow = r; }) : { items: [all[1]], total: 1 };
      }
      if (path.endsWith("/accept") && o?.method === "POST") return { ok: true, was: wasOf(path.split("/")[3]) };
      return undefined;
    });
    render(Transactions);
    await screen.findByText("Merchant 0");
    await userEvent.click(screen.getByRole("button", { name: /^Accept Coffee for Merchant 0/ }));
    await waitFor(() => expect(card()).not.toBeNull());
    expect(within(card()!).getByText("To review · 1 of 1")).toBeInTheDocument();
    slow({ items: all, total: 2 });   // the old answer, with Merchant 0 still waiting
    await new Promise((r) => setTimeout(r));
    expect(within(card()!).getByText("To review · 1 of 1")).toBeInTheDocument();
    expect(within(card()!).queryByText("Merchant 0")).not.toBeInTheDocument();
  });

  it("a decision on the card keeps the fresh queue answer that follows it (its total included)", async () => {
    const all = many(2);
    let calls = 0;
    let release!: (v: unknown) => void;
    // The refresh the accept starts is slow: the card has already moved on (by its own count) when it lands.
    serve(all, 2, (path, o) => {
      if (path.includes("review=1") && path.includes("limit=20")) return ++calls === 1 ? { items: all, total: 2 } : new Promise((r) => { release = r; });
      if (path.endsWith("/accept") && o?.method === "POST") return { ok: true, was: wasOf(path.split("/")[3]) };
      return undefined;
    });
    render(Transactions);
    await waitFor(() => expect(card()).not.toBeNull());
    await userEvent.click(within(card()!).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(within(card()!).getByText("To review · 1 of 1")).toBeInTheDocument());
    release({ items: [all[1]], total: 5 });
    // The server's answer, not just the local count down, is what the card shows.
    await waitFor(() => expect(within(card()!).getByText("To review · 1 of 5")).toBeInTheDocument());
    expect(within(card()!).getByText("Merchant 1")).toBeInTheDocument();
  });

  it("loading the next batch waits for a whole-queue refresh in flight and doesn't make it stale", async () => {
    const all = many(22);
    let release!: (v: unknown) => void;
    let calls = 0;
    const asked: string[] = [];
    // After the first batch of 20, the next whole refresh is held back; the server's queue then has m0 decided.
    serve(all, 22, (path, o) => {
      if (path.includes("review=1") && path.includes("limit=20")) {
        asked.push(path);
        const rest = all.slice(1);
        if (++calls === 1) return { items: all.slice(0, 20), total: 22 };
        if (calls === 2) return new Promise((r) => { release = r; });
        const offset = Number(new URLSearchParams(path.split("?")[1]).get("offset"));
        return { items: rest.slice(offset, offset + 20), total: 21 };
      }
      if (path.endsWith("/accept") && o?.method === "POST") return { ok: true, was: wasOf(path.split("/")[3]) };
      return undefined;
    });
    render(Transactions);
    await waitFor(() => expect(card()).not.toBeNull());
    // A row in the list is accepted: the whole queue is asked for again (held back).
    await userEvent.click(screen.getByRole("button", { name: /^Accept Coffee for Merchant 0/ }));
    await waitFor(() => expect(calls).toBe(2));
    // At the end of the 20 loaded, Skip asks for more, but only once the refresh has landed.
    for (let i = 0; i < 19; i++) await userEvent.click(within(card()!).getByRole("button", { name: "Skip" }));
    const last = userEvent.click(within(card()!).getByRole("button", { name: "Skip" }));
    await new Promise((r) => setTimeout(r, 20));
    expect(calls).toBe(2);
    release({ items: all.slice(1, 21), total: 21 });
    await last;
    await waitFor(() => expect(calls).toBe(3));
    expect(asked.at(-1)).toContain("offset=20");
    await waitFor(() => expect(within(card()!).getByText("Merchant 21")).toBeInTheDocument());
    expect(within(card()!).queryByText("Merchant 0")).not.toBeInTheDocument();
  });

  it("calls a day Today and Yesterday by the host's day (from the server), not the browser's", async () => {
    serve([tx({ id: "x", payee: "Xray", posted: "2026-03-10" }), tx({ id: "y", payee: "Yank", posted: "2026-03-09" })], 2, (path) =>
      path.startsWith("/api/overview") ? { events: [], today: "2026-03-10" } : path.includes("limit=20&") ? { items: [], total: 0 } : undefined);
    render(Transactions);
    await screen.findByText("Xray");
    await waitFor(() => expect(screen.getByRole("region", { name: "Today" })).toBeInTheDocument());
    expect(screen.getByRole("region", { name: "Yesterday" })).toBeInTheDocument();
  });
});
