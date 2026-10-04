// @vitest-environment jsdom
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));
vi.mock("$lib/app.svelte", () => ({
  app: { state: { brands: {}, connected: true }, version: 0 },
  refreshState: vi.fn(),
  reload: vi.fn(),
}));

import { api } from "$lib/api";
import NetWorth from "../../../pages/NetWorth.svelte";

const day = (n: number) =>
  new Date(Date.UTC(2026, 8, 30 - n)).toISOString().slice(0, 10);
const nw = (
  history: { date: string; net: number }[],
  change: Record<string, number | null>,
) => ({
  today: "2026-09-30",
  net: 5000,
  assets: 5500,
  liabilities: 500,
  history: history.map((h) => ({ ...h, assets: h.net, liabilities: 0 })),
  first_snapshot: history[0]?.date ?? null,
  change,
  assets_list: [],
  loan_accounts: [],
  realie: { configured: false, used: 0, limit: 0 },
  excluded: [],
  groups: [
    { key: "cash", label: "Cash", side: "asset", total: 5500, items: [] },
    { key: "cc", label: "Cards", side: "liability", total: 500, items: [] },
  ],
});
const plain = (el: Element) =>
  el
    .textContent!.replace(/\u00a0/g, " ")
    .replace(/\s+/g, " ")
    .trim();
const change = async () => plain(await screen.findByTestId("nw-change"));
const serve = (body: unknown) =>
  vi
    .mocked(api)
    .mockImplementation((async (path: string) =>
      path === "/api/networth" ? body : {}) as never);

beforeEach(() => {
  vi.mocked(api).mockReset();
});

describe("the net worth hero", () => {
  it("switches the change figure and the chart's range, and disables a range with no data", async () => {
    const history = [365, 200, 90, 60, 30, 10, 0].map((n, i) => ({
      date: day(n),
      net: 1000 + i * 500,
    }));
    serve(nw(history, { "30d": 300, "90d": 1200, "1y": null }));
    const { container } = render(NetWorth);
    expect(await change()).toBe("+$300 (+6.4%) in the last month");
    expect(screen.getByTestId("nw-change")).toHaveClass("text-good");
    expect(screen.queryByText("Over time")).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "1Y" })).toBeDisabled();
    const chart = () => container.querySelector("svg[role=slider]")!.innerHTML;
    const before = chart();
    await userEvent.click(screen.getByRole("radio", { name: "3M" }));
    await waitFor(async () =>
      expect(await change()).toBe("+$1,200 (+31.6%) in the last 3 months"),
    );
    await waitFor(() => expect(chart()).not.toBe(before));
  });

  it("says since when the change really is, when the snapshot it's measured from is older than the range", async () => {
    const history = [400, 51, 31, 0].map((n, i) => ({
      date: day(n),
      net: 1000 + i * 500,
    }));
    serve({
      ...nw(history, { "30d": 300, "90d": 1200, "1y": 2000 }),
      change_since: { "30d": day(31), "90d": day(400), "1y": day(400) },
    });
    const { unmount } = render(NetWorth);
    expect(await change()).toBe("+$300 (+6.4%) in the last month");
    await userEvent.click(screen.getByRole("radio", { name: "3M" }));
    await waitFor(async () =>
      expect(await change()).toBe("+$1,200 (+31.6%) since Aug 26, 2025"),
    );
    unmount();
    serve({
      ...nw(history, { "30d": 300, "90d": 1200, "1y": 2000 }),
      change_since: { "30d": day(51), "90d": null, "1y": null },
    });
    render(NetWorth);
    expect(await change()).toBe("+$300 (+6.4%) since Aug 10");
  });

  it("shows no change, and no placeholder, when the ranges have too little history", async () => {
    serve(
      nw(
        [
          { date: "2026-09-29", net: 5000 },
          { date: "2026-09-30", net: 5100 },
        ],
        { "30d": null, "90d": null, "1y": null },
      ),
    );
    render(NetWorth);
    expect(await screen.findByText("Net worth")).toBeInTheDocument();
    expect(screen.queryByText(/not enough history/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/in the last|since/)).not.toBeInTheDocument();
  });

  it("says the history builds up, in place of the chart, when there's only today's snapshot", async () => {
    serve(
      nw([{ date: "2026-09-30", net: 5000 }], {
        "30d": null,
        "90d": null,
        "1y": null,
      }),
    );
    const { container } = render(NetWorth);
    expect(await screen.findByTestId("nw-no-history")).toHaveTextContent(
      "History builds up each day you open Runway",
    );
    expect(container.querySelector("svg[role=slider]")).toBeNull();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByText("Over time")).not.toBeInTheDocument();
  });

  it("shows a loss in the loss color with a real minus", async () => {
    const history = [40, 30, 0].map((n, i) => ({
      date: day(n),
      net: 6000 - i * 500,
    }));
    serve(nw(history, { "30d": -1000, "90d": null, "1y": null }));
    render(NetWorth);
    expect(await change()).toBe("−$1,000 (−16.7%) in the last month");
    expect(screen.getByTestId("nw-change")).toHaveClass("text-loss");
  });

  it("leaves the percentage off when net worth started at or below zero", async () => {
    const history = [40, 30, 0].map((n) => ({ date: day(n), net: 100 }));
    serve(nw(history, { "30d": 6000, "90d": null, "1y": null }));
    render(NetWorth);
    expect(await change()).toBe("+$6,000 in the last month");
  });

  it("starts the chart where the change is measured from, so a sparse history doesn't fall back to all of it", async () => {
    const history = [400, 31, 0].map((n, i) => ({
      date: day(n),
      net: 1000 + i * 500,
    }));
    serve({
      ...nw(history, { "30d": 500, "90d": 1500, "1y": 1500 }),
      change_since: { "30d": day(31), "90d": day(400), "1y": day(400) },
    });
    render(NetWorth);
    const chart = await screen.findByRole("slider");
    expect(chart).toHaveAttribute("aria-valuemax", "1");
    await userEvent.click(screen.getByRole("radio", { name: "1Y" }));
    await waitFor(() =>
      expect(screen.getByRole("slider")).toHaveAttribute("aria-valuemax", "2"),
    );
  });

  it("follows the chart's readout in the headline, and goes back to today when it's put away", async () => {
    const history = [20, 10, 0].map((n, i) => ({
      date: day(n),
      net: 4000 + i * 100,
    }));
    serve(nw(history, { "30d": null, "90d": null, "1y": null }));
    render(NetWorth);
    const headline = await screen.findByTestId("nw-headline");
    expect(headline).toHaveTextContent("$5,000");
    const chart = screen.getByRole("slider");
    await fireEvent.keyDown(chart, { key: "Home" });
    expect(headline).toHaveTextContent("$4,000");
    expect(await change()).toBe("on Sep 10, 2026");
    await fireEvent.keyDown(chart, { key: "ArrowRight" });
    expect(headline).toHaveTextContent("$4,100");
    await fireEvent.keyDown(chart, { key: "Escape" });
    expect(headline).toHaveTextContent("$5,000");
    expect(screen.queryByTestId("nw-change")).toBeNull();
  });
});

