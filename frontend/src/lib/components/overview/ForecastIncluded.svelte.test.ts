// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import ForecastIncluded from "./ForecastIncluded.svelte";

afterEach(cleanup);

const used = (category: string, amount: number, account = "Checking") => ({ category, amount, account_id: account, account, chosen: false });
const show = (budget: unknown) => render(ForecastIncluded, { budget } as never);
const group = () => screen.getByRole("group", { name: "What the forecast includes" });

describe("ForecastIncluded", () => {
  it("is a named disclosure that opens by keyboard", async () => {
    show({ monthly: 0, used: [], skipped: [] });
    const summary = screen.getByText("What’s included");
    const details = summary.closest("details")!;
    expect(details.open).toBe(false);
    await userEvent.tab();
    expect(summary).toHaveFocus();
    // A native <summary> is a tab stop; the browser turns Enter/Space/tap into this click (jsdom doesn't).
    await userEvent.click(summary);
    expect(details.open).toBe(true);
  });

  it("lists each used budget with its amount, and the total the forecast returned", () => {
    show({ monthly: 700, used: [used("Groceries", 500), used("Dining", 200, "Visa")], skipped: [] });
    expect(within(group()).getByText(/Groceries · Checking/)).toBeInTheDocument();
    expect(within(group()).getByText("$500 a month")).toBeInTheDocument();
    expect(within(group()).getByText(/Dining · Visa/)).toBeInTheDocument();
    expect(within(group()).getByText("$700 a month in all")).toBeInTheDocument();
    expect(within(group()).getByText("Nothing is left out.")).toBeInTheDocument();
    expect(within(group()).queryByText("Left out")).not.toBeInTheDocument();
  });

  it("says budget spending is off, with a link to turn it on, when there is no budget", () => {
    show(null);
    expect(within(group()).getByText(/Off: no budgets are counted/)).toBeInTheDocument();
    expect(within(group()).getByRole("link", { name: "Turn on budgets" })).toHaveAttribute("href", "#budget");
    expect(within(group()).getByText("Recurring bills and income")).toBeInTheDocument();
  });

  it("says so when budgets exist but none are spent, and nothing is skipped", () => {
    show({ monthly: 0, used: [], skipped: [] });
    expect(within(group()).getByText("None of your budgets are spent in the forecast.")).toBeInTheDocument();
    expect(within(group()).queryByText("Left out")).not.toBeInTheDocument();
  });

  it("explains a budget recurring items already cover as a choice, with no setup link", () => {
    show({ monthly: 0, used: [], skipped: [{ category: "Mortgage", reason: "a recurring item already covers it" }, { category: "Utilities", reason: "a recurring item already covers it" }] });
    const li = within(group()).getByText("Already covered by recurring").closest("li")!;
    expect(li).toHaveAttribute("data-kind", "covered");
    expect(li).toHaveTextContent("Mortgage, Utilities.");
    expect(within(li).queryByRole("link")).not.toBeInTheDocument();
  });

  it.each([
    ["its account isn't in the forecast", "Account isn't in the forecast"],
    ["its card isn't paid from a forecast account", "Card isn't paid from a forecast account"],
    ["its card's statement is out of date", "Card statement is out of date"],
  ])("explains %s as an input that needs setup, with a link", (reason, title) => {
    show({ monthly: 0, used: [], skipped: [{ category: "Medical", reason }] });
    const li = within(group()).getByText(title).closest("li")!;
    expect(li).toHaveAttribute("data-kind", "setup");
    expect(li).toHaveTextContent("Medical.");
    expect(within(li).getByRole("link", { name: "Open account settings" })).toHaveAttribute("href", "#setup/accounts");
  });

  it("shows an unknown future reason as sent, grouped with its categories", () => {
    show({ monthly: 0, used: [], skipped: [{ category: "Pets", reason: "its budget is paused" }, { category: "Gifts", reason: "its budget is paused" }] });
    const li = within(group()).getByText("Its budget is paused").closest("li")!;
    expect(li).toHaveAttribute("data-kind", "other");
    expect(li).toHaveTextContent("Pets, Gifts.");
    expect(within(li).queryByRole("link")).not.toBeInTheDocument();
  });

  it("renders an empty reason and many categories safely", () => {
    const many = Array.from({ length: 40 }, (_, i) => used(`Cat ${i}`, 10 + i));
    show({ monthly: 1000, used: many, skipped: [{ category: "Odd", reason: "" }] });
    expect(within(group()).getAllByRole("listitem").length).toBe(41);
    expect(within(group()).getByText(/Odd\./)).toBeInTheDocument();
  });
});
