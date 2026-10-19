// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn() }));

import { app } from "$lib/app.svelte";
import type { Overview } from "$lib/types";
import BalanceCard from "./BalanceCard.svelte";

afterEach(() => cleanup());

const fc = (balance: number) => ({
  today: "2026-09-30", dates: ["2026-09-30"], total: [balance], low: { date: "2026-09-30", balance },
  accounts: [{ id: "chk", name: "Everyday Checking", kind: "checking", balance }],
  events: [], cards: [], unlinked_cards: [], warnings: [], warning_links: [], budget: null,
}) as unknown as Overview;

describe("BalanceCard", () => {
  it("shows the balance headline in whole dollars, with no cents element", () => {
    app.state = { connected: true };
    render(BalanceCard, { fc: fc(4820.55), days: 30, shownDays: 30, setDays: vi.fn(), onchange: vi.fn() });
    const total = screen.getByTestId("balance-total");
    expect(total).toHaveTextContent(/^\$4,821$/);
    expect(total.querySelector("span")).toBeNull();
  });
});
