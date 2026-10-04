// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));

import { api } from "$lib/api";
import { toast } from "svelte-sonner";
import { bodyOf, calls, churning } from "./fixtures";
import Rewards from "./Rewards.svelte";
import type { Churning, RewardRow } from "./types";

const row = (over: Partial<RewardRow>): RewardRow => ({
  currency: "chase_ur",
  name: "Chase Ultimate Rewards",
  earned: 0,
  bonuses: 0,
  balance: null,
  cents: 1.5,
  value: 0,
  balance_value: null,
  ...over,
});
const setup = (rows: RewardRow[]) => {
  const d = {
    people: ["Alex"],
    currencies: [],
    currency_groups: [],
    rewards: {
      Alex: {
        currencies: rows,
        value: rows.reduce((a, r) => a + r.value, 0),
        balance_value: rows.reduce((a, r) => a + (r.balance_value ?? 0), 0),
      },
    },
  } as unknown as Churning;
  render(Rewards, { d, people: ["Alex"], onchanged: vi.fn() });
};

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(toast).mockReset();
  vi.mocked(toast.error).mockReset();
});

describe("Rewards", () => {
  it("keeps this year's earnings out of the balances view", () => {
    setup([
      row({
        currency: "amex_mr",
        name: "Amex Membership Rewards",
        earned: 4183,
        value: 62.75,
      }),
    ]);
    const r = screen.getByText("Amex Membership Rewards").closest("tr")!;
    expect(within(r).queryByText("~4,183")).not.toBeInTheDocument();
    expect(within(r).queryByText("~$63")).not.toBeInTheDocument();
    expect(within(r).getByText("—", { selector: "td" })).toBeInTheDocument();
  });

  it("shows what was earned this year, and what it's worth, in its own view", async () => {
    setup([
      row({
        currency: "amex_mr",
        name: "Amex Membership Rewards",
        earned: 4183,
        bonuses: 60000,
        value: 962.75,
      }),
      row({
        currency: "airline",
        name: "Other airline miles",
        balance: 10,
        balance_value: 0.1,
      }),
    ]);
    await userEvent.click(
      screen.getByRole("radio", { name: "Earned this year" }),
    );
    const r = screen.getByText("Amex Membership Rewards").closest("tr")!;
    expect(within(r).getByText("~4,183")).toBeInTheDocument();
    expect(within(r).getByText("60,000")).toBeInTheDocument();
    expect(within(r).getByText("~$963")).toBeInTheDocument();
    expect(screen.queryByText("Other airline miles")).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("Alex's Amex Membership Rewards balance"),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: "Balances" }));
    expect(
      screen.getByLabelText("Alex's Amex Membership Rewards balance"),
    ).toBeInTheDocument();
  });

  it("shows no table when nothing was earned this year, and no placeholder either", async () => {
    setup([row({ balance: 5, balance_value: 0.08 })]);
    await userEvent.click(
      screen.getByRole("radio", { name: "Earned this year" }),
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Nothing earned yet this year."),
    ).not.toBeInTheDocument();
  });

  it("shows the worth of the balance alone when there's also a year's earnings", async () => {
    setup([
      row({
        earned: 5000,
        bonuses: 60000,
        value: 975,
        balance: 142000,
        balance_value: 2130,
      }),
    ]);
    const r = screen.getByText("Chase Ultimate Rewards").closest("tr")!;
    expect(within(r).getByText("$2,130")).toBeInTheDocument();
    expect(screen.queryByText("$3,105")).not.toBeInTheDocument();
    expect(
      screen.getByTitle("What the balances you entered are worth"),
    ).toHaveTextContent("$2,130");
    await userEvent.click(
      screen.getByRole("radio", { name: "Earned this year" }),
    );
    expect(
      screen.getByTitle(/What this year's points and bonuses are worth/),
    ).toHaveTextContent("$975");
  });

  it("lets a balance change by one point", async () => {
    setup([row({ balance: 100, balance_value: 1.5 })]);
    const box = screen.getByLabelText("Alex's Chase Ultimate Rewards balance");
    await userEvent.click(box);
    expect(box).toHaveAttribute("type", "number");
    expect(box).toHaveAttribute("step", "1");
  });

  it("shows a balance with its commas, and edits it as a number", async () => {
    setup([row({ balance: 142000, balance_value: 2130 })]);
    const box = screen.getByLabelText("Alex's Chase Ultimate Rewards balance");
    expect(box).toHaveValue("142,000");
    await userEvent.click(box);
    expect(box).toHaveValue(142000);
    await userEvent.tab();
    expect(box).toHaveValue("142,000");
  });

  it("takes a balance off the list, even one that is 0", async () => {
    vi.mocked(api).mockResolvedValue({});
    setup([
      row({
        currency: "airline",
        name: "Other airline miles",
        balance: 0,
        balance_value: 0,
        as_of: "2026-09-01",
      }),
    ]);
    await userEvent.click(
      screen.getByRole("button", { name: "Other airline miles details" }),
    );
    await userEvent.click(
      screen.getByRole("button", {
        name: "Remove Alex's Other airline miles balance",
      }),
    );
    await vi.waitFor(() =>
      expect(api).toHaveBeenCalledWith("/api/churning/balances", {
        method: "POST",
        body: { owner: "Alex", currency: "airline", points: null },
      }),
    );
  });

  it("says a balance was removed with an Undo that puts back the same points and day", async () => {
    vi.mocked(api).mockResolvedValue({});
    setup([
      row({
        currency: "airline",
        name: "Other airline miles",
        balance: 41250.5,
        balance_value: 12,
        as_of: "2026-08-30",
      }),
    ]);
    await userEvent.click(
      screen.getByRole("button", { name: "Other airline miles details" }),
    );
    await userEvent.click(
      screen.getByRole("button", {
        name: "Remove Alex's Other airline miles balance",
      }),
    );
    await waitFor(() =>
      expect(calls("/api/churning/balances")).toHaveLength(1),
    );
    const [msg, opts] = vi.mocked(toast).mock.calls.at(-1)! as [
      string,
      { action: { label: string; onClick: () => void } },
    ];
    expect(msg).toBe("Removed Alex’s Other airline miles balance");
    expect(opts.action.label).toBe("Undo");
    opts.action.onClick();
    await waitFor(() =>
      expect(bodyOf(calls("/api/churning/balances")[1])).toEqual({
        owner: "Alex",
        currency: "airline",
        points: 41250.5,
        as_of: "2026-08-30",
      }),
    );
  });

  it("shows a toast instead of an unhandled rejection when removing a balance fails, and offers no undo", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Unknown currency"));
    setup([
      row({
        currency: "airline",
        name: "Other airline miles",
        balance: 100,
        balance_value: 1,
        as_of: "2026-09-01",
      }),
    ]);
    await userEvent.click(
      screen.getByRole("button", { name: "Other airline miles details" }),
    );
    await userEvent.click(
      screen.getByRole("button", {
        name: "Remove Alex's Other airline miles balance",
      }),
    );
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("Unknown currency"),
    );
    expect(toast).not.toHaveBeenCalled();
  });

  it("lays the earned table out as stacked cells below xl, with each figure labeled", async () => {
    setup([
      row({
        currency: "amex_mr",
        name: "Amex Membership Rewards",
        earned: 4183,
        bonuses: 60000,
        value: 962.75,
      }),
    ]);
    await userEvent.click(
      screen.getByRole("radio", { name: "Earned this year" }),
    );
    const r = screen.getByText("Amex Membership Rewards").closest("tr")!;
    expect(r.className).toContain("max-xl:grid");
    expect(
      [...r.querySelectorAll("td[data-label]")].map((td) =>
        td.getAttribute("data-label"),
      ),
    ).toEqual(["Spending", "Bonuses", "Total"]);
    expect(
      screen.getByRole("table").querySelector("thead")!.className,
    ).toContain("max-xl:hidden");
  });
});

