// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
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
