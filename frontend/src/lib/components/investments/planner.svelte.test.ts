// @vitest-environment jsdom
// The retirement planner form and its chart. The projection maths has its own tests (planner.test.ts); these check
// what the page shows from it and what it saves.
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));

import { api } from "$lib/api";
import PlannerChart from "./PlannerChart.svelte";
import { project } from "./planner";
import RetirementPlanner from "./RetirementPlanner.svelte";
import type { Plan, PlanData } from "./types";

const plan = (extra: Partial<Plan> = {}): Plan => ({
  people: [{ name: "Ann", birth_year: 1986, retire_age: 65, savings: 20000 }], plan_to_age: 95, spending: 60000,
  return_before: 0.05, return_after: 0.04, volatility: 0.1, inflation: 0.025, income: [], events: [], assets: [], ...extra,
});
const data = (extra: Partial<PlanData> = {}, p: Partial<Plan> = {}): PlanData => ({
  plan: plan(p), is_default: false, current: 400000, year: 2026, assets: [],
  computed: { annual_spending: 55000, yearly_savings: 18000, expected_return: 0.05 }, ...extra,
});
const setup = (d = data()) => render(RetirementPlanner, { data: d });

beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(api).mockResolvedValue({}); });
afterEach(() => vi.useRealTimers());

describe("RetirementPlanner", () => {
  it("shows the headline results from the plan", () => {
    setup();
    expect(screen.getByText("Chance your money lasts")).toBeInTheDocument();
    expect(screen.getByText(/^\d+%$/)).toBeInTheDocument();
    expect(screen.getByText("Invested at retirement")).toBeInTheDocument();
    expect(screen.getByText(/to age 95, across 1,000 markets/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Projected investments by age/ })).toBeInTheDocument();
  });

  it("says when it starts from Runway's own figures", () => {
    setup(data({ is_default: true }));
    expect(screen.getByText(/Starting from Runway's figures/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Start over/ })).not.toBeInTheDocument();
  });

  it("warns that the money runs out when the plan can't last", () => {
    setup(data({ current: 1000 }, { spending: 200000, people: [{ name: "Ann", birth_year: 1961, retire_age: 65, savings: 0 }] }));
    expect(screen.getByText("Typically runs out")).toBeInTheDocument();
    expect(screen.getByText(/^age \d+$/)).toBeInTheDocument();
  });

  it("asks for a birth year instead of a chart when the plan can't be projected yet", async () => {
    setup();
    const born = screen.getByLabelText("Born in");
    await userEvent.clear(born);
    expect(screen.getByText("Enter a birth year and retirement age to see the projection.")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /Projected/ })).not.toBeInTheDocument();
  });

  describe("editing", () => {
    it("keeps the plan a moment after you stop typing, not on every key", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      setup();
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await user.type(screen.getByRole("spinbutton", { name: "Yearly spending in retirement" }), "5");
      expect(api).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(800);
      expect(api).toHaveBeenCalledOnce();
      const [path, opts] = vi.mocked(api).mock.calls[0] as [string, { body: { plan: Plan } }];
      expect(path).toBe("/api/investments/plan");
      expect(opts.body.plan.spending).toBe(600005);
    });

    it("stores percentages as fractions", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      setup();
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const infl = screen.getByRole("spinbutton", { name: "Inflation" });
      expect(infl).toHaveValue(2.5);
      await user.clear(infl);
      await user.type(infl, "3");
      await vi.advanceTimersByTimeAsync(800);
      expect((vi.mocked(api).mock.lastCall![1] as { body: { plan: Plan } }).body.plan.inflation).toBeCloseTo(0.03);
    });

    it("shows why when the server refuses the plan", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      vi.mocked(api).mockRejectedValue(new Error("Retire age too low"));
      setup();
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await user.type(screen.getByLabelText("Name"), "x");
      await vi.advanceTimersByTimeAsync(800);
      expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: Retire age too low");
    });
  });

  describe("people", () => {
    it("adds a partner who starts with the first person's dates, and removes them again", async () => {
      setup();
      await userEvent.click(screen.getByRole("button", { name: /Add a partner/ }));
      expect(screen.getAllByLabelText("Born in")).toHaveLength(2);
      expect(screen.getAllByLabelText("Born in")[1]).toHaveValue(1986);
      await userEvent.click(screen.getByRole("button", { name: /Remove Partner/ }));
      expect(screen.getAllByLabelText("Born in")).toHaveLength(1);
      expect(screen.getByRole("button", { name: /Add a partner/ })).toBeInTheDocument();
    });

    it("drops the partner's income when they're removed", async () => {
      setup();
      await userEvent.click(screen.getByRole("button", { name: /Add a partner/ }));
      await userEvent.click(screen.getByRole("button", { name: /Social Security/ }));
      await userEvent.selectOptions(screen.getByRole("combobox"), "1");   // whose income: the partner's
      await userEvent.click(screen.getByRole("button", { name: /Remove Partner/ }));
      expect(screen.queryByLabelText("Social Security a year")).not.toBeInTheDocument();
    });
  });

  describe("income", () => {
    it("adds Social Security starting at 67, and pension at 65", async () => {
      setup();
      await userEvent.click(screen.getByRole("button", { name: /Social Security/ }));
      await userEvent.click(screen.getByRole("button", { name: /Pension/ }));
      expect(screen.getAllByLabelText("From age").map((i) => (i as HTMLInputElement).value)).toEqual(["67", "65"]);
    });

    it("removes an income", async () => {
      setup();
      await userEvent.click(screen.getByRole("button", { name: /Pension/ }));
      await userEvent.click(screen.getByRole("button", { name: "Remove Pension" }));
      expect(screen.queryByLabelText("Pension a year")).not.toBeInTheDocument();
    });

    it("leaves 'until age' empty for life", async () => {
      setup();
      await userEvent.click(screen.getByRole("button", { name: /Pension/ }));
      expect(screen.getByLabelText("Until age")).toHaveValue(null);
    });
  });

  describe("one-time events", () => {
    it("adds an event five years out as money spent, and can flip it to money received", async () => {
      setup();
      await userEvent.click(screen.getByRole("button", { name: /Add an event/ }));
      expect(screen.getByLabelText("Year")).toHaveValue(2031);
      expect(screen.getByLabelText("Money")).toHaveValue("out");
      expect(screen.getByRole("spinbutton", { name: /Amount/ })).toHaveValue(10000);
      await userEvent.selectOptions(screen.getByLabelText("Money"), "in");
      expect(screen.getByLabelText("Money")).toHaveValue("in");
      expect(screen.getByRole("spinbutton", { name: /Amount/ })).toHaveValue(10000);   // the amount stays positive; only the sign flips
    });

    it("removes an event", async () => {
      setup();
      await userEvent.click(screen.getByRole("button", { name: /Add an event/ }));
      await userEvent.click(screen.getByRole("button", { name: "Remove event" }));
      expect(screen.queryByLabelText("Year")).not.toBeInTheDocument();
    });
  });

  describe("assets", () => {
    const home = { key: "home1", name: "Home", kind: "home", value: 500000, yearly_change: 0.03, owed: 200000 };

    it("explains where assets come from when there are none", () => {
      setup();
      expect(screen.getByText(/can be sold into the plan here/)).toBeInTheDocument();
    });

    it("lists an asset with what it's worth after its loan and lets you sell it in a year", async () => {
      setup(data({ assets: [home] }));
      expect(screen.getByText(/\$300,000 after the loan/)).toBeInTheDocument();
      await userEvent.click(screen.getByRole("checkbox", { name: /Home/ }));
      expect(screen.getByDisplayValue("2051")).toBeInTheDocument();   // the first person retires at 65 (born 1986)
      expect(screen.getByText(/^≈ \$/)).toBeInTheDocument();
      await userEvent.click(screen.getByRole("checkbox", { name: /Home/ }));
      expect(screen.queryByText(/^Sell in/)).not.toBeInTheDocument();
    });
  });

  it("starts over from Runway's figures, saving that the plan is the default again", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: /Start over/ }));
    expect(api).toHaveBeenCalledWith("/api/investments/plan", { method: "POST", body: { plan: null } });
    await waitFor(() => expect(screen.getByLabelText("Born in")).toHaveValue(1986));   // year - 40
    expect(screen.getByText(/Starting from Runway's figures/)).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Yearly spending in retirement" })).toHaveValue(55000);
  });

  it("keeps the plan and shows the error when starting over fails", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Locked"));
    setup();
    await userEvent.click(screen.getByRole("button", { name: /Start over/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Locked");
    expect(screen.getByRole("spinbutton", { name: "Yearly spending in retirement" })).toHaveValue(60000);
  });
});

