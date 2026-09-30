// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: { brands: {} }, version: 0 }, refreshState: vi.fn(), reload: vi.fn() }));

import { api } from "$lib/api";
import NetWorth from "../../../pages/NetWorth.svelte";

const day = (n: number) => new Date(Date.UTC(2026, 8, 30 - n)).toISOString().slice(0, 10);
const nw = (history: { date: string; net: number }[], change: Record<string, number | null>) => ({
  today: "2026-09-30", net: 5000, assets: 5500, liabilities: 500,
  history: history.map((h) => ({ ...h, assets: h.net, liabilities: 0 })), first_snapshot: history[0]?.date ?? null, change,
  assets_list: [], loan_accounts: [], realie: { configured: false, used: 0, limit: 0 }, excluded: [],
  groups: [{ key: "cash", label: "Cash", side: "asset", total: 5500, items: [] }, { key: "cc", label: "Cards", side: "liability", total: 500, items: [] }],
});
const serve = (body: unknown) => vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/networth" ? body : {})) as never);

beforeEach(() => { vi.mocked(api).mockReset(); });

describe("the net worth hero", () => {
  it("switches the change figure and the chart's range, and disables a range with no data", async () => {
    const history = [365, 200, 90, 60, 30, 10, 0].map((n, i) => ({ date: day(n), net: 1000 + i * 500 }));
    serve(nw(history, { "30d": 300, "90d": 1200, "1y": null }));
    const { container } = render(NetWorth);
    expect(await screen.findByText("+$300.00 in the last 30 days")).toBeInTheDocument();
    expect(screen.queryByText("Over time")).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "1y" })).toBeDisabled();
    const chart = () => container.querySelector("svg[role=img]")!.innerHTML;
    const before = chart();
    await userEvent.click(screen.getByRole("radio", { name: "90d" }));
    expect(await screen.findByText("+$1,200.00 in the last 90 days")).toBeInTheDocument();
    await waitFor(() => expect(chart()).not.toBe(before));
  });

  it("is a single muted line, with no chart, when there is no history yet", async () => {
    serve(nw([{ date: "2026-09-30", net: 5000 }], { "30d": null, "90d": null, "1y": null }));
    const { container } = render(NetWorth);
    expect(await screen.findByText(/Tracking since Sep 30, 2026\. The chart fills in as the days go by\./)).toBeInTheDocument();
    expect(container.querySelector("svg[role=img]")).toBeNull();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByText("Over time")).not.toBeInTheDocument();
  });
});
