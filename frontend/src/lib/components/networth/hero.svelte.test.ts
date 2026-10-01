// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: { brands: {}, connected: true }, version: 0 }, refreshState: vi.fn(), reload: vi.fn() }));

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

  it("says since when the change really is, when the snapshot it's measured from is older than the range", async () => {
    // Opened 400, 51 and 31 days ago and today: "30 days" is from 31 days ago (close enough), "90 days" really from a year ago
    const history = [400, 51, 31, 0].map((n, i) => ({ date: day(n), net: 1000 + i * 500 }));
    serve({ ...nw(history, { "30d": 300, "90d": 1200, "1y": 2000 }), change_since: { "30d": day(31), "90d": day(400), "1y": day(400) } });
    render(NetWorth);
    expect(await screen.findByText("+$300.00 in the last 30 days")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: "90d" }));
    expect(await screen.findByText(/^\+\$1,200\.00 since Aug\s26,\s2025$/)).toBeInTheDocument();
    serve({ ...nw(history, { "30d": 300, "90d": 1200, "1y": 2000 }), change_since: { "30d": day(51), "90d": null, "1y": null } });
    render(NetWorth);
    expect(await screen.findByText(/^\+\$300\.00 since Aug\s10$/)).toBeInTheDocument();
  });

  it("shows no change, and no placeholder, when the ranges have too little history", async () => {
    serve(nw([{ date: "2026-09-29", net: 5000 }, { date: "2026-09-30", net: 5100 }], { "30d": null, "90d": null, "1y": null }));
    render(NetWorth);
    expect(await screen.findByText("Net worth")).toBeInTheDocument();
    expect(screen.queryByText(/not enough history/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/in the last|since/)).not.toBeInTheDocument();
  });

  it("has no chart and no filler line when there is no history yet", async () => {
    serve(nw([{ date: "2026-09-30", net: 5000 }], { "30d": null, "90d": null, "1y": null }));
    const { container } = render(NetWorth);
    expect(await screen.findByText("Net worth")).toBeInTheDocument();
    expect(screen.queryByText(/chart fills in|Tracking since/)).not.toBeInTheDocument();
    expect(container.querySelector("svg[role=img]")).toBeNull();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByText("Over time")).not.toBeInTheDocument();
  });
});