describe("the net worth summary", () => {
  const card = (id: string, value: number) => ({
    type: "account",
    id,
    name: `Card ${id}`,
    value,
    org: "Sample Card Co",
  });

  it("says nothing is owed, without $0.00 groups, when the cards are all paid off", async () => {
    serve({
      ...nw([], { "30d": null, "90d": null, "1y": null }),
      liabilities: 0,
      groups: [
        {
          key: "cash",
          label: "Cash",
          side: "asset",
          total: 5500,
          items: [{ type: "account", id: "c", name: "Checking", value: 5500 }],
        },
        {
          key: "credit",
          label: "Credit cards",
          side: "liability",
          total: 0,
          items: [card("a", 0), card("b", 0)],
        },
      ],
    });
    render(NetWorth);
    expect(await screen.findByText("Nothing owed")).toBeInTheDocument();
    expect(screen.queryByText("Credit cards")).toBeNull();
    expect(screen.getByText("nothing owed")).toBeInTheDocument();
  });

  it("keeps each kind of asset's color whatever else there is, and puts it before the group's heading", async () => {
    const group = (key: string, label: string, total: number) => ({
      key,
      label,
      side: "asset",
      total,
      items: [{ type: "account", id: key, name: label, value: total }],
    });
    serve({
      ...nw([], { "30d": null, "90d": null, "1y": null }),
      groups: [group("cash", "Cash", 0), group("home", "Real estate", 5500)],
    });
    const { container } = render(NetWorth);
    await screen.findByText("What makes it up");
    const bar = container.querySelector(
      "[role=img][aria-label^='Share of assets']",
    )!;
    expect((bar.querySelector("span") as HTMLElement).style.background).toBe(
      "var(--nw-4)",
    );
    expect(
      (container.querySelector("[data-color=home]") as HTMLElement).style
        .background,
    ).toBe("var(--nw-4)");
    expect(container.querySelector("[data-color=cash]")).toBeNull();
  });

  it("names an account's kind in words in the list of accounts left out", async () => {
    serve({
      ...nw([], { "30d": null, "90d": null, "1y": null }),
      excluded: [
        { id: "x", name: "Old card", org: null, kind: "credit", balance: 20 },
      ],
    });
    render(NetWorth);
    await userEvent.click(await screen.findByRole("button", { name: "Show" }));
    expect(screen.getByText(/· Credit card/)).toBeInTheDocument();
  });
});

describe("a reload that fails", () => {
  it("keeps the numbers under a Couldn't refresh line, and Retry brings the new ones", async () => {
    const acct = {
      type: "account",
      id: "a1",
      name: "Checking",
      value: 5500,
      org: "Bank",
    };
    const first = {
      ...nw([], { "30d": null, "90d": null, "1y": null }),
      groups: [
        {
          key: "cash",
          label: "Cash",
          side: "asset",
          total: 5500,
          items: [acct],
        },
      ],
    };
    let fail = false;
    vi.mocked(api).mockImplementation((async (path: string) => {
      if (path === "/api/networth") {
        if (fail) throw new Error("Server down");
        return first;
      }
      return {};
    }) as never);
    render(NetWorth);
    await screen.findByTestId("nw-headline");
    expect(screen.queryByTestId("refresh-failed")).toBeNull();
    fail = true;
    await userEvent.click(
      screen.getByRole("button", { name: "Checking, $5,500.00" }),
    );
    await userEvent.click(
      await screen.findByRole("switch", { name: "Count in net worth" }),
    );
    const banner = await screen.findByTestId("refresh-failed");
    expect(banner).toHaveTextContent(
      "Couldn’t refresh · showing earlier numbers",
    );
    expect(banner).toHaveAttribute("title", "Server down");
    expect(screen.getByTestId("nw-headline")).toHaveTextContent("$5,000");
    fail = false;
    await fireEvent.click(
      within(banner).getByRole("button", { name: "Retry" }),
    );
    await waitFor(() =>
      expect(screen.queryByTestId("refresh-failed")).toBeNull(),
    );
  });
});
