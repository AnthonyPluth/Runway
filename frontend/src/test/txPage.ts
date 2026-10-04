import { vi } from "vitest";
import { api } from "$lib/api";
import { app, route } from "$lib/app.svelte";
import { categories } from "$lib/categories.svelte";
import { txFilters, txShow } from "$lib/filters.svelte";
import type { Tx } from "$lib/components/transactions/types";
import { endBatch } from "$lib/undoBatch";
import { toast } from "svelte-sonner";
import { category, tx } from "./fixtures";

export const none = () => ({ q: "", account: "", category: "", from: "", to: "", min: "", max: "", kind: "" as const, scope: "" });
export const summary = () => document.querySelector("[data-summary]");
export const accounts = [{ id: "a1", name: "Checking", kind: "checking" }, { id: "inv", name: "Brokerage", kind: "investment" }];
export const rows = (): Tx[] => [tx({ id: "a", payee: "Alpha", category: "Coffee" }), tx({ id: "b", payee: "Bravo", category: "Groceries" })];
type Handler = (path: string, opts?: { method?: string; body?: unknown }) => unknown;
export const serve = (list: Tx[] = rows(), total = list.length, extra: Handler = () => undefined) =>
  vi.mocked(api).mockImplementation((async (path: string, opts?: { method?: string; body?: unknown }) => {
    const custom = extra(path, opts);
    if (custom !== undefined) return custom;
    if (path === "/api/categories") return [category("Coffee"), category("Groceries")];
    if (path === "/api/accounts") return accounts;
    if (path === "/api/recurring") return [];
    if (path === "/api/state") return { connected: true, review_count: 3 };
    if (path.startsWith("/api/overview")) return { events: [] };
    if (path.startsWith("/api/transactions?")) return { items: list, total, sum: list.reduce((n, t) => n + t.amount, 0) };
    return {};
  }) as never);
export const lastList = () => vi.mocked(api).mock.calls.map((c) => c[0] as string).filter((p) => p.startsWith("/api/transactions?") && !p.includes("ignored=only")).at(-1)!;

export function resetTxPage() {
  endBatch();
  vi.mocked(api).mockReset();
  vi.mocked(toast).mockClear(); vi.mocked(toast.success).mockClear(); vi.mocked(toast.error).mockClear();
  app.state = { connected: true, review_count: 3 };
  categories.list = [];
  Object.assign(txFilters.transactions, none());
  Object.assign(txFilters.review, none());
  txShow.ignored = false;
  route.query = ""; route.page = "transactions";
  history.replaceState(null, "", "/#transactions");
}