describe("rewards", () => {
  beforeEach(() => {
    vi.mocked(api).mockResolvedValue({ ok: true } as never);
  });
  const row = {
    currency: "aa",
    name: "American AAdvantage",
    earned: 0,
    bonuses: 0,
    balance: 40000,
    as_of: "2026-08-30",
    cents: 1.4,
    value: 0,
    balance_value: 560,
    earned_since: 1300,
    est_balance: 41300,
    est_value: 578.2,
  };
  const setup = () => {
    const d = churning({
      rewards: {
        Alex: { currencies: [row], value: 0, balance_value: 560 },
        Sam: { currencies: [], value: 0, balance_value: 0 },
      },
    });
    render(Rewards, { d, people: ["Alex"], onchanged: vi.fn() });
  };

  it("folds the day, the estimate and Remove behind the row, and shows the balance's day (no date to pick) and a labeled estimate of the balance now", async () => {
    setup();
    expect(screen.queryByText(/Estimated now/)).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "American AAdvantage details" }),
    );
    expect(screen.getByText("Entered Aug 30")).toBeInTheDocument();
    expect(document.querySelector('input[type="date"]')).toBeNull();
    expect(screen.getByText(/Estimated now/)).toHaveTextContent("~41,300");
    expect(screen.getByText(/1,300 earned since/)).toBeInTheDocument();
    expect(screen.getByText("$560", { selector: "td" })).toBeInTheDocument();
  });

  it("saves a balance as of today", async () => {
    setup();
    const box = screen.getByLabelText("Alex's American AAdvantage balance");
    await userEvent.clear(box);
    await userEvent.type(box, "42000");
    await userEvent.tab();
    await waitFor(() =>
      expect(bodyOf(calls("/api/churning/balances")[0])).toEqual({
        owner: "Alex",
        currency: "aa",
        points: "42000",
        as_of: "2026-09-30",
      }),
    );
  });

  it("asks before deleting a currency of your own, which takes its balances with it", async () => {
    const d = churning({
      currencies: [
        {
          key: "x-bilt",
          name: "Bilt",
          kind: "other",
          cents: 2,
          default: null,
          custom: true,
          overridden: false,
          estimate: false,
          as_of: null,
          source_note: "",
        },
      ],
      currency_groups: [],
      rewards: { Alex: { currencies: [], value: 0, balance_value: 0 } },
    } as never);
    render(Rewards, { d, people: ["Alex"], onchanged: vi.fn() });
    await userEvent.click(screen.getByText("Point values"));
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete Bilt?" });
    expect(dialog).toHaveTextContent("balances");
    expect(calls(/currencies\/.*remove/)).toHaveLength(0);
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Delete" }),
    );
    await waitFor(() =>
      expect(calls("/api/churning/currencies/x-bilt/remove")).toHaveLength(1),
    );
  });

  it("has point-value inputs at least 13px, and 36px tall on a phone", async () => {
    setup();
    await userEvent.click(screen.getByText("Point values"));
    const input = screen.getByLabelText("Cents a American AAdvantage point");
    expect(input.className).toContain("text-[13px]");
    expect(input.className).toContain("phone:h-9");
  });

  it("groups the point values and marks which are estimates and which are yours", async () => {
    setup();
    await userEvent.click(screen.getByText("Point values"));
    expect(screen.getByText("Airline miles")).toBeInTheDocument();
    expect(screen.getByText("your value")).toBeInTheDocument();
    expect(
      screen.getAllByText(/estimate \(as of Jun 2026\)/).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText(/Estimates, not official values/)).toBeNull();
  });
});
