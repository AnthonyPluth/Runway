// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/categories.svelte", () => ({ loadCategories: vi.fn(async () => {}), categories: { list: [] }, catLabel: (x: string) => x, categoryGroups: () => [] }));

import { api } from "$lib/api";
import OrderDetail from "./OrderDetail.svelte";

beforeEach(() => { vi.mocked(api).mockReset(); });

describe("an order's link to the store", () => {
  it.each([["amazon", "amazon.com"], ["target", "target.com"], ["costco", "costco.com"]])("names the right site for %s", async (retailer, site) => {
    vi.mocked(api).mockResolvedValue({ id: "x", retailer, channel: "store", order_number: "1", placed: "2026-09-26", total: 10, items: [], charges: [],
      url: `https://www.${site}/orders` } as never);
    render(OrderDetail, { orderId: "x" });
    const link = await screen.findByRole("link", { name: `Open on ${site}` });
    expect(link).toHaveAttribute("href", `https://www.${site}/orders`);
  });
});

describe("changing something in an order", () => {
  it("keeps the order on screen while it loads again, instead of falling back to Loading…", async () => {
    const order = { id: "x", retailer: "costco", channel: "store", order_number: "77", placed: "2026-09-26", total: 10, details: 1, url: "https://www.costco.com",
      items: [], charges: [{ id: "c1", amount: -10, date: "2026-09-26", tx_id: "t1", payee: "Costco", posted: "2026-09-26", account_name: "Visa", applied: null }] };
    let reload: (v: unknown) => void = () => {};
    vi.mocked(api).mockImplementation((async (path: string, opts?: { method?: string }) => {
      if (opts?.method === "POST") return { ok: true };
      if (path.startsWith("/api/retail/orders/")) return vi.mocked(api).mock.calls.filter((c) => String(c[0]).startsWith("/api/retail/orders/")).length > 1
        ? new Promise((res) => { reload = res; }) : order;
      return {};
    }) as never);
    const onchange = vi.fn();
    render(OrderDetail, { orderId: "x", onchange });
    await userEvent.click(await screen.findByRole("button", { name: "Not this transaction" }));
    await waitFor(() => expect(onchange).toHaveBeenCalled());
    await waitFor(() => expect(vi.mocked(api).mock.calls.filter((c) => String(c[0]).startsWith("/api/retail/orders/"))).toHaveLength(2));
    // the second read hasn't answered: the order is still there
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
    expect(screen.getByText(/Costco purchase 77/)).toBeInTheDocument();
    reload({ ...order, charges: [] });
    expect(await screen.findByText("No card charges for this order yet.")).toBeInTheDocument();
  });
});

describe("the AI's suggestions for an order's items", () => {
  const order = (items: unknown[]) => ({ id: "x", retailer: "costco", channel: "store", order_number: "77", placed: "2026-09-26", total: 10, details: 1, url: "https://www.costco.com",
    items, charges: [] });
  const item = (id: number, title: string, category: string | null) => ({ id, title, amount: 5, quantity: 1, category, category_source: category ? "manual" : null });

  it("offers each suggestion and creates a proposed new category when you use it", async () => {
    let items = [item(1, "BANANAS", null), item(2, "KS PAPER TOWEL", null), item(3, "MILK", "Groceries")];
    vi.mocked(api).mockImplementation((async (path: string) => {
      if (path === "/api/retail/orders/x/suggest") return [{ item_id: 1, category: "Groceries", new_category: null }, { item_id: 2, category: null, new_category: { name: "Paper Goods", parent: "Groceries" } }];
      if (path.startsWith("/api/retail/items/")) { items = items.map((x) => (x.id === 2 ? { ...x, category: "Paper Goods" } : x)); return { category: "Paper Goods", created: true, orders: 1 }; }
      if (path.startsWith("/api/retail/orders/")) return order(items);
      return {};
    }) as never);
    render(OrderDetail, { orderId: "x" });
    await userEvent.click(await screen.findByRole("button", { name: "Suggest categories with AI" }));
    expect(await screen.findByRole("button", { name: "Use Groceries for BANANAS" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create Paper Goods and use it for KS PAPER TOWEL" }));
    await waitFor(() => expect(vi.mocked(api).mock.calls.some((c) => c[0] === "/api/retail/items/2")).toBe(true));
    const call = vi.mocked(api).mock.calls.find((c) => c[0] === "/api/retail/items/2")!;
    expect((call[1] as { body: unknown }).body).toEqual({ new_category: { name: "Paper Goods", parent: "Groceries" } });
    await waitFor(() => expect(screen.queryByRole("button", { name: /Create Paper Goods/ })).not.toBeInTheDocument());
  });

  it("doesn't offer to suggest when every item has a category", async () => {
    vi.mocked(api).mockResolvedValue(order([item(1, "MILK", "Groceries")]) as never);
    render(OrderDetail, { orderId: "x" });
    await screen.findByText("MILK");
    expect(screen.queryByRole("button", { name: "Suggest categories with AI" })).not.toBeInTheDocument();
  });
});
