// @vitest-environment jsdom
// The retirement planner form and its chart. The projection maths has its own tests (planner.test.ts); these check
// what the page shows from it and what it saves.
import { cleanup, render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));

import { api } from "$lib/api";
import PlannerChart from "./PlannerChart.svelte";
import { fmt0 } from "$lib/format";
import { project, projectionIn } from "./planner";
import RetirementPlanner from "./RetirementPlanner.svelte";
import type { PlanAsset, PlanData, RetirementPlan } from "./types";
import { viewport } from "$lib/phone.svelte";

const plan = (extra: Partial<RetirementPlan> = {}): RetirementPlan => ({
  people: [{ name: "Ann", birth_year: 1986, retire_age: 65, savings: 20000 }], plan_to_age: 95, spending: 60000,
  return_before: 0.05, return_after: 0.04, volatility: 0.1, inflation: 0.025, income: [], events: [], assets: [], ...extra,
});
const data = (extra: Partial<PlanData> = {}, p: Partial<RetirementPlan> = {}): PlanData => ({
  plan: plan(p), is_default: false, current: 400000, year: 2026, assets: [],
  computed: { annual_spending: 55000, yearly_savings: 18000, expected_return: 0.05 }, ...extra,
});
const setup = (d = data()) => render(RetirementPlanner, { data: d });
const homeAsset = (over: Partial<PlanAsset> = {}): PlanAsset => ({
  key: "home1", name: "Home", kind: "home", value: 500000, yearly_change: 0.03, owed: 0, ...over,
});
// $200,000 at 6% and $1,500 a month from October 2026: the last payment in 2045 (runway/loans.py works out the year).
const repaying = (loan: Partial<NonNullable<PlanAsset["loan"]>> = {}): PlanAsset => homeAsset({
  owed: 200000, owed_by_year: [200000, 0],
  loan: { rate: 6, payment: 1500, source: "manual", note: null, account_id: "mtg", payoff_year: 2045, payment_counted: true, ...loan },
});

