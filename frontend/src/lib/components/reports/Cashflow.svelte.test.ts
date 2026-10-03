// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));

import { api } from "$lib/api";
import { monthLabel, thisMonth } from "$lib/format";
import Cashflow from "./Cashflow.svelte";
import Status from "./Status.svelte";
import { reportState } from "./state.svelte";
import type { Cashflow as Flow } from "./types";

const desc = { selector: "dt > span" };   // "Money in" is also a table heading
const flow = (extra: Partial<Flow> = {}): Flow => ({
  month: "2026-03",
  income: [{ name: "Salary", value: 4000 }],
  spending: [{ name: "Housing", value: 1500, children: [{ name: "Rent", value: 1500 }] }, { name: "Food", value: 500, children: [] }],
  total_in: 4000, total_out: 2000, net: 2000, ...extra,
});
beforeEach(() => { vi.mocked(api).mockReset(); reportState.month = "2026-03"; reportState.monthPicked = true; });
afterEach(() => { vi.useRealTimers(); });

describe("Cashflow report", () => {
  it("shows money in, money out and what's left over", async () => {
    vi.mocked(api).mockResolvedValue(flow());
    render(Cashflow);
    expect(await screen.findByText("Money in", desc)).toBeInTheDocument();
    expect(screen.getByText("$4,000", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByText("$2,000", { selector: "dd" })).toBeInTheDocument();   // money out
    expect(screen.getByText("$2,000", { selector: "div" })).toBeInTheDocument();   // the left-over hero
    expect(screen.getByText("Left over", { selector: "div" })).toBeInTheDocument();
    expect(screen.getByText("Savings rate 50%")).toBeInTheDocument();
    expect(api).toHaveBeenCalledWith("/api/cashflow?month=2026-03");
  });

  it("flags a month that spent more than came in", async () => {
    vi.mocked(api).mockResolvedValue(flow({ total_in: 1000, total_out: 1500, net: -500 }));
    render(Cashflow);
    const label = await screen.findByText("Spent more than came in", { selector: "div" });
    expect(label).toHaveClass("text-destructive");
    expect(screen.queryByText(/▲/)).not.toBeInTheDocument();
    expect(screen.getByText("Savings rate −50%")).toBeInTheDocument();
    expect(screen.getByText("$500", { selector: "div" })).toHaveClass("text-destructive");   // shown without the sign
  });

  it("frames a month still under way as so far, in muted text, even when more went out than came in", async () => {
    reportState.month = thisMonth();
    vi.mocked(api).mockResolvedValue(flow({ month: thisMonth(), total_in: 0, total_out: 2140, net: -2140 }));
    render(Cashflow);
    const short = monthLabel(thisMonth()).split(" ")[0];
    const label = await screen.findByText(`Spent more than came in so far in ${short}`, { selector: "div" });
    expect(label).not.toHaveClass("text-destructive");
    expect(screen.getByText("$2,140", { selector: "div" })).not.toHaveClass("text-destructive");
    expect(screen.queryByText(/▲/)).not.toBeInTheDocument();
  });

  it("says what's left over so far in the month under way", async () => {
    reportState.month = thisMonth();
    vi.mocked(api).mockResolvedValue(flow({ month: thisMonth() }));
    render(Cashflow);
    expect(await screen.findByText(`Left over so far in ${monthLabel(thisMonth()).split(" ")[0]}`, { selector: "div" })).toBeInTheDocument();
  });

  it("rounds money in and out to whole dollars and derives what's left over from them, so the tiles add up", async () => {
    vi.mocked(api).mockResolvedValue(flow({ total_in: 1000.4, total_out: 499.6, net: 500.8 }));
    render(Cashflow);
    expect(await screen.findByText("$1,000", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByText("$500", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByText("$500", { selector: "div" })).toBeInTheDocument();   // 1,000 − 500, not $501
  });

  it("lists the same numbers as a table with shares and subcategories", async () => {
    vi.mocked(api).mockResolvedValue(flow());
    render(Cashflow);
    await userEvent.click(await screen.findByText("Show as table"));
    const table = screen.getByRole("table");
    expect(within(table).getByRole("row", { name: /Salary \$4,000 100%/ })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /^Housing \$1,500 75%/ })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /Housing\s*>\s*Rent\s+\$1,500 75%/ })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /Food \$500 25%/ })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /Left over \$2,000/ })).toBeInTheDocument();
  });

  it("says when the month has nothing in it, and only that", async () => {
    vi.mocked(api).mockResolvedValue(flow({ income: [], spending: [], total_in: 0, total_out: 0, net: 0 }));
    render(Cashflow);
    expect(await screen.findByText(/Nothing in March 2026 ·/)).toBeInTheDocument();
    expect(screen.queryByText("Money in", desc)).not.toBeInTheDocument();
    expect(screen.queryByText("Left over", { selector: "div" })).not.toBeInTheDocument();
  });

  it("says nothing's in the month under way yet", async () => {
    reportState.month = thisMonth();
    vi.mocked(api).mockResolvedValue(flow({ month: thisMonth(), income: [], spending: [], total_in: 0, total_out: 0, net: 0 }));
    render(Cashflow);
    expect(await screen.findByText(`Nothing in ${monthLabel(thisMonth()).split(" ")[0]} yet ·`, { exact: false })).toBeInTheDocument();
  });

  it("steps back a month from the empty message", async () => {
    vi.mocked(api).mockResolvedValue(flow({ income: [], spending: [], total_in: 0, total_out: 0, net: 0 }));
    render(Cashflow);
    await screen.findByText(/Nothing in March 2026/);
    vi.mocked(api).mockResolvedValue(flow({ month: "2026-02" }));
    await userEvent.click(screen.getByRole("button", { name: "Go back a month" }));
    expect(api).toHaveBeenLastCalledWith("/api/cashflow?month=2026-02");
    expect(await screen.findByText("February 2026")).toBeInTheDocument();
    expect(screen.queryByText(/Nothing in/)).not.toBeInTheDocument();
  });

  it("loads another month when you move to it, keeping the old one until it arrives", async () => {
    vi.mocked(api).mockResolvedValue(flow());
    render(Cashflow);
    await screen.findByText("March 2026");
    vi.mocked(api).mockResolvedValue(flow({ month: "2026-02" }));
    await userEvent.click(screen.getByRole("button", { name: "Previous month" }));
    expect(api).toHaveBeenLastCalledWith("/api/cashflow?month=2026-02");
    expect(await screen.findByText("February 2026")).toBeInTheDocument();
    expect(reportState.month).toBe("2026-02");
  });

  it("says it couldn't load, with Retry and the error behind Details", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Offline"));
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(Cashflow);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Couldn’t load March 2026.");
    expect(within(alert).getByText("Offline")).not.toBeVisible();
    await userEvent.click(within(alert).getByText("Details"));
    expect(within(alert).getByText("Offline")).toBeVisible();
    vi.mocked(api).mockResolvedValue(flow());
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Money in", desc)).toBeInTheDocument();
  });

  it("keeps the month picker on the month asked for when it fails to load, without the old month's numbers", async () => {
    vi.mocked(api).mockResolvedValue(flow());
    render(Cashflow);
    await screen.findByText("Money in", desc);
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(api).mockRejectedValue(new Error("Offline"));
    await userEvent.click(screen.getByRole("button", { name: "Previous month" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t load February 2026.");
    expect(screen.getByText("February 2026")).toBeInTheDocument();
    expect(screen.queryByText("March 2026")).not.toBeInTheDocument();
    expect(screen.queryByText("Money in", desc)).not.toBeInTheDocument();
  });

  it("dims the last month while the next loads, and the picker still works", async () => {
    vi.mocked(api).mockResolvedValue(flow());
    render(Cashflow);
    await screen.findByText("Money in", desc);
    let done!: (v: Flow) => void;
    vi.mocked(api).mockReturnValue(new Promise((r) => { done = r; }) as never);
    await userEvent.click(screen.getByRole("button", { name: "Previous month" }));
    const busy = screen.getByText("Money in", desc).closest("[aria-busy]")!;
    expect(busy).toHaveAttribute("aria-busy", "true");
    expect(busy).toHaveClass("opacity-60");
    expect(screen.getByRole("button", { name: "Previous month" })).toBeEnabled();
    done(flow({ month: "2026-02" }));
    expect(await screen.findByText("February 2026")).toBeInTheDocument();
    await vi.waitFor(() => expect(screen.getByText("Money in", desc).closest("[aria-busy]")).toHaveAttribute("aria-busy", "false"));
  });

  it("moves on to the new month when the calendar does, unless a month was picked", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(new Date("2026-09-30T23:59:00"));
    reportState.month = "2026-09"; reportState.monthPicked = false;
    vi.mocked(api).mockImplementation(((url: string) => Promise.resolve(flow({ month: url.slice(-7) }))) as never);
    render(Cashflow);
    expect(await screen.findByText("September 2026")).toBeInTheDocument();
    vi.setSystemTime(new Date("2026-10-01T00:01:00"));
    await vi.advanceTimersByTimeAsync(60_000);
    expect(api).toHaveBeenLastCalledWith("/api/cashflow?month=2026-10");
    expect(await screen.findByText("October 2026")).toBeInTheDocument();
    // A month someone picked stays
    reportState.month = "2026-08"; reportState.monthPicked = true;
    vi.mocked(api).mockClear();
    vi.setSystemTime(new Date("2026-11-01T00:01:00"));
    await vi.advanceTimersByTimeAsync(60_000);
    expect(api).not.toHaveBeenCalled();
  });
});

describe("report Status", () => {
  it("Status shows a placeholder block until there's an error, then an alert with a retry", async () => {
    const retry = vi.fn();
    const { unmount } = render(Status, { error: null, retry });
    expect(screen.getByRole("status")).toHaveTextContent("Loading…");
    expect(screen.getByRole("status")).toHaveClass("animate-pulse", "motion-reduce:animate-none");
    unmount();
    render(Status, { error: new Error("bad"), retry });
    expect(screen.getByRole("alert")).toHaveTextContent(/^Couldn’t load this report\./);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalled();
  });

  it("compact, it's a line with no placeholder while loading", () => {
    const { container } = render(Status, { error: null, retry: () => {}, compact: true });
    expect(container.textContent).toBe("");
    expect(screen.queryByRole("status")).toBeNull();
  });
});
