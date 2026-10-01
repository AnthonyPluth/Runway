// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import type { Overview } from "$lib/types";
import ForecastTable from "./ForecastTable.svelte";

const fc = (charged: number, assumed: boolean) => ({
  dates: ["2026-10-25", "2026-10-26"], total: [1000, 1000], events: [], low: { date: "2026-10-25", balance: 1000 },
  accounts: [{ id: "chk", name: "Checking", balance: 1000, daily_spend: 0 }], cards: [],
  budget: {
    total: [1000, 650], low: { date: "2026-10-26", balance: 650 }, used: [], skipped: [], monthly: 310,
    changes: [{ date: "2026-10-26", account_id: "chk", kind: "card", name: "Double Cash statement", amount: -350, charged, assumed_cycle: assumed }],
  },
}) as unknown as Overview;

describe("ForecastTable", () => {
  it("says when a card's budgeted statement is on an assumed cycle", () => {
    render(ForecastTable, { fc: fc(40, true) });
    expect(screen.getByText("Double Cash statement (budgeted)")).toBeInTheDocument();
    expect(screen.getByText(/\$40\.00 already on the card, the rest budgeted spending · no statement yet, so taken to close at the month’s end and be paid 25 days later/)).toBeInTheDocument();
  });

  it("says nothing about the cycle for a card with a statement", () => {
    render(ForecastTable, { fc: fc(0, false) });
    expect(screen.getByText("budgeted spending")).toBeInTheDocument();
    expect(screen.queryByText(/no statement yet/)).not.toBeInTheDocument();
  });
});
