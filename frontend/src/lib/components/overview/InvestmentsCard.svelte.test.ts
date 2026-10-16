// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn() }));
vi.mock("$lib/components/investments/live", () => ({ livePrices: vi.fn(() => () => {}) }));

import type { InvestmentsSummary } from "$lib/api-types";
import { api } from "$lib/api";
import { livePrices } from "$lib/components/investments/live";
import type { LiveQuotes } from "$lib/components/investments/types";
import InvestmentsCard from "./InvestmentsCard.svelte";
import { applyLive } from "./investmentsCard";

const summary = (over: Partial<InvestmentsSummary> = {}): InvestmentsSummary => ({
  empty: false, total: 5460, day_change: -80, day_change_pct: -0.0145, market: "closed",
  movers: [{ ticker: "BBB", day_change_pct: -0.1 }, { ticker: "CCC", day_change_pct: 0.03 }, { ticker: "AAA", day_change_pct: 0.02 }],
  trend: [5500, 5480, 5460],
  positions: [{ ticker: "AAA", quantity: 10, value: 1020, day_change: 20 }, { ticker: "BBB", quantity: 10, value: 900, day_change: -100 }],
  ...over,
});

beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(livePrices).mockClear(); });
afterEach(() => { vi.useRealTimers(); });

describe("InvestmentsCard", () => {
  it("shows the total, today's change, the movers, and links to Investments", async () => {
    vi.mocked(api).mockResolvedValue(summary());
    render(InvestmentsCard);
    const link = await screen.findByRole("link", { name: "Investments" });
    expect(link).toHaveAttribute("href", "#investments");
    expect(api).toHaveBeenCalledWith("/api/investments/summary");
    expect(screen.getByText("$5,460")).toBeInTheDocument();
    expect(screen.getByText(/−\$80\.00/)).toBeInTheDocument();
    expect(screen.getByText("BBB")).toBeInTheDocument();
    expect(screen.getByText("−10.0%")).toBeInTheDocument();
    expect(screen.getByText("+3.0%")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Value over the last month" })).toBeInTheDocument();
    expect(screen.queryByText("Live")).not.toBeInTheDocument();
  });

  it("is not shown with no investment accounts", async () => {
    vi.mocked(api).mockResolvedValue(summary({ empty: true, total: 0, day_change: null, day_change_pct: null, movers: [], trend: [], positions: [] }));
    render(InvestmentsCard);
    await waitFor(() => expect(api).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId("investments-card")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Investments" })).not.toBeInTheDocument();
  });

  it("says it couldn't load, with no figures, and tries again", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("down")).mockResolvedValueOnce(summary());
    render(InvestmentsCard);
    expect(await screen.findByText(/Couldn't load investments/)).toBeInTheDocument();
    expect(screen.queryByText("$5,460")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("$5,460")).toBeInTheDocument();
    expect(screen.queryByText(/Couldn't load investments/)).not.toBeInTheDocument();
  });

  it("re-prices the total and today's change from live quotes, and shows the Live dot while the market is open", async () => {
    vi.mocked(api).mockResolvedValue(summary());
    render(InvestmentsCard);
    await screen.findByText("$5,460");
    const onQuotes = vi.mocked(livePrices).mock.calls[0][0];
    onQuotes({ quotes: { AAA: { price: 110, prev_close: 105, time: 1, type: "EQUITY" } }, market: "open", as_of: "" } as LiveQuotes);
    expect(await screen.findByText("$5,540")).toBeInTheDocument();   // 5460 − 1020 + 1100
    expect(screen.getByText(/−\$50\.00/)).toBeInTheDocument();      // AAA +50 (110 against 105), BBB −100
    expect(screen.getByText("Live")).toBeInTheDocument();
  });
});

describe("applyLive", () => {
  it("adds the total and the change up again, leaving cash and the base alone", () => {
    const base = summary();
    const out = applyLive(base, { AAA: { price: 110, prev_close: 105, time: 1, type: "EQUITY" } }, "open");
    expect(out.total).toBe(5540);
    expect(out.day_change).toBe(-50);   // AAA +50, BBB −100
    expect(out.day_change_pct).toBeCloseTo(-50 / 2050, 10);   // of what they were worth at the last close
    expect(out.trend.at(-1)).toBe(5540);
    expect(base.total).toBe(5460);
    expect(out.market).toBe("open");
  });

  it("leaves a position with no quote as it was", () => {
    expect(applyLive(summary(), {}, "closed")).toMatchObject({ total: 5460, day_change: -80 });
  });
});
