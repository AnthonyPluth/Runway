// @vitest-environment jsdom
import { fireEvent, render, screen, within } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("$lib/filters.svelte", async (orig) => ({ ...(await orig<typeof import("$lib/filters.svelte")>()), showTransactions: vi.fn() }));

import { api } from "$lib/api";
import { categories } from "$lib/categories.svelte";
import { showTransactions } from "$lib/filters.svelte";
import type { Category } from "$lib/types";
import MerchantDetail from "./MerchantDetail.svelte";
import Merchants from "./Merchants.svelte";
import { reportState } from "./state.svelte";
import type { MerchantReport, MerchantsReport } from "./types";

const list = (merchants: MerchantsReport["merchants"], extra: Partial<MerchantsReport> = {}): MerchantsReport => ({
  start: "2026-07-01", end: "2026-10-01", count: merchants.length, total: merchants.reduce((a, m) => a + m.total, 0), merchants, ...extra,
});
const shop = { name: "Corner Shop", total: 120.6, count: 3, average: 40.2, last: "2026-09-20", category: "Groceries" };
const one: MerchantReport = { name: "Corner Shop", months: ["2026-08", "2026-09"], values: [80, 40], total: 120,
  transactions: [{ id: "t1", posted: "2026-09-20", amount: -40.25, category: "Groceries", account_name: "Card", description: "CORNER SHOP" }] };

beforeEach(() => {
  vi.mocked(api).mockReset(); vi.mocked(showTransactions).mockReset();
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
  vi.setSystemTime(new Date("2026-09-25T12:00:00"));
  Object.assign(reportState, { range: "3m", merchantQ: "", merchantsOpen: [] });
  categories.list = [{ name: "Groceries", color: "#008300", icon: "🛒" }] as Category[];
});
afterEach(() => { vi.useRealTimers(); categories.list = []; });

describe("Merchants", () => {
  it("lists merchants in whole dollars, the usual category with its emoji, and the period's total under the table", async () => {
    vi.mocked(api).mockResolvedValue(list([shop], { count: 140, total: 9071.4 }) as never);
    render(Merchants);
    const row = await screen.findByRole("row", { name: /Corner Shop/ });
    expect(row).toHaveTextContent("🛒 Groceries");
    expect(row).toHaveTextContent("$121");
    expect(row).not.toHaveTextContent("$120.60");
    expect(screen.getByRole("columnheader", { name: "Money out" })).toBeInTheDocument();
    expect(screen.getByText("The top 1 of 140 merchants")).toBeInTheDocument();
    expect(screen.getByText("$9,071")).toBeInTheDocument();
  });

  it("searches on the server, after a pause in typing", async () => {
    vi.mocked(api).mockResolvedValue(list([shop]) as never);
    render(Merchants);
    await screen.findByRole("row", { name: /Corner Shop/ });
    await fireEvent.input(screen.getByRole("searchbox", { name: "Find a merchant" }), { target: { value: "corn" } });
    expect(api).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(300);
    expect(api).toHaveBeenLastCalledWith("/api/reports/merchants?start=2026-07-01&end=2026-10-01&q=corn");
    expect(reportState.merchantQ).toBe("corn");
  });

  it("says when nothing matches, with a way to clear the search", async () => {
    reportState.merchantQ = "zzz";
    vi.mocked(api).mockResolvedValue(list([]) as never);
    render(Merchants);
    expect(await screen.findByText(/No merchants match/)).toBeInTheDocument();
    expect(screen.queryByText("No spending in this period.")).not.toBeInTheDocument();
    vi.mocked(api).mockResolvedValue(list([shop]) as never);
    await fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    expect(api).toHaveBeenLastCalledWith("/api/reports/merchants?start=2026-07-01&end=2026-10-01");
    expect(await screen.findByRole("row", { name: /Corner Shop/ })).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Find a merchant" })).toHaveValue("");
  });

  it("keeps the search and what's open when you come back to the tab", async () => {
    vi.mocked(api).mockImplementation(((url: string) => Promise.resolve(url.includes("/merchant?") ? one : list([shop]))) as never);
    const first = render(Merchants);
    await fireEvent.input(screen.getByRole("searchbox", { name: "Find a merchant" }), { target: { value: "corner" } });
    await vi.advanceTimersByTimeAsync(300);
    await fireEvent.click(await screen.findByRole("button", { name: "Corner Shop" }));
    first.unmount();
    render(Merchants);
    expect(screen.getByRole("searchbox", { name: "Find a merchant" })).toHaveValue("corner");
    expect(await screen.findByRole("button", { name: "Corner Shop" })).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByText("All its transactions")).toBeInTheDocument();
  });

  it("says when the period has no spending", async () => {
    vi.mocked(api).mockResolvedValue(list([]) as never);
    render(Merchants);
    expect(await screen.findByText("No spending in this period.")).toBeInTheDocument();
  });
});

describe("one merchant", () => {
  const period = { from: "2026-07-01", to: "2026-09-30" };

  it("opens all its transactions for the period the table shows", async () => {
    vi.mocked(api).mockResolvedValue(one as never);
    render(MerchantDetail, { name: "Corner Shop", period });
    await fireEvent.click(await screen.findByRole("button", { name: "All its transactions" }));
    expect(showTransactions).toHaveBeenCalledWith({ scope: "budget", q: "Corner Shop", from: "2026-07-01", to: "2026-09-30" });
  });

  it("opens a row's day in Transactions, its amount to the cent", async () => {
    vi.mocked(api).mockResolvedValue(one as never);
    render(MerchantDetail, { name: "Corner Shop", period });
    const row = await screen.findByRole("row", { name: /Sep\s20/ });
    expect(row).toHaveTextContent("$40.25");
    await fireEvent.click(within(row).getByRole("button"));
    expect(showTransactions).toHaveBeenCalledWith({ scope: "budget", q: "Corner Shop", from: "2026-09-20", to: "2026-09-20" });
  });

  it("says when it couldn't load, and tries again", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(api).mockRejectedValue(new Error("Offline"));
    render(MerchantDetail, { name: "Corner Shop", period });
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t load Corner Shop.");
    vi.mocked(api).mockResolvedValue(one as never);
    await fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("$120 in the last 12 months")).toBeInTheDocument();
  });
});
