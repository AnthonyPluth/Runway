// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/categories.svelte", () => {
  const list = [{ name: "Travel", parent: null }, { name: "Hotels", parent: "Travel" }, { name: "Groceries", parent: null }, { name: "Trips", parent: null }];
  return { loadCategories: vi.fn(async () => {}), categories: { list }, catLabel: (c: { name: string }) => c.name,
    categoryGroups: () => [{ label: "Spending", items: list }], catParentOf: (n: string) => list.find((c) => c.name === n)?.parent ?? null };
});

import { api } from "$lib/api";
import BestCard from "./BestCard.svelte";
import { calls } from "./fixtures";

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({ ok: true } as never);
});

describe("best card", () => {
  it("asks for portal rates when you'll book through the portal, and shows the portal note", async () => {
    const row = { id: 1, owner: "Alex", product: "Venture X", issuer: "capital_one", multiplier: 10, currency: "Capital One miles", cents: 1.4,
      return_pct: 14, value: null, bonus: null, needs_portal: true, portal_name: "Capital One Travel", portal_option: null,
      note: "Only when booked through Capital One Travel" };
    vi.mocked(api).mockImplementation((async (path: string) => ({ cards: [path.includes("portal=1") ? row : { ...row, multiplier: 2, needs_portal: false, note: null }] })) as never);
    render(BestCard, { person: "", version: 0, showOwner: false });
    await screen.findByText("Venture X");
    await userEvent.selectOptions(screen.getByLabelText("Category of the purchase"), "Hotels");   // under Travel
    expect(calls(/best\?/).at(-1)![0]).not.toContain("portal=1");
    await userEvent.click(screen.getByLabelText("I'll book through the issuer's travel portal"));
    expect(await screen.findByText("Only when booked through Capital One Travel")).toBeInTheDocument();
    expect(screen.getByText("Via Capital One Travel")).toBeInTheDocument();
    expect(calls(/best\?/).at(-1)![0]).toContain("portal=1");
  });

  it("only offers the portal for travel, and stops asking for portal rates when you pick something else", async () => {
    vi.mocked(api).mockResolvedValue({ cards: [] } as never);
    render(BestCard, { person: "", version: 0, showOwner: false });
    await screen.findByText("No open cards yet.");
    expect(screen.queryByLabelText("I'll book through the issuer's travel portal")).not.toBeInTheDocument();   // Anything
    await userEvent.selectOptions(screen.getByLabelText("Category of the purchase"), "Travel");
    await userEvent.click(screen.getByLabelText("I'll book through the issuer's travel portal"));
    await waitFor(() => expect(calls(/best\?/).at(-1)![0]).toContain("portal=1"));
    await userEvent.selectOptions(screen.getByLabelText("Category of the purchase"), "Groceries");
    expect(screen.queryByLabelText("I'll book through the issuer's travel portal")).not.toBeInTheDocument();
    await waitFor(() => expect(calls(/best\?/).at(-1)![0]).toContain("category=Groceries"));
    expect(calls(/best\?/).at(-1)![0]).not.toContain("portal=1");
  });

  it("offers the portal for a category a card has a portal-only rate on, whatever it's called", async () => {
    const row = { id: 1, owner: "Alex", product: "Venture X", issuer: "capital_one", multiplier: 2, currency: "miles", cents: 1.4, return_pct: 2.8,
      value: null, bonus: null, needs_portal: false, portal_name: "Capital One Travel",
      portal_option: { multiplier: 10, return_pct: 14, portal_name: "Capital One Travel", value: null, note: "" }, note: null };
    vi.mocked(api).mockImplementation((async (path: string) => ({ cards: [path.includes("category=Trips") ? row : { ...row, portal_option: null }] })) as never);
    render(BestCard, { person: "", version: 0, showOwner: false });
    await screen.findByText("Venture X");
    await userEvent.selectOptions(screen.getByLabelText("Category of the purchase"), "Trips");
    expect(await screen.findByLabelText("I'll book through the issuer's travel portal")).toBeInTheDocument();
  });

  it("offers the better portal rate when portal isn't ticked", async () => {
    const row = { id: 1, owner: "Alex", product: "Venture X", issuer: "capital_one", multiplier: 2, currency: "miles", cents: 1.4, return_pct: 2.8,
      value: null, bonus: null, needs_portal: false, portal_name: "Capital One Travel",
      portal_option: { multiplier: 10, return_pct: 14, portal_name: "Capital One Travel", value: null, note: "" }, note: "10x if booked through Capital One Travel" };
    vi.mocked(api).mockResolvedValue({ cards: [row] } as never);
    render(BestCard, { person: "", version: 0, showOwner: false });
    expect(await screen.findByText("10x if booked through Capital One Travel")).toBeInTheDocument();
  });
});

