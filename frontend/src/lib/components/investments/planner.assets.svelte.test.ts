// @vitest-environment jsdom
import { screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));

import { api } from "$lib/api";
import type { RetirementPlan } from "./types";
import { data, homeAsset, repaying, resetPlanner, setup } from "../../../test/planner";

beforeEach(resetPlanner);
afterEach(() => vi.useRealTimers());

describe("RetirementPlanner", () => {
  describe("assets", () => {
    const home = homeAsset({ owed: 200000 });

    it("has no assets section when there are none", () => {
      setup();
      expect(screen.queryByText(/can be sold into the plan here/)).not.toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: /Homes/ })).not.toBeInTheDocument();
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

    it("counts today's balance when the loan can't be projected, without a note about it", () => {
      const unknown = { ...home, yearly_change: 0.025, owed_by_year: [200000], loan: { rate: null, payment: null, source: null, note: "no_rate" as const } };
      setup(data({ assets: [unknown] }, { assets: [{ key: "home1", sell_year: 2036 }] }));
      expect(screen.getByText(/^≈ \$/)).toHaveTextContent("≈ $300,000");
      expect(screen.getByText(/^≈ \$/).getAttribute("title")).toMatch(/less \$200,000 owed on the loan today\./);
      expect(screen.queryByText(/to project it/)).not.toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "Settings → Accounts" })).not.toBeInTheDocument();
    });

    it("shows equity vested by the year it's sold", () => {
      const acme = { key: "equity:acme", name: "Acme", kind: "equity", value: 10000, yearly_change: null, owed: 0,
        value_by_year: [10000, 25000, 40000], owed_by_year: [0], loan: null };
      setup(data({ assets: [acme] }, { inflation: 0, assets: [{ key: "equity:acme", sell_year: 2030 }] }));
      expect(screen.getByText(/^≈ \$/)).toHaveTextContent("≈ $40,000");
      expect(screen.getByText(/^≈ \$/)).toHaveAttribute("title", "Acme: $40,000 vested by 2030, at today’s share price. In today’s dollars.");
    });

    it("says what equity still vesting comes to once it's all vested", () => {
      const acme = { key: "equity:acme", name: "Acme", kind: "equity", value: 10000, yearly_change: null, owed: 0,
        value_by_year: [10000, 25000, 40000], owed_by_year: [0], loan: null };
      const done = { ...acme, key: "equity:done", name: "Doneco", value: 5000, value_by_year: [5000] };
      setup(data({ assets: [acme, done] }));
      expect(screen.getByText("$10,000 vested today · $40,000 once all vested")).toBeInTheDocument();
      expect(screen.getByText("$5,000")).toBeInTheDocument();   // all vested already: just what it's worth
    });

    it("doesn’t spell out the rules for selling and loans", () => {
      setup(data({ assets: [home] }));
      expect(screen.queryByText(/Proceeds are before selling costs/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Tick one to sell it/)).not.toBeInTheDocument();
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

    it("under spending, has no note about Runway's figure while it's the one in use", () => {
      setup(data({ assets: [repaying()] }));
      expect(screen.queryByText(/Runway's figure, from your last six months/)).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Use Runway's figure" })).not.toBeInTheDocument();
    });

    it("in future dollars, says the same about loan payments; the projection's odds don't change", async () => {
      setup(data({ assets: [repaying()] }));
      const odds = screen.getByText(/^\d+%$/).textContent;
      await userEvent.click(screen.getByRole("radio", { name: "Future dollars" }));
      expect(screen.getByText(/Its \$1,500\/month loan payment is already in your spending, until it’s paid off in 2045/)).toBeInTheDocument();
      expect(screen.getByText(/^\d+%$/).textContent).toBe(odds);
    });

    it("takes nothing off a spending figure you typed, and can go back to Runway's", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      setup(data({ assets: [repaying()] }));
      const field = screen.getByRole("textbox", { name: "Yearly spending in retirement" });
      await user.clear(field);
      await user.type(field, "48000");
      expect(screen.queryByText(/taken as it is/)).not.toBeInTheDocument();
      expect(screen.getByText("Its $1,500/month loan payment goes on until it’s paid off in 2045.")).toBeInTheDocument();
      await vi.advanceTimersByTimeAsync(800);
      let body = (vi.mocked(api).mock.calls.at(-1) as [string, { body: { plan: RetirementPlan } }])[1].body.plan;
      expect([body.spending, body.spending_own]).toEqual([48000, true]);
      await user.click(screen.getByRole("button", { name: "Use Runway's figure" }));
      expect(field).toHaveValue("55000");
      expect(screen.queryByRole("button", { name: "Use Runway's figure" })).not.toBeInTheDocument();
      expect(screen.getByText(/already in your spending, until it’s paid off in 2045/)).toBeInTheDocument();
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
      expect(screen.getByText("Its $1,500/month loan payment was paid as a transfer, so it isn’t in your spending and the plan adds it to your spending in retirement until it’s paid off in 2045."))
        .toBeInTheDocument();
      expect(screen.queryByText(/It's missing/)).not.toBeInTheDocument();
      expect(screen.queryByText(/categorized as a transfer/)).not.toBeInTheDocument();
      expect(screen.queryByText(/the plan takes it off/)).not.toBeInTheDocument();
    });

    it("says when Runway can't tell whether a loan's payment is in spending, and leaves spending alone", () => {
      setup(data({ assets: [repaying({ payment_counted: null })] }));
      expect(screen.getByText("Runway couldn’t tell whether its $1,500/month loan payment is in your spending, so the plan leaves your spending as it is."))
        .toBeInTheDocument();
      expect(screen.queryByText(/which the plan adds/)).not.toBeInTheDocument();
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

    it("doesn't list a vehicle: it isn't counted or sold (its loan's payment is still in spending until paid off)", () => {
      const car = repaying({ account_id: "auto", payoff_year: 2029 });
      setup(data({ assets: [{ ...car, key: "asset:car", name: "Car", kind: "vehicle", value: 30000 }] }));
      expect(screen.queryByText("Car")).not.toBeInTheDocument();
      expect(screen.queryByText("Vehicle")).not.toBeInTheDocument();
      expect(screen.queryByText("Homes & other assets")).not.toBeInTheDocument();   // nothing else to list
    });

    it("lists a loan against nothing for its payment, with what's owed: it isn't sold", () => {
      const stu = repaying({ account_id: "stu", payoff_year: 2029 });
      const none = { ...stu, key: "loan:exp", name: "Car Loan", kind: "loan", value: 0, owed: 30000,
        loan: { rate: null, payment: null, source: null, note: "no_rate" as const, account_id: "exp", payoff_year: null, payment_counted: null } };
      setup(data({ assets: [{ ...stu, key: "loan:stu", name: "Student Loan", kind: "loan", value: 0, owed: 12000 }, none] }));
      expect(screen.getByRole("heading", { name: "Homes, other assets & loans" })).toBeInTheDocument();
      expect(screen.getByText("Loan · $12,000 owed")).toBeInTheDocument();
      expect(screen.getByText("Loan · $30,000 owed")).toBeInTheDocument();
      expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
      expect(screen.getByText("Its $1,500/month loan payment is already in your spending, until it’s paid off in 2029; from 2030 the plan takes it off."))
        .toBeInTheDocument();
      expect(screen.getAllByText(/loan payment/)).toHaveLength(1);   // no payment known for the new one: nothing to say
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
});
