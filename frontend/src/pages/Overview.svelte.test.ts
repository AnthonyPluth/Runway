// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import type { Overview as Forecast } from "$lib/types";
import Overview from "./Overview.svelte";

const forecast = (days: number, balance: number): Forecast => ({
  today: "2026-03-15", dates: Array.from({ length: days }, (_, i) => new Date(Date.UTC(2026, 2, 15 + i)).toISOString().slice(0, 10)),
  total: Array.from({ length: days }, () => balance), low: { date: "2026-03-15", balance },
  accounts: [{ id: "chk", name: "Everyday Checking", kind: "checking", balance }],
  events: [], cards: [], warnings: [],
});

// jsdom doesn't lay out SVG; the chart measures its labels.
Object.assign(SVGElement.prototype, { getBBox: () => ({ x: 0, y: 0, width: 0, height: 0 }) });

beforeEach(() => {
  vi.mocked(api).mockReset();
  app.state = { connected: true, horizon_days: 90, setup: { bank: true, primary: true, recurring: true, budgets: true, dismissed: false } };
});

describe("Overview", () => {
  it("keeps the forecast on screen while another length loads", async () => {
    let answer: (fc: Forecast) => void = () => {};
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/api/overview?days=90") return forecast(90, 1000) as never;
      if (path === "/api/overview?days=30") return new Promise((r) => { answer = r as typeof answer; }) as never;
      return new Promise(() => {}) as never;   // This month isn't under test
    });
    render(Overview);
    expect(await screen.findByText(/stays above \$1,000 for 90\sdays/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("radio", { name: "1M" }));
    expect(api).toHaveBeenCalledWith("/api/overview?days=30");
    // Still the 90-day forecast, not the loading placeholder, until the new one arrives.
    expect(screen.getByText(/stays above \$1,000 for 90\sdays/)).toBeInTheDocument();
    expect(document.querySelector("[aria-busy=true]")).toBeNull();

    answer(forecast(30, 2000));
    expect(await screen.findByText(/stays above \$2,000 for 30\sdays/)).toBeInTheDocument();
  });
});
