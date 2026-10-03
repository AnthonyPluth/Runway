// @vitest-environment jsdom
import { fireEvent, render, screen, within } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("$lib/filters.svelte", async (orig) => ({ ...(await orig<typeof import("$lib/filters.svelte")>()), showTransactions: vi.fn() }));

import { api } from "$lib/api";
import { categories } from "$lib/categories.svelte";
import { showTransactions } from "$lib/filters.svelte";
import { viewport } from "$lib/phone.svelte";
import type { Category } from "$lib/types";
import Breakdown from "./Breakdown.svelte";
import { reportState } from "./state.svelte";
import Treemap from "./Treemap.svelte";
import type { BreakdownReport, ReportTx } from "./types";

const report: BreakdownReport = {
  start: "2026-07-01", end: "2026-10-01",
  tree: { name: "Spending", value: 1000, children: [
    { name: "Groceries", value: 997, children: [{ name: "Corner Shop", value: 997 }, { name: "Market", value: 0.4 }] },
    { name: "Dining", value: 3, children: [{ name: "Taco Place", value: 3 }] },
  ] },
};
const txs: ReportTx[] = [{ id: "t1", posted: "2026-09-02", amount: -12.5, payee: "Taco Place", category: "Dining", account_name: "Card", part: false }];
const respond = (url: string) => Promise.resolve(url.startsWith("/api/reports/transactions") ? txs : report);

beforeEach(() => {
  vi.mocked(api).mockReset(); vi.mocked(showTransactions).mockReset();
  Object.assign(reportState, { range: "3m", path: [] });
  categories.list = [{ name: "Groceries", color: "#008300", icon: "🛒" }, { name: "Dining", color: "#c98500", icon: "🍽️" }] as Category[];
});
afterEach(() => { viewport.phone = false; categories.list = []; });

describe("Breakdown", () => {
  it("lists the blocks in whole dollars and shares that never round a real amount away", async () => {
    vi.mocked(api).mockImplementation(respond as never);
    render(Breakdown);
    await fireEvent.click(await screen.findByText("Show as table"));
    expect(screen.getByRole("row", { name: /Groceries \$997 >99%/ })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /Dining \$3 <1%/ })).toBeInTheDocument();
  });

  it("opens the table at first on a phone", async () => {
    viewport.phone = true;
    vi.mocked(api).mockImplementation(respond as never);
    render(Breakdown);
    expect((await screen.findByText("Show as table")).closest("details")).toHaveAttribute("open");
  });

  it("colors each category as Budget does", async () => {
    vi.mocked(api).mockImplementation(respond as never);
    render(Breakdown);
    const block = await screen.findByRole("button", { name: "Dining: $3" });
    expect(block.querySelector("rect")!.style.fill).toBe("rgb(201, 133, 0)");
  });

  it("opens a transaction at the bottom in Transactions, and the whole block from the header", async () => {
    reportState.path = ["Dining"];
    vi.mocked(api).mockImplementation(respond as never);
    render(Breakdown);
    const row = await screen.findByRole("row", { name: /Taco Place/ });
    expect(row).toHaveTextContent("$12.50");   // a transaction, to the cent
    await fireEvent.click(within(row).getByRole("button"));
    expect(showTransactions).toHaveBeenLastCalledWith({ scope: "budget", from: "2026-09-02", to: "2026-09-02", q: "Taco Place", category: "Dining" });
    await fireEvent.click(screen.getByRole("button", { name: "Transactions" }));
    expect(showTransactions).toHaveBeenLastCalledWith({ scope: "budget", from: "2026-07-01", to: "2026-09-30", q: "Taco Place", category: "Dining" });
  });

  it("says when a block's transactions couldn't load, and tries again", async () => {
    reportState.path = ["Dining"];
    let fail = true;
    vi.mocked(api).mockImplementation(((url: string) => (url.startsWith("/api/reports/transactions") && fail ? Promise.reject(new Error("Offline")) : respond(url))) as never);
    render(Breakdown);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t load these transactions.");
    fail = false;
    await fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("row", { name: /Taco Place/ })).toBeInTheDocument();
  });
});

describe("Treemap", () => {
  const items = [{ name: "Big", value: 990, color: "#008300" }, { name: "Tiny", value: 10, color: "#c98500" }];

  it("writes names in the ink that reads on each block", () => {
    render(Treemap, { items, total: 1000, onpick: () => {} });
    expect(screen.getByText("Big", { selector: "text" })).toHaveAttribute("fill", "#ffffff");
  });

  it("on a touch screen, shows a small unlabeled block's readout first and opens it on the second tap", async () => {
    const onpick = vi.fn();
    render(Treemap, { items, total: 1000, onpick });
    const tiny = screen.getByRole("button", { name: "Tiny: $10" });
    expect(within(tiny).queryByText("Tiny", { selector: "text" })).toBeNull();   // too small for its name
    await fireEvent.pointerDown(tiny, { pointerType: "touch" });
    await fireEvent.click(tiny);
    expect(onpick).not.toHaveBeenCalled();
    expect(document.querySelector(".bg-popover")).toHaveTextContent(/Tiny.*\$10.*of this view.*1%/);
    await fireEvent.pointerDown(tiny, { pointerType: "touch" });
    await fireEvent.click(tiny);
    expect(onpick).toHaveBeenCalledWith(items[1]);
  });

  it("opens a labeled block on the first tap", async () => {
    const onpick = vi.fn();
    render(Treemap, { items, total: 1000, onpick });
    const big = screen.getByRole("button", { name: "Big: $990" });
    await fireEvent.pointerDown(big, { pointerType: "touch" });
    await fireEvent.click(big);
    expect(onpick).toHaveBeenCalledWith(items[0]);
  });
});