describe("PlannerChart", () => {
  const proj = project(plan(), 400000, 2026, [], 50);
  const setup2 = () => render(PlannerChart, { p: proj, names: ["You"] });

  it("draws the likely band, the median line and a retirement marker", () => {
    const { container } = setup2();
    expect(container.querySelectorAll("svg path").length).toBeGreaterThanOrEqual(4);   // band, high, low, median
    expect(screen.getByText("retirement")).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute("aria-label", expect.stringMatching(/median \$[\d,]+ at the end/));
  });

  it("labels the axis with the first person's age", () => {
    setup2();
    expect(screen.getByText("Your age")).toBeInTheDocument();
  });

  it("names the person when it isn't 'You'", () => {
    render(PlannerChart, { p: proj, names: ["Ann"] });
    expect(screen.getByText("Ann's age")).toBeInTheDocument();
  });

  it("has a table of every year", async () => {
    setup2();
    await userEvent.click(screen.getByText("Show as table"));
    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(proj.years.length + 1);
    expect(within(rows[1]).getByText("2026")).toBeInTheDocument();
  });

  it("shows a readout for the year under the pointer", async () => {
    const { container } = setup2();
    const svg = container.querySelector("svg")!;
    svg.getBoundingClientRect = () => ({ left: 0, top: 0, right: 320, bottom: 260, width: 320, height: 260, x: 0, y: 0, toJSON() {} });
    await userEvent.hover(container.querySelector("rect")!);
    const rect = container.querySelector("rect")!;
    rect.dispatchEvent(new MouseEvent("mousemove", { clientX: 100, bubbles: true }));
    expect(await screen.findByText("Typical", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("Good markets", { selector: "div span" })).toBeInTheDocument();
    rect.dispatchEvent(new MouseEvent("mouseleave"));
    await waitFor(() => expect(screen.queryByText("Typical", { selector: "span" })).not.toBeInTheDocument());
  });
});
