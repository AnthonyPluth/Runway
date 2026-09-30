// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));

import { api } from "$lib/api";
import Cashflow from "./Cashflow.svelte";
import Status from "./Status.svelte";
import Tile from "./Tile.svelte";
import { reportState } from "./state.svelte";
import type { Cashflow as Flow } from "./types";

const desc = { selector: "dt > span" };   // "Money in" is also a table heading
const flow = (extra: Partial<Flow> = {}): Flow => ({
  month: "2026-03",
  income: [{ name: "Salary", value: 4000 }],
  spending: [{ name: "Housing", value: 1500, children: [{ name: "Rent", value: 1500 }] }, { name: "Food", value: 500, children: [] }],
  total_in: 4000, total_out: 2000, net: 2000, ...extra,
});
beforeEach(() => { vi.mocked(api).mockReset(); reportState.month = "2026-03"; });

describe("Cashflow report", () => {
  it("shows money in, money out and what's left over", async () => {
    vi.mocked(api).mockResolvedValue(flow());
    render(Cashflow);
    expect(await screen.findByText("Money in", desc)).toBeInTheDocument();
    expect(screen.getByText("$4,000", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByText("$2,000", { selector: "dd" })).toBeInTheDocument();   // money out
    expect(screen.getByText("$2,000", { selector: "div" })).toBeInTheDocument();   // the left-over hero
    expect(screen.getByText("Left over", { selector: "div" })).toBeInTheDocument();
    expect(screen.getByText("50% of money in")).toBeInTheDocument();
    expect(api).toHaveBeenCalledWith("/api/cashflow?month=2026-03");
  });

  it("flags a month that spent more than came in", async () => {
    vi.mocked(api).mockResolvedValue(flow({ total_in: 1000, total_out: 1500, net: -500 }));
    render(Cashflow);
    const label = await screen.findByText("▲ Spent more than came in", { selector: "div" });
    expect(label).toHaveClass("text-destructive");
    expect(screen.getByText("$500", { selector: "div" })).toHaveClass("text-destructive");   // shown without the sign
  });

  it("lists the same numbers as a table with shares and subcategories", async () => {
    vi.mocked(api).mockResolvedValue(flow());
    render(Cashflow);
    await userEvent.click(await screen.findByText("Show as table"));
    const table = screen.getByRole("table");
    expect(within(table).getByRole("row", { name: /Salary \$4,000\.00 100%/ })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /^Housing \$1,500\.00 75%/ })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /Housing\s*>\s*Rent\s+\$1,500\.00 75%/ })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /Food \$500\.00 25%/ })).toBeInTheDocument();
  });

  it("says when the month has no transactions", async () => {
    vi.mocked(api).mockResolvedValue(flow({ income: [], spending: [], total_in: 0, total_out: 0, net: 0 }));
    render(Cashflow);
    expect(await screen.findByText(/No transactions in March 2026\./)).toBeInTheDocument();
    expect(screen.getByText(/Try an earlier month/)).toBeInTheDocument();
  });

  it("steps back a month from the empty message", async () => {
    vi.mocked(api).mockResolvedValue(flow({ income: [], spending: [], total_in: 0, total_out: 0, net: 0 }));
    render(Cashflow);
    await screen.findByText(/No transactions in March 2026\./);
    vi.mocked(api).mockResolvedValue(flow({ month: "2026-02" }));
    await userEvent.click(screen.getByRole("button", { name: "Go back one month" }));
    expect(api).toHaveBeenLastCalledWith("/api/cashflow?month=2026-02");
    expect(await screen.findByText("February 2026")).toBeInTheDocument();
    expect(screen.queryByText(/No transactions in/)).not.toBeInTheDocument();
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

  it("shows the error with Try again when it can't load", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Offline"));
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(Cashflow);
    expect(await screen.findByText("Something went wrong: Offline")).toBeInTheDocument();
    vi.mocked(api).mockResolvedValue(flow());
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Money in", desc)).toBeInTheDocument();
  });
});

describe("report Tile and Status", () => {
  it("Tile shows its label, value and note, and turns red when alerting", () => {
    render(Tile, { label: "Spent", value: "$5", sub: "note", alert: true });
    expect(screen.getByText("Spent")).toHaveClass("text-destructive");
    expect(screen.getByText("$5")).toHaveClass("text-destructive");
    expect(screen.getByText("note")).toBeInTheDocument();
  });

  it("Status says Loading until there's an error, then offers a retry", async () => {
    const retry = vi.fn();
    const { unmount } = render(Status, { error: null, retry });
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    unmount();
    render(Status, { error: new Error("bad"), retry });
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalled();
  });
});
