// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { bodyOf, calls, churning } from "./fixtures";
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
  it("keeps this year's earnings out of the balances view", () => {
    // ~4,183 points earned (worth $63 at 1.5¢) but no balance entered: nothing to value in Balances.
    setup([row({ currency: "amex_mr", name: "Amex Membership Rewards", earned: 4183, value: 62.75 })]);
    const r = screen.getByText("Amex Membership Rewards").closest("tr")!;
    expect(within(r).queryByText("~4,183")).not.toBeInTheDocument();
    expect(within(r).queryByText("~$63")).not.toBeInTheDocument();
    expect(within(r).getByText("—", { selector: "td" })).toBeInTheDocument();
  });

  it("shows what was earned this year, and what it's worth, in its own view", async () => {
    setup([row({ currency: "amex_mr", name: "Amex Membership Rewards", earned: 4183, bonuses: 60000, value: 962.75 }),
      row({ currency: "airline", name: "Other airline miles", balance: 10, balance_value: 0.1 })]);
    await userEvent.click(screen.getByRole("radio", { name: "Earned this year" }));
    const r = screen.getByText("Amex Membership Rewards").closest("tr")!;
    expect(within(r).getByText("~4,183")).toBeInTheDocument();         // spending
    expect(within(r).getByText("60k")).toBeInTheDocument();            // bonuses
    expect(within(r).getByText("~$963")).toBeInTheDocument();          // what that's worth
    expect(screen.queryByText("Other airline miles")).not.toBeInTheDocument();   // earned nothing this year
    expect(screen.queryByLabelText("Alex's Amex Membership Rewards balance")).not.toBeInTheDocument();   // no balances here
    await userEvent.click(screen.getByRole("radio", { name: "Balances" }));
    expect(screen.getByLabelText("Alex's Amex Membership Rewards balance")).toBeInTheDocument();
  });

  it("says so when nothing was earned this year", async () => {
    setup([row({ balance: 5, balance_value: 0.08 })]);
    await userEvent.click(screen.getByRole("radio", { name: "Earned this year" }));
    expect(screen.getByText("Nothing earned yet this year.")).toBeInTheDocument();
  });

  it("shows the worth of the balance alone when there's also a year's earnings", async () => {
    setup([row({ earned: 5000, bonuses: 60000, value: 975, balance: 142000, balance_value: 2130 })]);
    const r = screen.getByText("Chase Ultimate Rewards").closest("tr")!;
    expect(within(r).getByText("$2,130")).toBeInTheDocument();
    expect(screen.queryByText("$3,105")).not.toBeInTheDocument();   // balance + earnings
    expect(screen.getByTitle("What the balances you entered are worth")).toHaveTextContent("$2,130");
    await userEvent.click(screen.getByRole("radio", { name: "Earned this year" }));
    expect(screen.getByTitle(/What this year's points and bonuses are worth/)).toHaveTextContent("$975");
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

describe("rewards", () => {
  beforeEach(() => { vi.mocked(api).mockResolvedValue({ ok: true } as never); });
  const row = { currency: "aa", name: "American AAdvantage", earned: 0, bonuses: 0, balance: 40000, as_of: "2026-08-30", cents: 1.4, value: 0,
    balance_value: 560, earned_since: 1300, est_balance: 41300, est_value: 578.2 };
  const setup = () => {
    const d = churning({ rewards: { Alex: { currencies: [row], value: 0, balance_value: 560 }, Sam: { currencies: [], value: 0, balance_value: 0 } } });
    render(Rewards, { d, people: ["Alex"], onchanged: vi.fn() });
  };

  it("shows the balance as of its day and a labeled estimate of the balance now", () => {
    setup();
    expect(screen.getByLabelText("Day of Alex's American AAdvantage balance")).toHaveValue("2026-08-30");
    expect(screen.getByText(/Estimated now/)).toHaveTextContent("~41,300");
    expect(screen.getByText(/1,300 earned since/)).toBeInTheDocument();
    expect(screen.getByText("$560", { selector: "td" })).toBeInTheDocument();   // Worth stays the balance you entered
  });

  it("saves a new balance as of today unless you set its day", async () => {
    setup();
    const box = screen.getByLabelText("Alex's American AAdvantage balance");
    await userEvent.clear(box);
    await userEvent.type(box, "42000");
    await userEvent.tab();
    await waitFor(() => expect(bodyOf(calls("/api/churning/balances")[0])).toEqual({ owner: "Alex", currency: "aa", points: "42000", as_of: "2026-09-30" }));
  });

  it("groups the point values and says which are estimates and which are yours", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Point values" }));
    expect(screen.getByText("Airline miles")).toBeInTheDocument();
    expect(screen.getByText("your value")).toBeInTheDocument();      // AA is overridden
    expect(screen.getAllByText(/estimate \(as of Jun 2026\)/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Estimates, not official values/)).toBeInTheDocument();
  });
});
