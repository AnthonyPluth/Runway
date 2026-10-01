// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn().mockResolvedValue({}) }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import type { CardSummary } from "$lib/types";
import { toast } from "svelte-sonner";
import CardsTable from "./CardsTable.svelte";
import { forecastSheet } from "./forecastSheet.svelte";
import SetupChecklist from "./SetupChecklist.svelte";

beforeEach(() => { vi.mocked(api).mockClear(); vi.mocked(toast.error).mockClear(); });
afterEach(() => { app.state = null; forecastSheet.open = false; vi.useRealTimers(); });

describe("SetupChecklist", () => {
  const setup = (s: Partial<NonNullable<typeof app.state>["setup"]> = {}) => {
    app.state = { connected: false, setup: { bank: false, primary: false, recurring: false, budgets: false, dismissed: false, ...s } };
  };

  it("lists four steps with the first one to do highlighted", () => {
    setup();
    render(SetupChecklist);
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByRole("progressbar", { name: "Setup progress" })).toHaveAttribute("aria-valuenow", "0");
    expect(screen.getByText("0 of 4 done")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect" })).toHaveAttribute("href", "#setup/connections");
  });

  it("ticks off finished steps and stops describing them", () => {
    setup({ bank: true, primary: true });
    render(SetupChecklist);
    expect(screen.getByText("2 of 4 done")).toBeInTheDocument();
    expect(screen.getAllByLabelText("Done")).toHaveLength(2);
    expect(screen.queryByRole("link", { name: "Connect" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add" })).toHaveAttribute("href", "#budget/recurring");
    expect(screen.getByRole("link", { name: "Budget" })).toHaveAttribute("href", "#budget");
  });

  it("chooses the main account in the forecast settings, right there on Overview", async () => {
    setup({ bank: true });
    render(SetupChecklist);
    expect(screen.queryByRole("link", { name: "Choose" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Choose" }));
    expect(forecastSheet.open).toBe(true);
  });

  it("can't choose the main account before a bank is connected", () => {
    setup();
    render(SetupChecklist, { welcome: true });
    const choose = screen.getByRole("button", { name: "Choose" });
    expect(choose).toBeDisabled();
    expect(choose).toHaveAccessibleDescription("after your bank connects");
  });

  it("welcomes a new user on its own, without a Dismiss button", () => {
    setup();
    render(SetupChecklist, { welcome: true });
    expect(screen.getByText("Welcome to Runway")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Dismiss" })).not.toBeInTheDocument();
  });

  it("can be put away, remembering that on the server", async () => {
    setup({ bank: true });
    vi.mocked(api).mockResolvedValue({});
    render(SetupChecklist);
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(api).toHaveBeenCalledWith("/api/settings", { method: "POST", body: { setup_dismissed: true } });
    expect(api).toHaveBeenCalledWith("/api/state", { keep: true });
  });

  it("shows the error when dismissing fails", async () => {
    setup();
    vi.mocked(api).mockRejectedValueOnce(new Error("Nope"));
    render(SetupChecklist);
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(toast.error).toHaveBeenCalledWith("Nope");
  });
});

describe("CardsTable", () => {
  const card = (extra: Partial<CardSummary> = {}): CardSummary => ({
    id: "c1", name: "Sapphire", owed_now: 800, statement_key: "stmt-c1", statement_balance: 600, last_close: "2026-03-01", remaining: 600,
    due_date: "2026-03-26", avg_monthly_spend: 700, avg_cycles: 3, minimum_payment: 35, ...extra,
  });
  const at = (iso: string) => vi.useFakeTimers({ toFake: ["Date"], now: new Date(`${iso}T12:00:00`) });

  it("explains how to link cards when there are none", () => {
    render(CardsTable, { cards: [] });
    expect(screen.getByText(/Link your cards through Plaid/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings → Accounts" })).toHaveAttribute("href", "#setup/accounts");
  });

  it("shows what a card owes, its statement, due date, minimum and usual spending", () => {
    at("2026-03-10");
    render(CardsTable, { cards: [card()] });
    expect(screen.getByText(/owes \$800\.00 now/)).toBeInTheDocument();
    expect(screen.getByText("about $700.00 a statement")).toHaveAttribute("title", expect.stringContaining("last 3 statements"));
    expect(screen.getByRole("button", { name: "$600.00" })).toBeInTheDocument();
    expect(screen.getByText("due Mar 26")).not.toHaveClass("text-amber-400");
    expect(screen.getByText(/min \$35\.00/)).toBeInTheDocument();
  });

  it("highlights a payment due within a week", () => {
    at("2026-03-22");
    render(CardsTable, { cards: [card()] });
    expect(screen.getByText("due Mar 26")).toHaveClass("text-amber-400");
  });

  it("says Paid once nothing remains, and what's left after a part payment", () => {
    at("2026-03-10");
    const { unmount } = render(CardsTable, { cards: [card({ remaining: 0 })] });
    expect(screen.getByText("Paid ✓")).toBeInTheDocument();
    unmount();
    render(CardsTable, { cards: [card({ remaining: 250 })] });
    expect(screen.getByText(/\$250\.00 left/)).toBeInTheDocument();
  });

  it("says how much of the statement a card that isn't paid in full pays, and what carries over", () => {
    at("2026-03-10");
    const { unmount } = render(CardsTable, { cards: [card({ pay_mode: "minimum", payment: 35, carried: 565 })] });
    expect(screen.getByText(/pays \$35\.00 of \$600\.00/)).toHaveAttribute("title", "The rest, $565.00, carries into the next statement");
    unmount();
    // paid in full: no "pays", as before
    render(CardsTable, { cards: [card({ pay_mode: "full", payment: 600, carried: 0 })] });
    expect(screen.queryByText(/pays/)).toBeNull();
  });

  it("says when there's no average yet", () => {
    render(CardsTable, { cards: [card({ avg_monthly_spend: null })] });
    expect(screen.getByText("no average yet")).toBeInTheDocument();
  });

  it("lets you correct the statement balance, and undo that", async () => {
    at("2026-03-10");
    render(CardsTable, { cards: [card({ statement_set: true, statement_reported: 590 })] });
    expect(screen.getByText("set")).toHaveAttribute("title", "Entered by you · the bank reported $590.00");
    await userEvent.click(screen.getByRole("button", { name: "$600.00" }));
    const box = screen.getByRole("spinbutton", { name: "Statement balance" });
    await userEvent.clear(box);
    await userEvent.type(box, "610{Enter}");
    expect(api).toHaveBeenCalledWith("/api/overrides", { method: "POST", body: { key: "stmt-c1", amount: 610 } });
    await userEvent.click(screen.getByRole("button", { name: "reset" }));
    expect(api).toHaveBeenCalledWith("/api/overrides", { method: "DELETE", body: { key: "stmt-c1" } });
  });
});
