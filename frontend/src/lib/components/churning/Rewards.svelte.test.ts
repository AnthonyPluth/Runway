// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import Rewards from "./Rewards.svelte";
import type { Churning, RewardRow } from "./types";

const row = (over: Partial<RewardRow>): RewardRow => ({
  currency: "chase_ur", name: "Chase Ultimate Rewards", earned: 0, bonuses: 0, balance: null, cents: 1.5, value: 0, balance_value: null, ...over,
});
const setup = (rows: RewardRow[]) => {
  const d = {
    people: ["Alex"], currencies: [], currency_groups: [], rewards: { Alex: { currencies: rows, value: rows.reduce((a, r) => a + r.value, 0),
      balance_value: rows.reduce((a, r) => a + (r.balance_value ?? 0), 0) } },
  } as unknown as Churning;
  render(Rewards, { d, people: ["Alex"], onchanged: vi.fn() });
};

beforeEach(() => { vi.mocked(api).mockReset(); });

describe("Rewards", () => {
  it("values only the balance you entered, not the points earned this year", () => {
    // ~4,183 points earned (worth $63 at 1.5¢) but no balance entered: nothing to value.
    setup([row({ currency: "amex_mr", name: "Amex Membership Rewards", earned: 4183, value: 62.75 })]);
    const r = screen.getByText("Amex Membership Rewards").closest("tr")!;
    expect(within(r).getByText("~4,183")).toBeInTheDocument();
    expect(within(r).queryByText("$63")).not.toBeInTheDocument();
    expect(within(r).getByText("—", { selector: "td" })).toBeInTheDocument();
  });

  it("shows the worth of the balance alone when there's also a year's earnings", () => {
    setup([row({ earned: 5000, bonuses: 60000, value: 975, balance: 142000, balance_value: 2130 })]);
    const r = screen.getByText("Chase Ultimate Rewards").closest("tr")!;
    expect(within(r).getByText("$2,130")).toBeInTheDocument();
    expect(screen.queryByText("$3,105")).not.toBeInTheDocument();   // balance + earnings
    expect(screen.getByTitle("What the balances you entered are worth")).toHaveTextContent("$2,130");
  });

  it("lets a balance change by one point", () => {
    setup([row({ balance: 100, balance_value: 1.5 })]);
    expect(screen.getByLabelText("Alex's Chase Ultimate Rewards balance")).toHaveAttribute("step", "1");
  });

  it("takes a balance off the list, even one that is 0", async () => {
    vi.mocked(api).mockResolvedValue({});
    setup([row({ currency: "airline", name: "Other airline miles", balance: 0, balance_value: 0, as_of: "2026-09-01" })]);
    screen.getByRole("button", { name: "Remove Alex's Other airline miles balance" }).click();
    await vi.waitFor(() => expect(api).toHaveBeenCalledWith("/api/churning/balances",
      { method: "POST", body: { owner: "Alex", currency: "airline", points: null } }));
  });
});
