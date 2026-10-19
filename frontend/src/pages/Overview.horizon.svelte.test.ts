// @vitest-environment jsdom
// The forecast length controls stay true to the data on screen: a length that fails to load leaves the previous forecast,
// its selection and an inline alert with Retry; replies that arrive out of order never overwrite the newest choice.
import { cleanup, render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import type { Overview as OverviewData } from "$lib/types";
import { toast } from "svelte-sonner";
import Overview, { forgetForecast } from "./Overview.svelte";

Object.assign(SVGElement.prototype, { getBBox: () => ({ x: 0, y: 0, width: 0, height: 0 }) });

const forecast = (days: number, balance: number): OverviewData => ({
  today: "2026-03-15", dates: Array.from({ length: days }, (_, i) => new Date(Date.UTC(2026, 2, 15 + i)).toISOString().slice(0, 10)),
  total: Array.from({ length: days }, () => balance), low: { date: "2026-03-15", balance },
  accounts: [{ id: "chk", name: "Everyday Checking", kind: "checking", balance }],
  events: [], cards: [], unlinked_cards: [], warnings: [], warning_links: [], missed: [], budget: null,
});

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(toast.error).mockClear();
  forgetForecast();   // the page keeps the length and forecast last shown at module level
  app.state = { connected: true, primary_account: "chk", horizon_days: 90, setup: { bank: true, primary: true, recurring: true, budgets: true, dismissed: true } };
});
afterEach(() => cleanup());

describe("forecast length", () => {
  // Deferred requests, one per asked length, settled by the test; the chart's label and the sentence come from the reply.
  type Pending = { ok: (fc: OverviewData) => void; fail: (m: string) => void };
  let asked: Record<string, Pending[]>;
  const horizonFc = (days: number, balance: number) => ({ ...forecast(days, balance), dates: forecast(days + 1, balance).dates, total: forecast(days + 1, balance).total });
  const chart = () => screen.getByRole("img", { name: /Projected balance/ });
  const radio = (name: string) => screen.getByRole("radio", { name });
  const defer = () => {
    asked = {};
    vi.mocked(api).mockImplementation((async (path: string) => {
      if (!path.startsWith("/api/overview")) return new Promise(() => {});
      return new Promise((ok, fail) => { (asked[path] ||= []).push({ ok: ok as Pending["ok"], fail: (m) => fail(new Error(m)) }); });
    }) as never);
  };
  const last = (path: string) => asked[path].at(-1)!;
  const settle = async () => { await Promise.resolve(); await Promise.resolve(); };
  const fresh = async (opening = 90) => {
    app.state!.horizon_days = opening;
    defer();
    render(Overview);
    await waitFor(() => expect(asked[`/api/overview?days=${opening}`]).toBeTruthy());
  };

  it("keeps the previous forecast and selection when a longer one fails, then retries it", async () => {
    await fresh(90);
    last("/api/overview?days=90").ok(horizonFc(90, 1000));
    expect(await screen.findByText(/stays above \$1,000 for 90\sdays/)).toBeInTheDocument();
    expect(chart()).toHaveAccessibleName("Projected balance over the next 90 days");

    await userEvent.click(radio("6M"));
    expect(await screen.findByRole("status")).toHaveTextContent(/Updating to 6 months/);
    expect(radio("3M")).toBeChecked();
    last("/api/overview?days=180").fail("Service unavailable");

    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t load 6 months. Showing the previous 90 days forecast.");
    expect(screen.queryByRole("status")).toBeNull();
    expect(radio("3M")).toBeChecked();
    expect(screen.getByText(/stays above \$1,000 for 90\sdays/)).toBeInTheDocument();
    expect(chart()).toHaveAccessibleName("Projected balance over the next 90 days");
    expect(toast.error).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(asked["/api/overview?days=180"]).toHaveLength(2);
    expect(screen.getByRole("alert")).toBeInTheDocument();   // still up until a replacement arrives
    expect(radio("3M")).toBeChecked();
    last("/api/overview?days=180").ok(horizonFc(180, 2000));

    expect(await screen.findByText(/stays above \$2,000 for 6\smonths/)).toBeInTheDocument();
    expect(radio("6M")).toBeChecked();
    expect(chart()).toHaveAccessibleName("Projected balance over the next 180 days");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("clears the alert when another length is chosen", async () => {
    await fresh(90);
    last("/api/overview?days=90").ok(horizonFc(90, 1000));
    await screen.findByText(/stays above \$1,000 for 90\sdays/);
    await userEvent.click(radio("1M"));
    last("/api/overview?days=30").fail("down");
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t load 30 days.");
    await userEvent.click(radio("2M"));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent(/Updating to 60 days/);
  });

  it("offers a retry when the first load fails, and draws the forecast once it works", async () => {
    await fresh(90);
    last("/api/overview?days=90").fail("Service unavailable");
    expect(await screen.findByText(/Something went wrong: Service unavailable/)).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /Projected balance/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(asked["/api/overview?days=90"]).toHaveLength(2);
    last("/api/overview?days=90").ok(horizonFc(90, 1500));
    expect(await screen.findByText(/stays above \$1,500 for 90\sdays/)).toBeInTheDocument();
    expect(chart()).toHaveAccessibleName("Projected balance over the next 90 days");
  });

  it("lets the newest choice win when replies arrive in reverse order", async () => {
    await fresh(90);
    last("/api/overview?days=90").ok(horizonFc(90, 1000));
    await screen.findByText(/stays above \$1,000 for 90\sdays/);
    await userEvent.click(radio("1M"));
    await userEvent.click(radio("2M"));
    expect(screen.getByRole("status")).toHaveTextContent(/Updating to 60 days/);
    last("/api/overview?days=60").ok(horizonFc(60, 3000));
    expect(await screen.findByText(/stays above \$3,000 for 60\sdays/)).toBeInTheDocument();
    last("/api/overview?days=30").ok(horizonFc(30, 4000));   // the older one answers late
    await settle();
    expect(screen.getByText(/stays above \$3,000 for 60\sdays/)).toBeInTheDocument();
    expect(radio("2M")).toBeChecked();
    expect(chart()).toHaveAccessibleName("Projected balance over the next 60 days");

    // a late failure of an older request neither raises an alert nor undoes the newer forecast
    await userEvent.click(radio("6M"));
    await userEvent.click(radio("1M"));
    last("/api/overview?days=30").ok(horizonFc(30, 5000));
    expect(await screen.findByText(/stays above \$5,000 for 30\sdays/)).toBeInTheDocument();
    last("/api/overview?days=180").fail("late");
    await settle();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(radio("1M")).toBeChecked();
  });

  it("remembers the length that arrived, not one that failed", async () => {
    await fresh(90);
    last("/api/overview?days=90").ok(horizonFc(90, 1000));
    await screen.findByText(/stays above \$1,000 for 90\sdays/);
    await userEvent.click(radio("6M"));
    last("/api/overview?days=180").fail("down");
    await screen.findByRole("alert");
    cleanup();
    defer();
    render(Overview);   // reopened: it asks for the length last shown, not 6M
    await waitFor(() => expect(asked["/api/overview?days=90"]).toBeTruthy());
    expect(asked["/api/overview?days=180"]).toBeUndefined();
  });
});
