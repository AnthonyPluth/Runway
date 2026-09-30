// @vitest-environment jsdom
// Net worth is a hub: Summary, Investments and Equity are tabs of one page.
import { render, screen } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: { brands: {} }, version: 0 }, refreshState: vi.fn(), reload: vi.fn() }));

import { api } from "$lib/api";
import NetWorth from "../../../pages/NetWorth.svelte";

beforeEach(() => {
  vi.mocked(api).mockReset();
  const summary = {
    today: "2026-09-30", net: 5000, assets: 5500, liabilities: 500, history: [], first_snapshot: null, change: { "30d": null, "90d": null, "1y": null },
    assets_list: [], loan_accounts: [], realie: { configured: false, used: 0, limit: 0 }, excluded: [],
    groups: [{ key: "cash", label: "Cash", side: "asset", total: 5500, items: [] }],
  };
  vi.mocked(api).mockImplementation((async (path: string) =>
    (path === "/api/plaid/status" ? { inv_accounts: 0 } : path === "/api/networth" ? summary : {})) as never);
});

describe("the Net worth tabs", () => {
  it("shows the tab bar with Summary current by default", async () => {
    render(NetWorth);
    await screen.findByText("What makes it up");
    const tabs = screen.getByRole("navigation", { name: "Net worth" });
    expect(screen.getByRole("heading", { name: "Net worth" })).toBeInTheDocument();
    expect(Array.from(tabs.querySelectorAll("a")).map((a) => [a.textContent!.trim(), a.getAttribute("href")])).toEqual([
      ["Summary", "#networth"], ["Investments", "#networth/investments"], ["Equity", "#networth/equity"]]);
    expect(screen.getByRole("link", { name: "Summary" })).toHaveAttribute("aria-current", "page");
  });

  it("opens Investments on #networth/investments", async () => {
    render(NetWorth, { sub: "investments" });
    expect(screen.getByRole("link", { name: "Investments" })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByText("No investment accounts yet")).toBeInTheDocument();
  });

  it("opens Equity on #networth/equity, without its own back link", () => {
    render(NetWorth, { sub: "equity" });
    expect(screen.getByRole("link", { name: "Equity" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("link", { name: /← Net worth/ })).not.toBeInTheDocument();
  });
});
