// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn() }));

import { api } from "$lib/api";
import { thisMonth } from "$lib/format";
import SummaryTiles from "./SummaryTiles.svelte";

const month = thisMonth();
const networth = (history = [{ date: `${month}-01`, net: 90000, assets: 0, liabilities: 0 }, { date: `${month}-10`, net: 91250, assets: 0, liabilities: 0 }]) =>
  ({ today: `${month}-10`, net: 91250, assets: 0, liabilities: 0, groups: [], excluded: [], history, first_snapshot: null,
    change: { "30d": 1250, "90d": null, "1y": null } });
const serve = (n: unknown) => vi.mocked(api).mockImplementation((async (path: string) => {
  if (path === "/api/networth") return n;
  throw new Error(path);
}) as never);

beforeEach(() => { vi.mocked(api).mockReset(); });
afterEach(cleanup);

describe("SummaryTiles", () => {
  it("is one tile, net worth, linking to Net worth, and asks for nothing about spending", async () => {
    serve(networth());
    render(SummaryTiles);
    expect(await screen.findByRole("link", { name: /^Net worth/ })).toHaveAttribute("href", "#networth");
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.queryByText(/^Spent in/)).not.toBeInTheDocument();
    expect(vi.mocked(api).mock.calls.map((c) => c[0])).toEqual(["/api/networth"]);
  });

  it("shows net worth with its change this month", async () => {
    serve(networth());
    const { container } = render(SummaryTiles);
    expect(await screen.findByText("$91,250")).toBeInTheDocument();
    expect(screen.getByText("+$1,250.00")).toBeInTheDocument();
    expect(screen.getByText("this month")).toBeInTheDocument();
    expect(container.querySelector("polyline")).not.toBeNull();
  });

  it("says so when net worth can't load, and tries again", async () => {
    let down = true;
    vi.mocked(api).mockImplementation((async () => { if (down) throw new Error("down"); return networth(); }) as never);
    render(SummaryTiles);
    expect(await screen.findByText("Couldn’t load net worth")).toBeInTheDocument();
    down = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("link", { name: /^Net worth/ })).toBeInTheDocument();
  });

  it("shows no change for the month when there's no snapshot from before it", async () => {
    serve(networth([{ date: `${month}-05`, net: 90500, assets: 0, liabilities: 0 }, { date: `${month}-10`, net: 91250, assets: 0, liabilities: 0 }]));
    render(SummaryTiles);
    expect(await screen.findByText("No change yet this month")).toBeInTheDocument();
    expect(screen.queryByText("this month")).not.toBeInTheDocument();
  });

  it("says where the change starts from when the snapshot is much older than the month", async () => {
    serve(networth([{ date: "2000-01-01", net: 90000, assets: 0, liabilities: 0 }, { date: `${month}-10`, net: 91250, assets: 0, liabilities: 0 }]));
    render(SummaryTiles);
    expect(await screen.findByText("+$1,250.00")).toBeInTheDocument();
    expect(screen.getByText("since Jan 1, 2000")).toBeInTheDocument();
    expect(screen.queryByText("this month")).not.toBeInTheDocument();
  });
});
