// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/categories.svelte", () => ({ loadCategories: vi.fn(async () => {}), categories: { list: [] }, catLabel: (x: string) => x, categoryGroups: () => [] }));

import { api } from "$lib/api";
import OrderDetail from "./OrderDetail.svelte";

beforeEach(() => vi.mocked(api).mockReset());

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
      if (String(path).startsWith("/api/retail/orders/")) return vi.mocked(api).mock.calls.filter((c) => String(c[0]).startsWith("/api/retail/orders/")).length > 1
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
