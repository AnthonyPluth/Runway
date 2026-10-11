// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));

import { api } from "$lib/api";
import { categories } from "$lib/categories.svelte";
import { app } from "$lib/app.svelte";
import { txFilters } from "$lib/filters.svelte";
import type { Tx } from "$lib/components/transactions/types";
import { pickCategory } from "../test/pick";
import { toast } from "svelte-sonner";
import { category, tx } from "../test/fixtures";
import { lastList, resetTxPage, rows, serve } from "../test/txPage";
import Transactions from "./Transactions.svelte";

beforeEach(resetTxPage);

describe("Transactions page", () => {
  describe("To review", () => {
    it("asks for only the transactions to review, with the count as 'to go'", async () => {
      serve(rows(), 2);
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      expect(lastList()).toContain("review=1");
      expect(lastList()).not.toContain("ignored=");
      expect(screen.getByText("2 to go")).toBeInTheDocument();
      expect(api).not.toHaveBeenCalledWith(expect.stringContaining("/api/overview"));
    });

    it("removes a transaction from the list once it's categorized, counting down", async () => {
      serve(rows(), 2, (path, o) => (path.endsWith("/category") && o?.method === "POST" ? { also_updated: 0, offer_rule: null } : undefined));
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      await pickCategory(screen.getByRole("combobox", { name: /^Category for Alpha(:|$)/ }), "Groceries");
      await waitFor(() => expect(screen.queryByText("Alpha")).not.toBeInTheDocument());
      expect(screen.getByText("1 to go")).toBeInTheDocument();
    });

    const queue = (list: Tx[]) => {
      let open = list;
      serve(list, list.length, (path, o) => {
        if (path.startsWith("/api/transactions?")) return { items: open, total: open.length };
        if (path.endsWith("/category") && o?.method === "POST") {
          const id = path.split("/")[3];
          const was = open.filter((t) => t.id === id).map((t) => ({ id, category: t.category, category_source: "ai", confidence: 0.6, needs_review: 1, payee: t.payee, is_split: 0 }));
          open = open.filter((t) => t.id !== id);
          return { also_updated: 0, offer_rule: null, was };
        }
        if (path === "/api/transactions/bulk") { open = list; return { updated: 1 }; }
        return undefined;
      });
    };
    const undo = () => (vi.mocked(toast).mock.calls.at(-1)![1] as unknown as { action: { onClick: () => Promise<void> } }).action.onClick();

    it("undoes a category change: sends what it was and puts the transaction back in the queue", async () => {
      queue(rows());
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      await pickCategory(screen.getByRole("combobox", { name: /^Category for Alpha(:|$)/ }), "Groceries");
      await waitFor(() => expect(screen.queryByText("Alpha")).not.toBeInTheDocument());
      expect(toast).toHaveBeenLastCalledWith("Coffee → Groceries", expect.objectContaining({ description: "Alpha" }));
      await undo();
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { restore: [
        { id: "a", category: "Coffee", category_source: "ai", confidence: 0.6, needs_review: 1, payee: "Alpha", is_split: 0 }] } });
      expect(await screen.findByText("Alpha")).toBeInTheDocument();
      expect(toast).toHaveBeenLastCalledWith("Undone", undefined);
    });

    it("moves focus to the next row's category when one leaves the queue", async () => {
      queue(rows());
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      await pickCategory(screen.getByRole("combobox", { name: /^Category for Alpha(:|$)/ }), "Groceries");
      await waitFor(() => expect(screen.getByRole("combobox", { name: /^Category for Bravo(:|$)/ })).toHaveFocus());
    });

    it("moves focus to the row before when the last one is done, and to 'All caught up' when none are left", async () => {
      queue(rows());
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      await pickCategory(screen.getByRole("combobox", { name: /^Category for Bravo(:|$)/ }), "Coffee");
      await waitFor(() => expect(screen.getByRole("combobox", { name: /^Category for Alpha(:|$)/ })).toHaveFocus());
      await pickCategory(screen.getByRole("combobox", { name: /^Category for Alpha(:|$)/ }), "Groceries");
      await waitFor(() => expect(screen.getByText(/All caught up/)).toHaveFocus());
    });

    it("celebrates an empty queue, but not an empty search", async () => {
      serve([], 0);
      const { unmount } = render(Transactions, { page: "review" });
      expect(await screen.findByText(/All caught up/)).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "See all transactions" })).toHaveAttribute("href", "#transactions");
      unmount();
      txFilters.review.q = "zzz";
      render(Transactions, { page: "review" });
      expect(await screen.findByText("No transactions match these filters.")).toBeInTheDocument();
    });

    it("asks you to connect a bank before there's anything to review", () => {
      app.state = { connected: false };
      serve();
      render(Transactions, { page: "review" });
      expect(screen.getByText("Connect a bank to review transactions")).toBeInTheDocument();
    });

    it("says AI suggestions need a key, with the way to Settings, until one is set", async () => {
      serve(rows(), 2);
      const { unmount } = render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      expect(screen.queryByRole("button", { name: "Suggest categories" })).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Settings › Connections" })).toHaveAttribute("href", "#setup/connections");
      unmount();
      app.state = { connected: true, review_count: 3, has_api_key: true };
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      expect(screen.getByRole("button", { name: "Suggest categories" })).toBeEnabled();
    });

    it("leaves out the AI button when there's nothing to review, and takes it away once the last one is done", async () => {
      app.state = { connected: true, review_count: 0, has_api_key: true };
      serve([], 0);
      const { unmount } = render(Transactions, { page: "review" });
      expect(await screen.findByText(/All caught up/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Suggest categories" })).not.toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "Settings › Connections" })).not.toBeInTheDocument();
      unmount();
      app.state = { connected: true, review_count: 1, has_api_key: true };
      serve([tx({ id: "a", payee: "Alpha", category: null, needs_review: 1 })], 1);
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      expect(screen.getByRole("button", { name: "Suggest categories" })).toBeEnabled();
      await pickCategory(screen.getByRole("combobox", { name: /^Category for Alpha(:|$)/ }), "Groceries");
      await waitFor(() => expect(screen.queryByRole("button", { name: "Suggest categories" })).not.toBeInTheDocument());
    });

    const waiting = (): Tx[] => [tx({ id: "a", payee: "Alpha", category: "Coffee", needs_review: 1, category_source: "ai", confidence: 0.95 }),
      tx({ id: "b", payee: "Bravo", category: "Groceries", needs_review: 1, category_source: "rule", confidence: 0.6 }),
      tx({ id: "c", payee: "Charlie", category: null, needs_review: 1 })];
    const wasOf = (id: string) => [{ id, category: "Coffee", category_source: "ai", confidence: 0.95, needs_review: 1, payee: "Alpha", is_split: 0 }];

    it("takes a row out of the queue before the server answers, and puts it back where it was if saving fails", async () => {
      let fail!: (e: Error) => void;
      serve(rows(), 2, (path, o) => (path.endsWith("/category") && o?.method === "POST" ? new Promise((_, no) => { fail = no; }) : undefined));
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      await pickCategory(screen.getByRole("combobox", { name: /^Category for Alpha(:|$)/ }), "Groceries");
      expect(screen.queryByText("Alpha")).not.toBeInTheDocument();
      expect(screen.getByText("1 to go")).toBeInTheDocument();
      fail(new Error("Locked"));
      expect(await screen.findByText("Alpha")).toBeInTheDocument();
      expect(screen.getAllByRole("listitem").map((r) => r.dataset.tx)).toEqual(["a", "b"]);
      expect(screen.getByText("2 to go")).toBeInTheDocument();
      expect(toast.error).toHaveBeenCalledWith("Couldn’t save Alpha", { description: "Locked" });
    });

    it("accepts a row's category, whoever set it, without changing it; Undo puts it back", async () => {
      serve(waiting(), 3, (path) => (path.endsWith("/accept") ? { ok: true, was: wasOf("b") } : undefined));
      render(Transactions, { page: "review" });
      await screen.findByText("Bravo");
      await userEvent.click(screen.getByRole("button", { name: "Accept Groceries for Bravo" }));
      expect(api).toHaveBeenCalledWith("/api/transactions/b/accept", { method: "POST" });
      await waitFor(() => expect(screen.queryByText("Bravo")).not.toBeInTheDocument());
      expect(toast).toHaveBeenLastCalledWith("Accepted Groceries", expect.objectContaining({ description: "Bravo" }));
      await (vi.mocked(toast).mock.calls.at(-1)![1] as unknown as { action: { onClick: () => Promise<void> } }).action.onClick();
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { restore: wasOf("b") } });
    });

    it("folds changes made one after another into one toast that undoes them all", async () => {
      serve(waiting(), 3, (path) => (path.endsWith("/accept") ? { ok: true, was: wasOf(path.split("/")[3]) } : undefined));
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      await userEvent.click(screen.getByRole("button", { name: "Accept Coffee for Alpha" }));
      await userEvent.click(screen.getByRole("button", { name: "Accept Groceries for Bravo" }));
      await waitFor(() => expect(vi.mocked(toast).mock.calls.at(-1)![0]).toBe("2 changed"));
      vi.mocked(api).mockClear();
      await (vi.mocked(toast).mock.calls.at(-1)![1] as unknown as { action: { onClick: () => Promise<void> } }).action.onClick();
      expect(vi.mocked(api).mock.calls.filter((c) => c[0] === "/api/transactions/bulk")).toHaveLength(2);
    });

    it("accepts every row it's at least 90% sure of at once, and can undo it", async () => {
      serve(waiting(), 3, (path) => (path === "/api/transactions/bulk" ? { updated: 1, was: wasOf("a") } : undefined));
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      await userEvent.click(screen.getByRole("button", { name: /Accept all ≥ 90%/ }));
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { ids: ["a"], reviewed: true } });
      await waitFor(() => expect(screen.queryByText("Alpha")).not.toBeInTheDocument());
      expect(screen.getByText("Bravo")).toBeInTheDocument();
      expect(toast).toHaveBeenLastCalledWith("Accepted 1 transaction", expect.objectContaining({ action: expect.objectContaining({ label: "Undo" }) }));
    });

    it("asks first before accepting more than ten at once", async () => {
      const many = Array.from({ length: 11 }, (_, i) => tx({ id: `m${i}`, payee: `Shop ${i}`, category: "Coffee", needs_review: 1, confidence: 0.99 }));
      serve(many, 11);
      render(Transactions, { page: "review" });
      await screen.findByText("Shop 0");
      await userEvent.click(screen.getByRole("button", { name: /Accept all ≥ 90%/ }));
      expect(await screen.findByRole("dialog", { name: "Accept 11 transactions?" })).toBeInTheDocument();
      expect(api).not.toHaveBeenCalledWith("/api/transactions/bulk", expect.anything());
    });

    it("says 'Accept all suggestions' when there are no confidences to go by", async () => {
      serve([tx({ id: "a", payee: "Alpha", category: "Coffee", needs_review: 1, confidence: null })], 1);
      render(Transactions, { page: "review" });
      await screen.findByText("Alpha");
      expect(screen.getByRole("button", { name: /Accept all suggestions/ })).toBeInTheDocument();
    });

    describe("keys", () => {
      const rowOf = (id: string) => document.querySelector<HTMLElement>(`[data-tx="${id}"]`)!;
      it("moves a ring between rows with j and k, and lets go with Escape", async () => {
        serve(waiting(), 3);
        render(Transactions, { page: "review" });
        await screen.findByText("Alpha");
        expect(document.querySelector("[data-keys-hint]")).toHaveTextContent("j/k move · Enter accept · c category");
        await userEvent.keyboard("j");
        expect(rowOf("a")).toHaveAttribute("data-focused");
        await userEvent.keyboard("j{ArrowDown}");
        expect(rowOf("c")).toHaveAttribute("data-focused");
        await userEvent.keyboard("k");
        expect(rowOf("b")).toHaveAttribute("data-focused");
        expect(rowOf("b")).toHaveFocus();
        await userEvent.keyboard("{Escape}");
        expect(document.querySelector("[data-focused]")).toBeNull();
      });

      it("accepts with Enter, and opens the picker when there's nothing to accept", async () => {
        serve(waiting(), 3, (path) => (path.endsWith("/accept") ? { ok: true, was: wasOf("a") } : undefined));
        render(Transactions, { page: "review" });
        await screen.findByText("Alpha");
        await userEvent.keyboard("j{Enter}");
        expect(api).toHaveBeenCalledWith("/api/transactions/a/accept", { method: "POST" });
        await waitFor(() => expect(rowOf("b")).toHaveAttribute("data-focused"));
        await userEvent.keyboard("j");
        expect(rowOf("c")).toHaveAttribute("data-focused");
        await userEvent.keyboard("{Enter}");
        expect(await screen.findByRole("listbox")).toBeInTheDocument();
      });

      it("opens the picker with c, and marks Ignore with i or Transfer with t", async () => {
        serve(waiting(), 3, (path, o) => (path.endsWith("/category") && o?.method === "POST" ? { also_updated: 0, offer_rule: null, was: [] } : undefined));
        render(Transactions, { page: "review" });
        await screen.findByText("Alpha");
        categories.list.push(category("Ignore", { is_transfer: 1 }), category("Transfer", { is_transfer: 1 }));
        await userEvent.keyboard("jc");
        expect(await screen.findByRole("listbox")).toBeInTheDocument();
        await userEvent.keyboard("{Escape}");
        rowOf("a").focus();
        await userEvent.keyboard("i");
        expect(api).toHaveBeenCalledWith("/api/transactions/a/category", { method: "POST", body: { category: "Ignore" } });
        await waitFor(() => expect(rowOf("b")).toHaveFocus());
        await userEvent.keyboard("t");
        expect(api).toHaveBeenCalledWith("/api/transactions/b/category", { method: "POST", body: { category: "Transfer" } });
      });

      it("leaves the keys alone while you type in the search", async () => {
        serve(waiting(), 3);
        render(Transactions, { page: "review" });
        await screen.findByText("Alpha");
        await userEvent.type(screen.getByRole("searchbox", { name: "Search transactions" }), "jk");
        expect(document.querySelector("[data-focused]")).toBeNull();
      });
    });

    describe("group by merchant", () => {
      const same = (): Tx[] => [tx({ id: "a", payee: "Alpha", category: null, amount: -5 }), tx({ id: "b", payee: "Alpha", category: null, amount: -7 }),
        tx({ id: "c", payee: "Bravo", category: null })];
      it("shows one row per merchant with one picker for all of them, off until you turn it on", async () => {
        serve(same(), 3, (path) => (path === "/api/transactions/bulk" ? { updated: 2, was: [], offer_rule: { merchant: "Alpha", match: "alpha", also_updated: 0 } } : undefined));
        render(Transactions, { page: "review" });
        await screen.findAllByText("Alpha");
        expect(screen.getByRole("switch", { name: "Group by merchant" })).toHaveAttribute("aria-checked", "false");
        await userEvent.click(screen.getByRole("switch", { name: "Group by merchant" }));
        const list = screen.getByRole("list", { name: "Merchants to review" });
        expect(within(list).getAllByRole("listitem")).toHaveLength(2);
        expect(within(list).getByText("2 transactions · −$12.00")).toBeInTheDocument();
        await pickCategory(screen.getByRole("combobox", { name: /^Category for 2 transactions from Alpha(:|$)/ }), "Groceries");
        expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { ids: ["a", "b"], category: "Groceries" } });
        await waitFor(() => expect(within(list).getAllByRole("listitem")).toHaveLength(1));
        expect(toast).toHaveBeenLastCalledWith("Alpha → Groceries", expect.objectContaining({ action: expect.objectContaining({ label: "Always for Alpha" }) }));
      });

      it("isn't offered when the AI groups by merchant itself", async () => {
        app.state = { connected: true, review_count: 3, has_api_key: true };
        serve(same(), 3);
        render(Transactions, { page: "review" });
        await screen.findAllByText("Alpha");
        expect(screen.queryByRole("switch", { name: "Group by merchant" })).not.toBeInTheDocument();
      });
    });
  });
});