describe("asking for the ranking", () => {
  const rank = (product: string, over = {}) => ({ id: 1, owner: "Alex", product, issuer: "chase", multiplier: 3, currency: "points", cents: 1.5, return_pct: 4.5, value: null,
    bonus: null, needs_portal: false, portal_name: null, portal_option: null, note: null, ...over });
  const deferred = <T,>() => { let resolve!: (v: T) => void, reject!: (e: Error) => void; const promise = new Promise<T>((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
  afterEach(() => { vi.useRealTimers(); });

  it("waits for a pause of 300ms in typing before asking again", async () => {
    vi.useFakeTimers();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    vi.mocked(api).mockResolvedValue({ cards: [rank("Freedom")] } as never);
    render(BestCard, { person: "", version: 0, showOwner: false });
    await vi.advanceTimersByTimeAsync(0);
    expect(calls(/best\?/)).toHaveLength(1);   // the first ask goes at once
    await user.type(screen.getByPlaceholderText("optional"), "250");
    await vi.advanceTimersByTimeAsync(299);
    expect(calls(/best\?/)).toHaveLength(1);   // still typing, so nothing yet
    await user.type(screen.getByPlaceholderText("optional"), "0");   // another key restarts the wait
    await vi.advanceTimersByTimeAsync(299);
    expect(calls(/best\?/)).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls(/best\?/)).toHaveLength(2);
    expect(calls(/best\?/)[1][0]).toContain("amount=2500");
  });

  it("asks at once when the page's data was reloaded", async () => {
    vi.useFakeTimers();
    vi.mocked(api).mockResolvedValue({ cards: [rank("Freedom")] } as never);
    const { rerender } = render(BestCard, { person: "", version: 0, showOwner: false });
    await vi.advanceTimersByTimeAsync(0);
    await rerender({ person: "", version: 1, showOwner: false });
    await vi.advanceTimersByTimeAsync(0);
    expect(calls(/best\?/)).toHaveLength(2);
  });

  it("ignores an old answer that arrives after a newer one", async () => {
    vi.useFakeTimers();
    const first = deferred<{ cards: unknown[] }>(), second = deferred<{ cards: unknown[] }>();
    vi.mocked(api).mockReturnValueOnce(first.promise as never).mockReturnValueOnce(second.promise as never);
    render(BestCard, { person: "", version: 0, showOwner: false });
    await vi.advanceTimersByTimeAsync(0);
    screen.getByPlaceholderText("optional").focus();
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).type(screen.getByPlaceholderText("optional"), "9");
    await vi.advanceTimersByTimeAsync(300);
    expect(calls(/best\?/)).toHaveLength(2);
    second.resolve({ cards: [rank("Newer answer")] });
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByText("Newer answer")).toBeInTheDocument();
    first.resolve({ cards: [rank("Older answer")] });
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.queryByText("Older answer")).toBeNull();
    expect(screen.getByText("Newer answer")).toBeInTheDocument();
  });

  it("shows a skeleton while the first answer loads, and dims the list while a newer one does", async () => {
    vi.useFakeTimers();
    const first = deferred<{ cards: unknown[] }>(), second = deferred<{ cards: unknown[] }>();
    vi.mocked(api).mockReturnValueOnce(first.promise as never).mockReturnValueOnce(second.promise as never);
    const { rerender } = render(BestCard, { person: "", version: 0, showOwner: false });
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByLabelText("Ranking your cards")).toHaveAttribute("aria-busy", "true");
    first.resolve({ cards: [rank("Freedom")] });
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.queryByLabelText("Ranking your cards")).toBeNull();
    const list = screen.getByRole("list");
    expect(list).toHaveAttribute("aria-busy", "false");
    await rerender({ person: "", version: 1, showOwner: false });
    await vi.advanceTimersByTimeAsync(0);
    expect(list).toHaveAttribute("aria-busy", "true");
    expect(list.className).toContain("opacity-60");
    second.resolve({ cards: [rank("Freedom")] });
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByRole("list")).toHaveAttribute("aria-busy", "false");
  });

  it("says in a friendly line that it couldn't rank, never the raw error, and Try again asks again", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("HTTP 500: internal error at /api/churning/best"));
    render(BestCard, { person: "", version: 0, showOwner: false });
    expect(await screen.findByText(/Couldn’t rank your cards just now/)).toBeInTheDocument();
    expect(screen.queryByText(/HTTP 500/)).toBeNull();
    vi.mocked(api).mockResolvedValue({ cards: [rank("Freedom")] } as never);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Freedom")).toBeInTheDocument();
    expect(screen.queryByText(/Couldn’t rank/)).toBeNull();
  });

  it("writes the money in a row one way, to the cent", async () => {
    vi.mocked(api).mockResolvedValue({ cards: [rank("Freedom", { value: 1.5, bonus: { remaining: 1250.5, deadline: "2026-12-01", amount: 200, currency: "cash" } })] } as never);
    render(BestCard, { person: "", version: 0, showOwner: false, today: "2026-09-30" });
    const row = (await screen.findByRole("list")).querySelector("li")!;
    expect(row).toHaveTextContent("bonus: $1,250.50 to go");
    expect(row).toHaveTextContent("$1.50");
  });
});
