// @vitest-environment jsdom
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));

import { api } from "$lib/api";
import { fmt0 } from "$lib/format";
import { project, projectionIn } from "./planner";
import RetirementPlanner from "./RetirementPlanner.svelte";
import type { RetirementPlan } from "./types";
import {
  data,
  homeAsset,
  plan,
  resetPlanner,
  setup,
} from "../../../test/planner";
import { viewport } from "$lib/phone.svelte";

beforeEach(resetPlanner);
afterEach(() => vi.useRealTimers());

describe("RetirementPlanner", () => {
  it("shows the headline results from the plan", () => {
    setup();
    expect(screen.getByText("Chance your money lasts")).toBeInTheDocument();
    expect(screen.getByText(/^\d+%$/)).toBeInTheDocument();
    expect(screen.getByText("Invested at retirement")).toBeInTheDocument();
    expect(
      screen.getByText(/to age 95, across 1,000 simulated market paths/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /Projected investments by age/ }),
    ).toBeInTheDocument();
  });

  it("doesn’t explain what the figures are based on, or carry a disclaimer", () => {
    setup();
    expect(screen.queryByText(/Based on your/)).not.toBeInTheDocument();
    expect(screen.queryByText(/financial advice/)).not.toBeInTheDocument();
  });

  it("on a phone, still has the plan's fields and the way to Investments", () => {
    viewport.phone = true;
    try {
      setup(data({ is_default: true }));
      expect(
        screen.queryByText(/Open Runway on a computer/),
      ).not.toBeInTheDocument();
      expect(screen.getByText("Who's retiring")).toBeInTheDocument();
      expect(
        screen.getByText("Spending a year", { exact: false }),
      ).toBeInTheDocument();
    } finally {
      viewport.phone = false;
    }
  });

  it("shows the results muted, marked as a starting estimate, while it starts from Runway's own figures", () => {
    setup(data({ is_default: true }));
    expect(screen.getByTestId("starting-estimate")).toHaveTextContent(
      "Starting estimate · edit below",
    );
    expect(screen.getByText(/^\d+%$/).className).toContain(
      "text-muted-foreground",
    );
    expect(
      screen.queryByRole("button", { name: /Start over/ }),
    ).not.toBeInTheDocument();
  });

  it("colours the result once the plan is your own, without the estimate mark", () => {
    setup();
    expect(screen.queryByTestId("starting-estimate")).not.toBeInTheDocument();
    expect(screen.getByText(/^\d+%$/).className).not.toContain(
      "text-muted-foreground",
    );
  });

  it("says the odds in a word, by the same thresholds as their color, with the thresholds in its tooltip", () => {
    const { unmount } = setup();
    const odds = () =>
      Number(screen.getByText(/^\d+%$/).textContent!.slice(0, -1)) / 100;
    const word = (s: number) =>
      s >= 0.85 ? "Likely" : s >= 0.7 ? "Uncertain" : "Unlikely";
    expect(screen.getByTestId("verdict")).toHaveTextContent(word(odds()));
    expect(screen.getByTestId("verdict")).toHaveAttribute(
      "title",
      "Likely at 85% or more, uncertain from 70%, unlikely below 70%",
    );
    unmount();
    setup(
      data(
        { current: 1000 },
        {
          spending: 200000,
          people: [
            { name: "Ann", birth_year: 1961, retire_age: 65, savings: 0 },
          ],
        },
      ),
    );
    expect(screen.getByTestId("verdict")).toHaveTextContent("Unlikely");
  });

  it("says what it starts from: what's invested today and in how many accounts", () => {
    render(RetirementPlanner, { data: data(), accounts: 3 });
    expect(screen.getByTestId("starting-from")).toHaveTextContent(
      "Starting from $400,000 invested today (3 accounts)",
    );
  });

  it("says when an age can't be, under its field, instead of projecting it", async () => {
    setup();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const retires = screen.getByRole("spinbutton", { name: /Retires at/ });
    await user.clear(retires);
    await user.type(retires, "30");
    expect(screen.getByText("At least 40, your age now")).toBeInTheDocument();
    expect(retires).toHaveAttribute("aria-invalid", "true");
    expect(
      screen.getByText("Fix the ages below to see the projection."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Chance your money lasts"),
    ).not.toBeInTheDocument();
    await user.clear(retires);
    await user.type(retires, "67");
    expect(screen.queryByText(/your age now/)).not.toBeInTheDocument();
    const planTo = screen.getByRole("spinbutton", { name: /Plan until age/ });
    await user.clear(planTo);
    await user.type(planTo, "60");
    expect(
      screen.getByText("Past the retirement age (67)"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Chance your money lasts"),
    ).not.toBeInTheDocument();
  });

  it("shows an empty state, not a projection, with nothing invested and no plan", () => {
    setup(data({ is_default: true, current: 0 }));
    expect(screen.getByText("Nothing to plan from yet")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Go to Investments" }),
    ).toHaveAttribute("href", "#networth/investments");
    expect(
      screen.queryByText("Chance your money lasts"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("img", { name: /Projected/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Born in")).not.toBeInTheDocument();
  });

  it("still plans with nothing invested once you've saved a plan of your own", () => {
    setup(data({ current: 0 }));
    expect(
      screen.queryByText("Nothing to plan from yet"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Chance your money lasts")).toBeInTheDocument();
  });

  it("warns that the money runs out when the plan can't last", () => {
    setup(
      data(
        { current: 1000 },
        {
          spending: 200000,
          people: [
            { name: "Ann", birth_year: 1961, retire_age: 65, savings: 0 },
          ],
        },
      ),
    );
    expect(screen.getByText("Runs out")).toBeInTheDocument();
    expect(screen.getByText(/^age \d+$/)).toBeInTheDocument();
  });

  it("asks for a birth year instead of a chart when the plan can't be projected yet", async () => {
    setup();
    const born = screen.getByLabelText("Born in");
    await userEvent.clear(born);
    expect(
      screen.getByText(
        "Enter a birth year and retirement age to see the projection.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("img", { name: /Projected/ }),
    ).not.toBeInTheDocument();
  });

  describe("editing", () => {
    it("keeps the plan a moment after you stop typing, not on every key", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      setup();
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await user.type(
        screen.getByRole("textbox", { name: "Yearly spending in retirement" }),
        "5",
      );
      expect(api).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(800);
      expect(api).toHaveBeenCalledOnce();
      const [path, opts] = vi.mocked(api).mock.calls[0] as [
        string,
        { body: { plan: RetirementPlan } },
      ];
      expect(path).toBe("/api/investments/plan");
      expect(opts.body.plan.spending).toBe(600005);
    });

    it("stores percentages as fractions", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      setup(data({ assets: [homeAsset()] }));
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const infl = screen.getByRole("spinbutton", { name: "Inflation" });
      expect(infl).toHaveValue(2.5);
      await user.clear(infl);
      await user.type(infl, "3");
      await vi.advanceTimersByTimeAsync(800);
      expect(
        (vi.mocked(api).mock.lastCall![1] as { body: { plan: RetirementPlan } })
          .body.plan.inflation,
      ).toBeCloseTo(0.03);
    });

    it("keeps inflation with the assumptions, with or without homes to sell (it also gives future dollars)", () => {
      setup(data({ assets: [homeAsset()] }));
      expect(
        screen
          .getByRole("spinbutton", { name: "Inflation" })
          .closest("details"),
      ).toHaveTextContent("Assumptions");
      expect(
        screen.queryByText(/taking off inflation/),
      ).not.toBeInTheDocument();
      cleanup();
      setup();
      expect(
        screen.getByRole("spinbutton", { name: "Inflation" }),
      ).toBeInTheDocument();
    });

    it("saves a pending change when you leave before the pause is up", async () => {
      const { unmount } = setup();
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await user.type(
        screen.getByRole("textbox", { name: "Yearly spending in retirement" }),
        "5",
      );
      expect(api).not.toHaveBeenCalled();
      unmount();
      expect(api).toHaveBeenCalledOnce();
      expect(
        (vi.mocked(api).mock.calls[0][1] as { body: { plan: RetirementPlan } })
          .body.plan.spending,
      ).toBe(600005);
    });

    it("doesn't save on leaving when nothing changed", () => {
      setup().unmount();
      expect(api).not.toHaveBeenCalled();
    });

    it("confirms a save with a brief Saved ✓", async () => {
      setup();
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await user.type(screen.getByLabelText("Name"), "x");
      await vi.advanceTimersByTimeAsync(800);
      expect(await screen.findByRole("status")).toHaveTextContent("Saved ✓");
      await vi.advanceTimersByTimeAsync(2500);
      await waitFor(() =>
        expect(screen.queryByRole("status")).not.toBeInTheDocument(),
      );
    });

    it("shows why when the server refuses the plan", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      vi.mocked(api).mockRejectedValue(new Error("Retire age too low"));
      setup();
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await user.type(screen.getByLabelText("Name"), "x");
      await vi.advanceTimersByTimeAsync(800);
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Not saved: Retire age too low",
      );
    });
  });

  describe("people", () => {
    it("adds a partner who starts with the first person's dates, and removes them again", async () => {
      setup();
      await userEvent.click(
        screen.getByRole("button", { name: /Add a partner/ }),
      );
      expect(screen.getAllByLabelText("Born in")).toHaveLength(2);
      expect(screen.getAllByLabelText("Born in")[1]).toHaveValue(1986);
      await userEvent.click(
        screen.getByRole("button", { name: /Remove Partner/ }),
      );
      expect(screen.getAllByLabelText("Born in")).toHaveLength(1);
      expect(
        screen.getByRole("button", { name: /Add a partner/ }),
      ).toBeInTheDocument();
    });

    it("drops the partner's income when they're removed", async () => {
      setup();
      await userEvent.click(
        screen.getByRole("button", { name: /Add a partner/ }),
      );
      await userEvent.click(
        screen.getByRole("button", { name: /Social Security/ }),
      );
      await userEvent.selectOptions(screen.getByRole("combobox"), "1");
      await userEvent.click(
        screen.getByRole("button", { name: /Remove Partner/ }),
      );
      expect(
        screen.queryByLabelText("Social Security a year"),
      ).not.toBeInTheDocument();
    });
  });

  describe("income", () => {
    it("adds Social Security starting at 67, and pension at 65", async () => {
      setup();
      await userEvent.click(
        screen.getByRole("button", { name: /Social Security/ }),
      );
      await userEvent.click(screen.getByRole("button", { name: /Pension/ }));
      expect(
        screen
          .getAllByLabelText("From age")
          .map((i) => (i as HTMLInputElement).value),
      ).toEqual(["67", "65"]);
    });

    it("removes an income", async () => {
      setup();
      await userEvent.click(screen.getByRole("button", { name: /Pension/ }));
      await userEvent.click(
        screen.getByRole("button", { name: "Remove Pension" }),
      );
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
      await userEvent.click(
        screen.getByRole("button", { name: /Add an event/ }),
      );
      expect(screen.getByLabelText("Year")).toHaveValue(2031);
      expect(screen.getByLabelText("Money")).toHaveValue("out");
      expect(screen.getByRole("textbox", { name: /Amount/ })).toHaveValue(
        "10000",
      );
      await userEvent.selectOptions(screen.getByLabelText("Money"), "in");
      expect(screen.getByLabelText("Money")).toHaveValue("in");
      expect(screen.getByRole("textbox", { name: /Amount/ })).toHaveValue(
        "10000",
      );
    });

    it("removes an event", async () => {
      setup();
      await userEvent.click(
        screen.getByRole("button", { name: /Add an event/ }),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Remove event" }),
      );
      expect(screen.queryByLabelText("Year")).not.toBeInTheDocument();
    });
  });

  describe("today's or future dollars", () => {
    afterEach(() => vi.restoreAllMocks());
    const figure = (label: RegExp) =>
      screen.getByText(label).nextElementSibling!;
    const p = project(plan(), 400000, 2026, []);
    const f = projectionIn(p, 2026, 0.025, "future");

    it("starts in today's dollars, and leaves the inflation to Assumptions", () => {
      setup();
      expect(
        screen.getByRole("radio", { name: "Today’s dollars" }),
      ).toBeChecked();
      expect(
        screen.queryByText(/what you enter is in today’s dollars/),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "2.5% a year" }),
      ).not.toBeInTheDocument();
      expect(figure(/^Invested at retirement$/)).toHaveTextContent(
        fmt0(p.atRetirement),
      );
    });

    it("shows the figures in each year's dollars, and back", async () => {
      setup();
      await userEvent.click(
        screen.getByRole("radio", { name: "Future dollars" }),
      );
      expect(figure(/^Invested at retirement · 2051$/)).toHaveTextContent(
        fmt0(f.atRetirement),
      );
      expect(f.atRetirement).toBeCloseTo(p.atRetirement * 1.025 ** 25, 4);
      expect(
        figure(/^Invested at retirement/).nextElementSibling,
      ).toHaveTextContent(`${fmt0(f.low[25])} – ${fmt0(f.high[25])} likely`);
      expect(figure(/^Left at age 95 · 2081$/)).toHaveTextContent(
        fmt0(f.atEnd),
      );
      expect(screen.getByText(/^\d+%$/)).toHaveTextContent(
        `${Math.round(p.success * 100)}%`,
      );
      expect(
        screen.getByRole("img", {
          name: /^Projected investments by age, in each year’s dollars: median/,
        }),
      ).toBeInTheDocument();
      await userEvent.click(
        screen.getByRole("radio", { name: "Today’s dollars" }),
      );
      expect(figure(/^Invested at retirement$/)).toHaveTextContent(
        fmt0(p.atRetirement),
      );
      expect(
        screen.getByRole("img", {
          name: /^Projected investments by age, in today’s dollars/,
        }),
      ).toBeInTheDocument();
    });

    it("remembers the choice in this browser, without saving the plan", async () => {
      setup();
      await userEvent.click(
        screen.getByRole("radio", { name: "Future dollars" }),
      );
      expect(localStorage.getItem("runway.planner.dollars")).toBe("future");
      await vi.advanceTimersByTimeAsync(800);
      expect(api).not.toHaveBeenCalled();
      cleanup();
      setup();
      expect(
        screen.getByRole("radio", { name: "Future dollars" }),
      ).toBeChecked();
      expect(figure(/^Invested at retirement · 2051$/)).toHaveTextContent(
        fmt0(f.atRetirement),
      );
    });

    it("still switches when the browser won't keep it, starting from today's dollars", async () => {
      vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
        throw new Error("blocked");
      });
      vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new Error("blocked");
      });
      setup();
      expect(
        screen.getByRole("radio", { name: "Today’s dollars" }),
      ).toBeChecked();
      await userEvent.click(
        screen.getByRole("radio", { name: "Future dollars" }),
      );
      expect(figure(/^Invested at retirement · 2051$/)).toHaveTextContent(
        fmt0(f.atRetirement),
      );
    });

    it("follows the inflation you set", async () => {
      setup();
      await userEvent.click(
        screen.getByRole("radio", { name: "Future dollars" }),
      );
      const infl = screen.getByRole("spinbutton", { name: "Inflation" });
      await userEvent.clear(infl);
      await userEvent.type(infl, "3");
      expect(figure(/^Invested at retirement · 2051$/)).toHaveTextContent(
        fmt0(p.atRetirement * 1.03 ** 25),
      );
    });
  });

  describe("assumptions", () => {
    const details = () =>
      screen
        .getByText("Assumptions", { selector: "summary span" })
        .closest("details")!;

    it("folds the set-once figures away once they're in, with a one-line summary", () => {
      setup(
        data(
          {},
          {
            income: [
              {
                name: "Social Security",
                amount: 28800,
                person: 0,
                start_age: 67,
                end_age: null,
              },
            ],
          },
        ),
      );
      expect(details().open).toBe(false);
      expect(details().querySelector("[data-summary]")).toHaveTextContent(
        "Born 1986 · to age 95 · Social Security $28,800 a year at 67 · 5% returns (4% retired), 2.5% inflation",
      );
      for (const name of [
        "Born in",
        "Plan until age",
        "Inflation",
        "Return while saving",
        "Social Security a year",
      ]) {
        expect(details()).toContainElement(screen.getByLabelText(name));
      }
      expect(details()).not.toContainElement(
        screen.getByLabelText("Retires at · age"),
      );
    });

    it("is open while something's missing: Runway's sample plan, or a birth year that isn't one", () => {
      setup(data({ is_default: true }));
      expect(details().open).toBe(true);
      cleanup();
      setup(
        data(
          {},
          {
            people: [
              { name: "Ann", birth_year: 0, retire_age: 65, savings: 0 },
            ],
          },
        ),
      );
      expect(details().open).toBe(true);
    });

    it("names each person's birth year with two, and says when there's no income yet", () => {
      setup(
        data(
          {},
          {
            people: [
              { name: "Ann", birth_year: 1986, retire_age: 65, savings: 0 },
              { name: "Bo", birth_year: 1988, retire_age: 63, savings: 0 },
            ],
          },
        ),
      );
      expect(details().querySelector("[data-summary]")).toHaveTextContent(
        /^Ann born 1986, Bo born 1988 · to age 95 · no retirement income yet · /,
      );
    });

    it("opens to show a new partner's birth year", async () => {
      setup();
      await userEvent.click(
        screen.getByRole("button", { name: /Add a partner/ }),
      );
      expect(details().open).toBe(true);
    });
  });

  describe("home equity and equity comp in the chart", () => {
    it("stacks a home's equity on the investments until it's sold, with a key", async () => {
      setup(data({ assets: [homeAsset({ owed: 200000 })] }));
      expect(screen.getByRole("list", { name: "Chart key" })).toHaveTextContent(
        "Home equity",
      );
      expect(
        screen.getByRole("img", { name: /with home equity$/ }),
      ).toBeInTheDocument();
      await userEvent.click(screen.getByText("Show as table"));
      expect(
        screen.getByRole("columnheader", { name: "Home equity" }),
      ).toBeInTheDocument();
      expect(
        within(screen.getAllByRole("row")[1]).getByText("$300,000"),
      ).toBeInTheDocument();
    });

    it("leaves vehicles out of it", () => {
      setup(
        data({
          assets: [
            homeAsset({ key: "asset:car", name: "Car", kind: "vehicle" }),
          ],
        }),
      );
      expect(
        screen.queryByRole("list", { name: "Chart key" }),
      ).not.toBeInTheDocument();
    });
  });

  describe("what the figures mean", () => {
    it("says when the investment history is shorter than a year, and leaves what savings is to Assumptions", () => {
      const { unmount } = setup();
      expect(
        screen.queryByText(/is what went into your investments/),
      ).not.toBeInTheDocument();
      unmount();
      setup(
        data({
          computed: {
            annual_spending: 55000,
            yearly_savings: 3000,
            expected_return: 0.05,
            savings_measured: true,
            savings_since: "2026-05-31",
          },
        }),
      );
      expect(
        screen.getByTitle(/only goes back to May\s31,\s2026/),
      ).toBeInTheDocument();
    });

    it("doesn't call a figure typed on the old card a measurement", () => {
      setup(
        data({
          computed: {
            annual_spending: 55000,
            yearly_savings: 20000,
            expected_return: 0.05,
            savings_measured: false,
            savings_since: null,
          },
        }),
      );
      expect(
        screen.queryByText(/what went into your investments/),
      ).not.toBeInTheDocument();
    });

    it("leaves who's covered while still working to Assumptions, with one person or two", async () => {
      setup();
      expect(
        screen.queryByText(/still working is assumed to cover/),
      ).not.toBeInTheDocument();
      await userEvent.click(
        screen.getByRole("button", { name: /Add a partner/ }),
      );
      expect(
        screen.queryByText(/still working is assumed to cover/),
      ).not.toBeInTheDocument();
      expect(screen.queryByText(/Savings stop at/)).not.toBeInTheDocument();
    });
  });

  it("asks before starting over, and doesn't clear anything on the first click", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: /Start over/ }));
    expect(
      screen.getByRole("button", {
        name: "Start over? This clears everything you entered here.",
      }),
    ).toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
    expect(
      screen.getByRole("textbox", { name: "Yearly spending in retirement" }),
    ).toHaveValue("60000");
  });

  it("shows dollar amounts with their commas", () => {
    setup();
    const field = screen.getByRole("textbox", {
      name: "Yearly spending in retirement",
    });
    expect(
      Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )!.get!.call(field),
    ).toBe("60,000");
  });

  it("starts over from Runway's figures, saving that the plan is the default again", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: /Start over/ }));
    await userEvent.click(
      screen.getByRole("button", { name: /Start over\? This clears/ }),
    );
    expect(api).toHaveBeenCalledWith("/api/investments/plan", {
      method: "POST",
      body: { plan: null },
    });
    await waitFor(() =>
      expect(screen.getByLabelText("Born in")).toHaveValue(1986),
    );
    expect(screen.getByText(/^\d+%$/).className).toContain(
      "text-muted-foreground",
    );
    expect(
      screen.getByRole("textbox", { name: "Yearly spending in retirement" }),
    ).toHaveValue("55000");
  });

  it("keeps the plan and shows the error when starting over fails", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Locked"));
    setup();
    await userEvent.click(screen.getByRole("button", { name: /Start over/ }));
    await userEvent.click(
      screen.getByRole("button", { name: /Start over\? This clears/ }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Locked");
    expect(
      screen.getByRole("textbox", { name: "Yearly spending in retirement" }),
    ).toHaveValue("60000");
  });
});
