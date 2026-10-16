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
