// @vitest-environment jsdom
// Overview's first draw uses the forecast asked for while the state was loading (lib/prefetch.ts), and asks again only
// when the horizon the state names isn't the one that forecast used. Its own file so the page's module state starts fresh.
import { cleanup, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import { prefetchOverview } from "$lib/prefetch";
import type { Overview as OverviewData } from "$lib/types";
import Overview from "./Overview.svelte";

Object.assign(SVGElement.prototype, { getBBox: () => ({ x: 0, y: 0, width: 0, height: 0 }) });

const forecast = (days: number, balance: number): OverviewData => ({
  today: "2026-03-15", dates: Array.from({ length: days + 1 }, (_, i) => new Date(Date.UTC(2026, 2, 15 + i)).toISOString().slice(0, 10)),
  total: Array.from({ length: days + 1 }, () => balance), low: { date: "2026-03-15", balance },
  accounts: [{ id: "chk", name: "Everyday Checking", kind: "checking", balance }],
  events: [], cards: [], unlinked_cards: [], warnings: [], warning_links: [], missed: [], budget: null,
});
const overviewCalls = () => vi.mocked(api).mock.calls.map((c) => String(c[0])).filter((p) => p.startsWith("/api/overview"));
// What the server answers: the stored horizon (here 180) when no length is asked for.
const serve = (stored: number) => vi.mocked(api).mockImplementation((async (path: string) => {
  if (path === "/api/overview") return forecast(stored, 1234.56);
  if (path.startsWith("/api/overview?days=")) return forecast(Number(path.split("=")[1]), 1234.56);
  return new Promise(() => {});
}) as never);

beforeEach(() => { vi.mocked(api).mockReset(); });
afterEach(() => cleanup());

describe("Overview with an early forecast", () => {
  it("draws it with no second request when the state's horizon matches", async () => {
    serve(90);
    prefetchOverview();   // main.ts does this before the state is asked for
    app.state = { connected: true, primary_account: "chk", horizon_days: 90, setup: { bank: true, primary: true, recurring: true, budgets: true, dismissed: true } };
    render(Overview);
    expect((await screen.findAllByText(/Everyday Checking/)).length).toBeGreaterThan(0);
    expect(overviewCalls()).toEqual(["/api/overview"]);
  });

  it("asks again with the state's horizon when it isn't the one the early forecast used", async () => {
    serve(90);
    prefetchOverview();
    app.state = { connected: true, primary_account: "chk", horizon_days: 180, setup: { bank: true, primary: true, recurring: true, budgets: true, dismissed: true } };
    render(Overview);
    await waitFor(() => expect(overviewCalls()).toEqual(["/api/overview", "/api/overview?days=180"]));
  });
});
