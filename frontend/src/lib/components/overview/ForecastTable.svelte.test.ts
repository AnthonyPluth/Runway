// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Overview } from "$lib/types";
import ForecastTable from "./ForecastTable.svelte";

const fc = (charged: number, assumed: boolean) => ({
  dates: ["2026-10-25", "2026-10-26"], total: [1000, 1000], events: [], low: { date: "2026-10-25", balance: 1000 },
  accounts: [{ id: "chk", name: "Checking", balance: 1000, daily_spend: 0 }], cards: [],
  budget: {
    total: [1000, 650], low: { date: "2026-10-26", balance: 650 }, used: [], skipped: [], monthly: 310,
    changes: [{ date: "2026-10-26", account_id: "chk", kind: "card", name: "New card statement", amount: -350, charged, assumed_cycle: assumed }],
  },
}) as unknown as Overview;

describe("ForecastTable", () => {
  it("says when a card's budgeted statement is on an assumed cycle", () => {
    render(ForecastTable, { fc: fc(40, true) });
    expect(screen.getByText(/New card statement \(budgeted\)/).closest("[title]")).toHaveAttribute("title",
      "$40.00 already on the card, the rest budgeted spending · no statement yet, so taken to close at the month’s end and be paid 25 days later");
  });

  it("says nothing about the cycle for a card with a statement", () => {
    render(ForecastTable, { fc: fc(0, false) });
    expect(screen.getByText(/New card statement \(budgeted\)/).closest("[title]")).toHaveAttribute("title", "budgeted spending");
  });

  it("breaks a day's budgeted spending down by budget when you tap it", async () => {
    const f = fc(0, false);
    f.budget!.changes = [
      { date: "2026-10-26", account_id: "chk", kind: "budget", name: "Dining", amount: -10 },
      { date: "2026-10-26", account_id: "chk", kind: "budget", name: "Groceries", amount: -25.5 },
    ];
    render(ForecastTable, { fc: f });
    await userEvent.click(screen.getByRole("button", { name: "Budgeted spending" }));
    const pop = (await waitFor(() => screen.getByText(/What’s left of each budget this month, spread evenly over the days left in it\./))).parentElement!;
    expect([...pop.querySelectorAll("li")].map((li) => li.textContent)).toEqual(["Groceries$25.50", "Dining$10.00"]);
    expect(pop).toHaveTextContent("That day$35.50");
  });

  it("shows a card payment both lines make alike once, with no badge, and keeps different ones apart", () => {
    const f = fc(0, false);
    f.total = [1000, 650];
    f.budget!.total = [1000, 640];   // the lines differ (by other budgeted spending), so the budget column shows
    f.events = [{ date: "2026-10-26", account_id: "chk", kind: "card", name: "New card statement", amount: -350, estimated: true }] as Overview["events"];
    const { unmount } = render(ForecastTable, { fc: f });
    expect(screen.getAllByText(/New card statement/)).toHaveLength(1);
    expect(screen.getByText(/New card statement \(estimate\)/)).toBeInTheDocument();
    expect(screen.queryByText("forecast only")).not.toBeInTheDocument();
    expect(screen.queryByText("budget only")).not.toBeInTheDocument();
    unmount();
    // an amount you changed for the forecast's payment: no longer the same, so one of each
    f.events = [{ ...f.events[0], amount: -400 }];
    render(ForecastTable, { fc: f });
    expect(screen.getAllByText(/New card statement/)).toHaveLength(2);
    expect(screen.getByText("forecast only")).toBeInTheDocument();
    expect(screen.getByText("budget only")).toBeInTheDocument();
  });
});