// Every edit schedules a save 700ms later. On real timers a test that edits and ends leaves that save pending, and it
// lands in the next test (a second call where one is expected). Fake timers are dropped after each test, and
// shouldAdvanceTime keeps user-event's own small delays moving.
// The dollars switch is remembered in localStorage, so each test starts without a choice.
beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true }); vi.mocked(api).mockReset(); vi.mocked(api).mockResolvedValue({}); localStorage.clear();
});
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

  it("says what the figures are based on, and that it isn't advice", () => {
    setup();
    expect(screen.getByText(/Based on your \$400,000 in investments today\./)).toHaveTextContent(/taxes aren't modeled\. Not financial advice\./);
  });

  it("on a phone, says the plan is made yours on a computer, where its fields are", () => {
    viewport.phone = true;
    try {
      setup(data({ is_default: true }));
      expect(screen.getByText(/Enter\s+your birth year and retirement age on a computer to make it yours/)).toBeInTheDocument();
      expect(screen.getByText("Open Runway on a computer to change the plan.")).toBeInTheDocument();
    } finally { viewport.phone = false; }
  });

  it("marks the results as a sample while it starts from Runway's own figures", () => {
    setup(data({ is_default: true }));
    expect(screen.getByText("Sample · based on default assumptions")).toBeInTheDocument();
    expect(screen.getByText(/Enter\s+your birth year and retirement age to make it yours/)).toBeInTheDocument();
    expect(screen.getByText(/^\d+%$/).className).toContain("text-muted-foreground");
    expect(screen.queryByRole("button", { name: /Start over/ })).not.toBeInTheDocument();
  });

  it("colours the result once the plan is your own", () => {
    setup();
    expect(screen.queryByText(/Sample ·/)).not.toBeInTheDocument();
    expect(screen.getByText(/^\d+%$/).className).not.toContain("text-muted-foreground");
  });

  it("shows an empty state, not a projection, with nothing invested and no plan", () => {
    setup(data({ is_default: true, current: 0 }));
    expect(screen.getByText("Nothing to plan from yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Investments" })).toHaveAttribute("href", "#networth/investments");
    expect(screen.queryByText("Chance your money lasts")).not.toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /Projected/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Born in")).not.toBeInTheDocument();
  });

  it("still plans with nothing invested once you've saved a plan of your own", () => {
    setup(data({ current: 0 }));
    expect(screen.queryByText("Nothing to plan from yet")).not.toBeInTheDocument();
    expect(screen.getByText("Chance your money lasts")).toBeInTheDocument();
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
      const [path, opts] = vi.mocked(api).mock.calls[0] as [string, { body: { plan: RetirementPlan } }];
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
      expect((vi.mocked(api).mock.lastCall![1] as { body: { plan: RetirementPlan } }).body.plan.inflation).toBeCloseTo(0.03);
    });

    it("keeps inflation with the assumptions, with or without homes to sell (it also gives future dollars)", () => {
      setup(data({ assets: [homeAsset()] }));
      expect(screen.getByRole("spinbutton", { name: "Inflation" }).closest("details")).toHaveTextContent("Assumptions");
      expect(screen.getByText(/taking off inflation \(2\.5% a year, under Assumptions\)/)).toBeInTheDocument();
      cleanup();
      setup();
      expect(screen.getByRole("spinbutton", { name: "Inflation" })).toBeInTheDocument();
    });

    it("saves a pending change when you leave before the pause is up", async () => {
      const { unmount } = setup();
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await user.type(screen.getByRole("spinbutton", { name: "Yearly spending in retirement" }), "5");
      expect(api).not.toHaveBeenCalled();
      unmount();
      expect(api).toHaveBeenCalledOnce();
      expect((vi.mocked(api).mock.calls[0][1] as { body: { plan: RetirementPlan } }).body.plan.spending).toBe(600005);
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
      await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
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
    const home = homeAsset({ owed: 200000 });

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

    it("says what the estimate assumes: the home's value then, less the loan paid down on its terms", async () => {
      const paying = { ...home, yearly_change: 0.025, owed_by_year: Array.from({ length: 26 }, (_, k) => 200000 - k * 8000),
        loan: { rate: 6.25, payment: 1840, source: "inferred" as const, note: null } };
      setup(data({ assets: [paying] }, { assets: [{ key: "home1", sell_year: 2036 }] }));
      // 2036: worth 500k (it keeps pace with inflation), 120k still owed in 2036's dollars = 120k / 1.025^10 today
      expect(screen.getByText(/^≈ \$/)).toHaveAttribute("title",
        "Home worth $500,000 in 2036, less $93,744 still owed on the loan at 6.25% and $1,840 a month from recent payments. In today’s dollars.");
      expect(screen.queryByText(/to project it/)).not.toBeInTheDocument();
    });

    it("says when a loan is paid off by the sale", () => {
      const paid = { ...home, owed_by_year: [200000, 100000, 0], loan: { rate: 5, payment: 9000, source: "plaid" as const, note: null } };
      setup(data({ assets: [paid] }, { assets: [{ key: "home1", sell_year: 2030 }] }));
      expect(screen.getByText(/^≈ \$/).getAttribute("title")).toMatch(/; the loan \(5% and \$9,000 a month from Plaid\) is paid off by then\./);
    });

    it("asks for the loan's interest rate when it can't project it, and counts today's balance", () => {
      const unknown = { ...home, yearly_change: 0.025, owed_by_year: [200000], loan: { rate: null, payment: null, source: null, note: "no_rate" as const } };
      setup(data({ assets: [unknown] }, { assets: [{ key: "home1", sell_year: 2036 }] }));
      expect(screen.getByText(/^≈ \$/)).toHaveTextContent("≈ $300,000");
      expect(screen.getByText(/^≈ \$/).getAttribute("title")).toMatch(/less \$200,000 owed on the loan today\./);
      expect(screen.getByText(/add the loan’s interest rate in/)).toHaveTextContent(/to project it\.$/);
      expect(screen.getByRole("link", { name: "Settings → Accounts" })).toHaveAttribute("href", "#setup/accounts");
    });

    it("shows equity vested by the year it's sold", () => {
      const acme = { key: "equity:acme", name: "Acme", kind: "equity", value: 10000, yearly_change: null, owed: 0,
        value_by_year: [10000, 25000, 40000], owed_by_year: [0], loan: null };
      setup(data({ assets: [acme] }, { inflation: 0, assets: [{ key: "equity:acme", sell_year: 2030 }] }));
      expect(screen.getByText(/^≈ \$/)).toHaveTextContent("≈ $40,000");
      expect(screen.getByText(/^≈ \$/)).toHaveAttribute("title", "Acme: $40,000 vested by 2030, at today’s share price. In today’s dollars.");
    });

    it("says proceeds are before selling costs and tax, and how a loan is handled", () => {
      setup(data({ assets: [home] }));
      expect(screen.getByText(/Proceeds are before selling costs \(often 6–8% of a home’s price\) and tax/)).toBeInTheDocument();
      expect(screen.getByText(/a loan’s payment comes off your spending once it’s paid off or sold, if it was counted as spending/i)).toBeInTheDocument();
      expect(screen.queryByText(/loan payment/)).not.toBeInTheDocument();   // no payment known: nothing to say about it
    });

    it("says in plain words when a loan's payment in spending ends, and when the plan takes it off", async () => {
      // $200,000 at 6% and $1,500 a month from October 2026: the last payment in 2045
      setup(data({ assets: [repaying()] }));
      expect(screen.getByText("Its $1,500/month loan payment is already in your spending, until it’s paid off in 2045; from 2046 the plan takes it off."))
        .toBeInTheDocument();
      await userEvent.click(screen.getByRole("checkbox", { name: /Home/ }));   // sold in 2051: paid off before then
      expect(screen.getByText(/until it’s paid off in 2045; from 2046 the plan takes it off/)).toBeInTheDocument();
      const year = screen.getByDisplayValue("2051");
      await userEvent.clear(year);
      await userEvent.type(year, "2040");
      expect(screen.getByText("Its $1,500/month loan payment is already in your spending, until it’s sold in 2040; from 2040 the plan takes it off."))
        .toBeInTheDocument();
    });

    it("under spending, says Runway's figure includes a loan's payment until it ends", () => {
      setup(data({ assets: [repaying()] }));
      expect(screen.getByText(/^Runway's figure, from your last six months\. It includes the \$1,500\/month payment on Home, until it’s paid off in 2045: the plan takes it off once it ends\.$/))
        .toBeInTheDocument();
    });

    it("in future dollars, says the same about loan payments; the projection's odds don't change", async () => {
      setup(data({ assets: [repaying()] }));
      const odds = screen.getByText(/^\d+%$/).textContent;
      await userEvent.click(screen.getByRole("radio", { name: "Future dollars" }));
      expect(screen.getByText(/It includes the \$1,500\/month payment on Home, until it’s paid off in 2045/)).toBeInTheDocument();
      expect(screen.getByText(/^\d+%$/).textContent).toBe(odds);
    });

    it("takes nothing off a spending figure you typed, and can go back to Runway's", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      setup(data({ assets: [repaying()] }));
      const field = screen.getByRole("spinbutton", { name: "Yearly spending in retirement" });
      await user.clear(field);
      await user.type(field, "48000");
      expect(screen.getByText(/^Your own figure, taken as it is: loan payments aren't added to it or taken off it\./)).toBeInTheDocument();
      expect(screen.getByText(/goes on until it’s paid off in 2045\. Your own spending figure is taken as it is, so make sure it has the payment in for those years\.$/))
        .toBeInTheDocument();
      await vi.advanceTimersByTimeAsync(800);
      let body = (vi.mocked(api).mock.calls.at(-1) as [string, { body: { plan: RetirementPlan } }])[1].body.plan;
      expect([body.spending, body.spending_own]).toEqual([48000, true]);
      await user.click(screen.getByRole("button", { name: "Use Runway's figure" }));
      expect(field).toHaveValue(55000);
      expect(screen.getByText(/^Runway's figure, from your last six months\. It includes the \$1,500\/month payment/)).toBeInTheDocument();
      await vi.advanceTimersByTimeAsync(800);
      body = (vi.mocked(api).mock.calls.at(-1) as [string, { body: { plan: RetirementPlan } }])[1].body.plan;
      expect([body.spending, body.spending_own]).toEqual([55000, false]);
    });

    it("keeps a saved plan's own spending figure as it is", () => {
      setup(data({ assets: [repaying()] }, { spending_own: true }));
      expect(screen.getByRole("button", { name: "Use Runway's figure" })).toBeInTheDocument();
      expect(screen.queryByText(/the plan takes it off/)).not.toBeInTheDocument();
    });

    it("says when a loan's payment isn't in spending, so the plan adds it until it ends", () => {
      setup(data({ assets: [repaying({ payment_counted: false })] }));
      expect(screen.getByText(/^Its \$1,500\/month loan payment isn’t in your spending: Runway didn’t find it in what you spent over the last six months \(a payment categorized as a transfer isn’t counted as spending\)\. So the plan adds it to your spending in retirement until it’s paid off in 2045\.$/))
        .toBeInTheDocument();
      expect(screen.getByText(/It's missing the \$1,500\/month payment on Home, until it’s paid off in 2045, which the plan adds while it's still paid\./)).toBeInTheDocument();
      expect(screen.queryByText(/the plan takes it off/)).not.toBeInTheDocument();
    });

    it("says when a loan's payment never pays it down, and whether it's in spending or added to it", () => {
      const below = { payment: 900, note: "payment_below_interest" as const, payoff_year: null };
      const { unmount } = setup(data({ assets: [repaying(below)] }));
      expect(screen.getByText("Its $900/month loan payment is already in your spending, and stays in it. It doesn’t cover the interest, so it never pays the loan down."))
        .toBeInTheDocument();
      unmount();
      setup(data({ assets: [repaying({ ...below, payment_counted: false })] }));
      expect(screen.getByText(/adds it to your spending in retirement for as long as the plan runs\. It doesn’t cover the interest, so it never pays the loan down\.$/))
        .toBeInTheDocument();
      expect(screen.queryByText(/stays in it/)).not.toBeInTheDocument();
    });

    it("asks for the rate when there's a payment but no rate", () => {
      setup(data({ assets: [repaying({ rate: null, note: "no_rate", payoff_year: null })] }));
      expect(screen.getByText("Its $1,500/month loan payment is already in your spending, and stays in it. Add the loan’s interest rate in Settings → Accounts to see when it ends."))
        .toBeInTheDocument();
      expect(screen.queryByText(/pay the loan down/)).not.toBeInTheDocument();
    });

    it("says a payment that outlasts the plan stays in spending", () => {
      setup(data({ assets: [repaying({ payoff_year: null })] }));   // projected, but not paid off within the plan's reach
      expect(screen.getByText("Its $1,500/month loan payment is already in your spending, and stays in it.")).toBeInTheDocument();
      expect(screen.queryByText(/pay the loan down/)).not.toBeInTheDocument();
    });

    it("lists a vehicle only for its loan's payment: it isn't counted or sold", () => {
      const car = repaying({ account_id: "auto", payoff_year: 2029 });
      setup(data({ assets: [{ ...car, key: "asset:car", name: "Car", kind: "vehicle", value: 30000 }] }));
      expect(screen.getByText("Vehicle")).toBeInTheDocument();
      expect(screen.queryByRole("checkbox", { name: /Car/ })).not.toBeInTheDocument();
      expect(screen.getByText("Its $1,500/month loan payment is already in your spending, until it’s paid off in 2029; from 2030 the plan takes it off."))
        .toBeInTheDocument();
      expect(screen.queryByText(/Tick one to sell it/)).not.toBeInTheDocument();   // nothing to sell
    });

    it("won't take a sale year before this one, and says when a kept one has passed", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      setup(data({ assets: [home] }, { assets: [{ key: "home1", sell_year: 2026, was: 2024 }] }));
      const year = screen.getByDisplayValue("2026");
      expect(year).toHaveAttribute("min", "2026");
      expect(screen.getByText("Was 2024, now past: counted as sold in 2026.")).toBeInTheDocument();
      await user.clear(year);
      await user.type(year, "2025");
      expect(screen.queryByText(/now past/)).not.toBeInTheDocument();
      expect(screen.getByText("Sell in 2026 or later.")).toBeInTheDocument();
      expect(year).toHaveAttribute("aria-invalid", "true");
      expect(screen.getByText(/^≈ \$/)).toHaveTextContent("≈ $300,000");   // counted this year: today's value less today's loan
    });

    describe("in future dollars", () => {
      const future = () => userEvent.click(screen.getByRole("radio", { name: "Future dollars" }));

      it("shows the home's value then and the loan's balance then, not deflated", async () => {
        const paying = { ...home, yearly_change: 0.025, owed_by_year: Array.from({ length: 26 }, (_, k) => 200000 - k * 8000),
          loan: { rate: 6.25, payment: 1840, source: "inferred" as const, note: null } };
        setup(data({ assets: [paying] }, { assets: [{ key: "home1", sell_year: 2036 }] }));
        expect(screen.getByText(/^≈ \$/)).toHaveTextContent("≈ $406,256");   // 500k less 93,744 today
        await future();
        // 500k × 1.025^10, less the 120k the loan's schedule says is owed in 2036
        expect(screen.getByText(/^≈ \$/)).toHaveTextContent("≈ $520,042");
        expect(screen.getByText(/^≈ \$/)).toHaveAttribute("title", "Home worth $640,042 in 2036, less $120,000 still owed on the loan at 6.25% and "
          + "$1,840 a month from recent payments. In 2036 dollars, at 2.5% a year inflation.");
      });

      it("says what today's balance of a loan it can't project comes to then", async () => {
        const unknown = { ...home, yearly_change: 0.025, owed_by_year: [200000], loan: { rate: null, payment: null, source: null, note: "no_rate" as const } };
        setup(data({ assets: [unknown] }, { assets: [{ key: "home1", sell_year: 2036 }] }));
        await future();
        expect(screen.getByText(/^≈ \$/)).toHaveTextContent("≈ $384,025");   // (500k − 200k) × 1.025^10
        expect(screen.getByText(/^≈ \$/).getAttribute("title")).toMatch(/less the \$200,000 owed on the loan today \(\$256,017 in 2036 dollars\)\. In 2036 dollars/);
      });

      it("shows equity at today's share price grown with inflation, as vested", async () => {
        const acme = { key: "equity:acme", name: "Acme", kind: "equity", value: 10000, yearly_change: null, owed: 0,
          value_by_year: [10000, 25000, 40000], owed_by_year: [0], loan: null };
        setup(data({ assets: [acme] }, { assets: [{ key: "equity:acme", sell_year: 2030 }] }));
        expect(screen.getByText(/^≈ \$/)).toHaveTextContent("≈ $40,000");   // level in today's dollars, not shrinking
        await future();
        expect(screen.getByText(/^≈ \$/)).toHaveTextContent("≈ $44,153");   // 40k × 1.025^4
        expect(screen.getByText(/^≈ \$/)).toHaveAttribute("title",
          "Acme: $44,153 vested by 2030, at today’s share price grown with inflation. In 2030 dollars, at 2.5% a year inflation.");
      });
    });
  });

  describe("today's or future dollars", () => {
    afterEach(() => vi.restoreAllMocks());
    const figure = (label: RegExp) => screen.getByText(label).nextElementSibling!;
    const p = project(plan(), 400000, 2026, []);
    const f = projectionIn(p, 2026, 0.025, "future");

    it("starts in today's dollars, saying what you enter is in them and what inflation it assumes", () => {
      setup();
      expect(screen.getByRole("radio", { name: "Today’s dollars" })).toBeChecked();
      expect(screen.getByText(/what you enter is in today’s dollars/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "2.5% a year" })).toBeInTheDocument();
      expect(figure(/^Invested at retirement$/)).toHaveTextContent(fmt0(p.atRetirement));
    });

    it("shows the figures in each year's dollars, and back", async () => {
      setup();
      await userEvent.click(screen.getByRole("radio", { name: "Future dollars" }));
      expect(figure(/^Invested at retirement · 2051$/)).toHaveTextContent(fmt0(f.atRetirement));
      expect(f.atRetirement).toBeCloseTo(p.atRetirement * 1.025 ** 25, 4);   // 2051 is 25 years out
      expect(figure(/^Invested at retirement/).nextElementSibling).toHaveTextContent(`${fmt0(f.low[25])} – ${fmt0(f.high[25])} likely`);
      expect(figure(/^Left at age 95 · 2081$/)).toHaveTextContent(fmt0(f.atEnd));
      expect(screen.getByText(/^\d+%$/)).toHaveTextContent(`${Math.round(p.success * 100)}%`);   // the odds don't change
      expect(screen.getByRole("img", { name: /^Projected investments by age, in each year’s dollars: median/ })).toBeInTheDocument();
      await userEvent.click(screen.getByRole("radio", { name: "Today’s dollars" }));
      expect(figure(/^Invested at retirement$/)).toHaveTextContent(fmt0(p.atRetirement));
      expect(screen.getByRole("img", { name: /^Projected investments by age, in today’s dollars/ })).toBeInTheDocument();
    });

    it("remembers the choice in this browser, without saving the plan", async () => {
      setup();
      await userEvent.click(screen.getByRole("radio", { name: "Future dollars" }));
      expect(localStorage.getItem("runway.planner.dollars")).toBe("future");
      await vi.advanceTimersByTimeAsync(800);
      expect(api).not.toHaveBeenCalled();
      cleanup();
      setup();
      expect(screen.getByRole("radio", { name: "Future dollars" })).toBeChecked();
      expect(figure(/^Invested at retirement · 2051$/)).toHaveTextContent(fmt0(f.atRetirement));
    });

    it("still switches when the browser won't keep it, starting from today's dollars", async () => {
      vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
      vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
      setup();
      expect(screen.getByRole("radio", { name: "Today’s dollars" })).toBeChecked();
      await userEvent.click(screen.getByRole("radio", { name: "Future dollars" }));
      expect(figure(/^Invested at retirement · 2051$/)).toHaveTextContent(fmt0(f.atRetirement));
    });

    it("takes you to the inflation it assumes", async () => {
      setup();
      await userEvent.click(screen.getByRole("button", { name: "2.5% a year" }));
      expect(screen.getByRole("spinbutton", { name: "Inflation" })).toHaveFocus();
    });

    it("follows the inflation you set", async () => {
      setup();
      await userEvent.click(screen.getByRole("radio", { name: "Future dollars" }));
      const infl = screen.getByRole("spinbutton", { name: "Inflation" });
      await userEvent.clear(infl);
      await userEvent.type(infl, "3");
      expect(screen.getByRole("button", { name: "3% a year" })).toBeInTheDocument();
      expect(figure(/^Invested at retirement · 2051$/)).toHaveTextContent(fmt0(p.atRetirement * 1.03 ** 25));
    });
  });

  describe("assumptions", () => {
    const details = () => screen.getByText("Assumptions", { selector: "summary span" }).closest("details")!;

    it("folds the set-once figures away once they're in, with a one-line summary", () => {
      setup(data({}, { income: [{ name: "Social Security", amount: 28800, person: 0, start_age: 67, end_age: null }] }));
      expect(details().open).toBe(false);
      expect(details().querySelector("[data-summary]")).toHaveTextContent(
        "Born 1986 · to age 95 · Social Security $28,800 a year at 67 · 5% returns (4% retired), 2.5% inflation");
      for (const name of ["Born in", "Plan until age", "Inflation", "Return while saving", "Social Security a year"]) {
        expect(details()).toContainElement(screen.getByLabelText(name));
      }
      expect(details()).not.toContainElement(screen.getByLabelText("Retires at · age"));   // changed more often: left out
    });

    it("is open while something's missing: Runway's sample plan, or a birth year that isn't one", () => {
      setup(data({ is_default: true }));
      expect(details().open).toBe(true);
      cleanup();
      setup(data({}, { people: [{ name: "Ann", birth_year: 0, retire_age: 65, savings: 0 }] }));
      expect(details().open).toBe(true);
    });

    it("names each person's birth year with two, and says when there's no income yet", () => {
      setup(data({}, { people: [{ name: "Ann", birth_year: 1986, retire_age: 65, savings: 0 }, { name: "Bo", birth_year: 1988, retire_age: 63, savings: 0 }] }));
      expect(details().querySelector("[data-summary]")).toHaveTextContent(/^Ann born 1986, Bo born 1988 · to age 95 · no retirement income yet · /);
    });

    it("opens to show a new partner's birth year, and to the inflation from the dollars switch", async () => {
      setup();
      await userEvent.click(screen.getByRole("button", { name: /Add a partner/ }));
      expect(details().open).toBe(true);
      cleanup();
      setup();
      await userEvent.click(screen.getByRole("button", { name: "2.5% a year" }));
      expect(details().open).toBe(true);
      expect(screen.getByRole("spinbutton", { name: "Inflation" })).toHaveFocus();
    });
  });

  describe("home equity and equity comp in the chart", () => {
    it("stacks a home's equity on the investments until it's sold, with a key", async () => {
      setup(data({ assets: [homeAsset({ owed: 200000 })] }));
      expect(screen.getByRole("list", { name: "Chart key" })).toHaveTextContent("Home equity");
      expect(screen.getByRole("img", { name: /with home equity$/ })).toBeInTheDocument();
      await userEvent.click(screen.getByText("Show as table"));
      expect(screen.getByRole("columnheader", { name: "Home equity" })).toBeInTheDocument();
      expect(within(screen.getAllByRole("row")[1]).getByText("$300,000")).toBeInTheDocument();   // this year: 500k less 200k owed
    });

    it("leaves vehicles out of it", () => {
      setup(data({ assets: [homeAsset({ key: "asset:car", name: "Car", kind: "vehicle" })] }));
      expect(screen.queryByRole("list", { name: "Chart key" })).not.toBeInTheDocument();
    });
  });

  describe("what the figures mean", () => {
    it("says what yearly savings is, and when the history is shorter than a year", () => {
      const { unmount } = setup();
      expect(screen.getByText(/\$18,000 a year, is what went into your investments in the last 12 months\. A rollover/)).toBeInTheDocument();
      unmount();
      setup(data({ computed: { annual_spending: 55000, yearly_savings: 3000, expected_return: 0.05, savings_measured: true, savings_since: "2026-05-31" } }));
      expect(screen.getByText(/only goes back to May\s31,\s2026/)).toBeInTheDocument();
    });

    it("doesn't call a figure typed on the old card a measurement", () => {
      setup(data({ computed: { annual_spending: 55000, yearly_savings: 20000, expected_return: 0.05, savings_measured: false, savings_since: null } }));
      expect(screen.queryByText(/what went into your investments/)).not.toBeInTheDocument();
    });

    it("with two people, says pay covers living costs until both have retired", async () => {
      setup();
      expect(screen.queryByText(/still working is assumed to cover/)).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: /Add a partner/ }));
      expect(screen.getByText(/the pay of whoever's still working is assumed to cover your living costs/)).toBeInTheDocument();
    });
  });

  it("asks before starting over, and doesn't clear anything on the first click", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: /Start over/ }));
    expect(screen.getByRole("button", { name: "Start over? This clears everything you entered here." })).toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
    expect(screen.getByRole("spinbutton", { name: "Yearly spending in retirement" })).toHaveValue(60000);
  });

  it("starts over from Runway's figures, saving that the plan is the default again", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: /Start over/ }));
    await userEvent.click(screen.getByRole("button", { name: /Start over\? This clears/ }));
    expect(api).toHaveBeenCalledWith("/api/investments/plan", { method: "POST", body: { plan: null } });
    await waitFor(() => expect(screen.getByLabelText("Born in")).toHaveValue(1986));   // year - 40
    expect(screen.getByText("Sample · based on default assumptions")).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Yearly spending in retirement" })).toHaveValue(55000);
  });

  it("keeps the plan and shows the error when starting over fails", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Locked"));
    setup();
    await userEvent.click(screen.getByRole("button", { name: /Start over/ }));
    await userEvent.click(screen.getByRole("button", { name: /Start over\? This clears/ }));
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

  it("gives an all-zero plan a $0 to $1k axis, with no repeated labels", () => {
    const zero = project(plan({ people: [{ name: "Ann", birth_year: 1986, retire_age: 65, savings: 0 }], spending: 0 }), 0, 2026, [], 20);
    const { container } = render(PlannerChart, { p: zero, names: ["You"] });
    const labels = [...container.querySelectorAll("svg text")].map((t) => t.textContent).filter((t) => t?.startsWith("$"));
    expect(labels.length).toBeGreaterThan(1);
    expect(new Set(labels).size).toBe(labels.length);
    expect(labels).toContain("$0");
    expect(labels).toContain("$1k");
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
