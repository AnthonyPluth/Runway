// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/categories.svelte", () => ({ loadCategories: vi.fn(async () => {}), categories: { list: [] }, catLabel: (x: string) => x, categoryGroups: () => [] }));

import { api } from "$lib/api";
import { toast } from "svelte-sonner";
import OrderDetail from "./OrderDetail.svelte";

const undo = () => (vi.mocked(toast).mock.calls.at(-1)![1] as unknown as { action: { onClick: () => Promise<void> } }).action.onClick();

beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(toast).mockClear(); });

describe("an order's link to the store", () => {
  it.each([["amazon", "amazon.com"], ["target", "target.com"], ["costco", "costco.com"]])("names the right site for %s", async (retailer, site) => {
    vi.mocked(api).mockResolvedValue({ id: "x", retailer, channel: "store", order_number: "1", placed: "2026-09-26", total: 10, items: [], charges: [],
      url: `https://www.${site}/orders` } as never);
    render(OrderDetail, { orderId: "x" });
    const link = await screen.findByRole("link", { name: `Open on ${site}` });
    expect(link).toHaveAttribute("href", `https://www.${site}/orders`);
  });
});

describe("a receipt opened from a category filter", () => {
  const order = { id: "x", retailer: "amazon", order_number: "1", placed: "2026-09-26", total: 110, subtotal: 100, details: 1, url: "https://www.amazon.com", charges: [],
    items: [{ id: 1, title: "Tea", quantity: 1, amount: 30, category: "Groceries" }, { id: 2, title: "Lamp", quantity: 1, amount: 70, category: "Shopping" }] };

  it("shows only that category's items, their share of tax and shipping, and the rest on asking", async () => {
    vi.mocked(api).mockResolvedValue(order as never);
    render(OrderDetail, { orderId: "x", family: ["Groceries"] });
    expect(await screen.findByText("Tea")).toBeInTheDocument();
    expect(screen.queryByText("Lamp")).not.toBeInTheDocument();
    expect(screen.getByText("+ ~$3.00 tax & shipping")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show all 2 items" }));
    expect(screen.getByText("Lamp")).toBeInTheDocument();
  });

  it("says tax and shipping without a figure when the order doesn't give its subtotal", async () => {
    vi.mocked(api).mockResolvedValue({ ...order, subtotal: null } as never);
    render(OrderDetail, { orderId: "x", family: ["Groceries"] });
    expect(await screen.findByText("+ tax & shipping")).toBeInTheDocument();
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

describe("undo and failures", () => {
  const order = { id: "x", retailer: "amazon", order_number: "1", placed: "2026-09-26", total: 10, subtotal: 10, details: 1, url: "https://www.amazon.com",
    items: [{ id: 7, title: "Tea", quantity: 1, amount: 10, category: "Groceries", category_source: "manual" }],
    charges: [{ id: "c1", amount: -10, date: "2026-09-26", tx_id: "t1", payee: "Amazon", posted: "2026-09-26", account_name: "Visa", applied: null }] };
  const serveOrder = (reply: unknown) => vi.mocked(api).mockImplementation((async (path: string, opts?: { method?: string }) => {
    if (opts?.method === "POST") return reply;
    if (path.startsWith("/api/retail/orders/")) return order;
    return {};
  }) as never);

  it("shows each item with just its price and category picker, without saying who chose the category", async () => {
    vi.mocked(api).mockResolvedValue({ ...order, items: [
      { id: 7, title: "Tea", quantity: 1, amount: 10, category: "Groceries", category_source: "manual" },
      { id: 8, title: "Lamp", quantity: 1, amount: 20, category: "Shopping", category_source: "memory" },
      { id: 9, title: "Mug", quantity: 1, amount: 5, category: "Shopping", category_source: "ai" },
      { id: 10, title: "Bag", quantity: 1, amount: 2, category: null, category_source: null }] } as never);
    render(OrderDetail, { orderId: "x" });
    expect(await screen.findByText("Tea")).toBeInTheDocument();
    for (const label of [/you picked/, /as before/, /^AI$/, /store's department/, /uses the transaction's category/])
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Category for Tea" }).closest(".col-span-2, [class*='col-span-2']")).not.toBeNull();
  });

  it("says a picked category applies to the item in every order", async () => {
    serveOrder({});
    render(OrderDetail, { orderId: "x" });
    expect(await screen.findByText("A category picked for an item applies to it in every order.")).toBeInTheDocument();
  });

  it("undoes Not this transaction with what the server sent back", async () => {
    const was = { charge: { id: "c1", tx_id: "t1" }, tx: [{ id: "t1" }] };
    serveOrder({ ok: true, was });
    const onchange = vi.fn();
    render(OrderDetail, { orderId: "x", onchange });
    await userEvent.click(await screen.findByRole("button", { name: "Not this transaction" }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Unmatched, and the transaction is back as it was", expect.objectContaining({ action: expect.anything() })));
    await undo();
    expect(api).toHaveBeenCalledWith("/api/retail/charges/c1/restore", { method: "POST", body: { was } });
    expect(onchange).toHaveBeenCalledTimes(2);
  });

  it("undoes Split by items", async () => {
    vi.mocked(api).mockImplementation((async (path: string, opts?: { method?: string }) => {
      if (opts?.method === "POST") return { result: "split", was: { charge: { id: "c1" } } };
      if (path.startsWith("/api/retail/orders/")) return { ...order, items: [...order.items, { id: 8, title: "Lamp", quantity: 1, amount: 0, category: "Shopping" }] };
      return {};
    }) as never);
    render(OrderDetail, { orderId: "x" });
    await userEvent.click(await screen.findByRole("button", { name: "Split by items" }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Split by items", expect.anything()));
    await undo();
    expect(api).toHaveBeenCalledWith("/api/retail/charges/c1/restore", { method: "POST", body: { was: { charge: { id: "c1" } } } });
  });

  it("offers to try again when the order couldn't be loaded", async () => {
    let fail = true;
    vi.mocked(api).mockImplementation((async () => { if (fail) throw new Error("Can’t reach Runway."); return order; }) as never);
    render(OrderDetail, { orderId: "x" });
    expect(await screen.findByText(/Can’t reach Runway\./)).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Tea")).toBeInTheDocument();
  });
});
