import { describe, expect, it } from "vitest";
import { agesFit, assumptionsLine, bornOk, copyPlan, newPartner, num, paymentLine, pctIn, personNames, planToError, planUsable, projectable,
  retireError, saleTitle, startingPlan, verdict, vestsTo } from "./planner";
import type { PlanAsset, RetirementPlan } from "./types";

const Y = 2026;
const plan = (over: Partial<RetirementPlan> = {}): RetirementPlan => ({
  people: [{ name: "Alex", birth_year: Y - 40, retire_age: 65, savings: 10_000 }], plan_to_age: 95, spending: 50_000,
  return_before: 0.05, return_after: 0.03, volatility: 0.1, inflation: 0.03, income: [], events: [], assets: [], ...over,
});
const home: PlanAsset = { key: "home:1", name: "House", kind: "home", value: 300_000, yearly_change: 0, owed: 50_000 };
const withLoan = (loan: Partial<NonNullable<PlanAsset["loan"]>>): PlanAsset => ({ ...home, owed_by_year: [50_000, 25_000, 0],
  loan: { rate: 6.25, payment: 700, source: "manual", note: null, payoff_year: 2028, payment_counted: true, ...loan } });

describe("numbers as typed", () => {
  it("counts an empty, half-typed or missing figure as nothing", () => {
    expect([num(5), num(""), num(NaN), num(Infinity), num(null), num(undefined), num("7")]).toEqual([5, 0, 0, 0, 0, 0, 0]);
  });
  it("shows a fraction as the percentage a field holds, without trailing dust", () => {
    expect([pctIn(0.025), pctIn(0.1), pctIn(0), pctIn(0.07 + 0.01)]).toEqual(["2.5", "10", "0", "8"]);
  });
});

describe("the plan as a form", () => {
  it("copies a plan so editing the copy leaves the original alone", () => {
    const p = plan(), c = copyPlan(p);
    c.people[0].name = "Sam";
    expect(p.people[0].name).toBe("Alex");
  });

  it("starts from Runway's figures for one person aged 40", () => {
    const s = startingPlan({ annual_spending: 48_000, yearly_savings: 12_000, expected_return: 0.06 }, Y);
    expect(s.people).toEqual([{ name: "You", birth_year: Y - 40, retire_age: 65, savings: 12_000 }]);
    expect(s).toMatchObject({ plan_to_age: 95, spending: 48_000, spending_own: false, return_before: 0.06, return_after: 0.04, inflation: 0.025 });
  });

  it("names people, and adds a partner with the first person's ages and nothing saved", () => {
    const p = plan({ people: [{ name: "", birth_year: 1980, retire_age: 62, savings: 1 }, { name: "", birth_year: 1982, retire_age: 60, savings: 2 }] });
    expect(personNames(p)).toEqual(["You", "Partner"]);
    expect(personNames(plan())).toEqual(["Alex"]);
    expect(newPartner(p)).toEqual({ name: "Partner", birth_year: 1980, retire_age: 62, savings: 0 });
  });
});

describe("ages that can't be", () => {
  it("flags retiring before the age you are now, and says what the age is", () => {
    expect(retireError({ name: "A", birth_year: Y - 70, retire_age: 65, savings: 0 }, Y)).toBe("At least 70, your age now");
    expect(retireError({ name: "A", birth_year: Y - 40, retire_age: 65, savings: 0 }, Y)).toBeNull();
    expect(retireError({ name: "A", birth_year: Y - 65, retire_age: 65, savings: 0 }, Y)).toBeNull();
  });

  it("says nothing while a birth year or age is still being typed", () => {
    expect(retireError({ name: "A", birth_year: 0, retire_age: 65, savings: 0 }, Y)).toBeNull();
    expect(retireError({ name: "A", birth_year: Y - 70, retire_age: NaN, savings: 0 }, Y)).toBeNull();
  });

  it("flags planning to an age at or before retirement", () => {
    expect(planToError(plan({ plan_to_age: 65 }))).toBe("Past the retirement age (65)");
    expect(planToError(plan({ plan_to_age: 66 }))).toBeNull();
    expect(planToError(plan({ plan_to_age: 0 }))).toBeNull();
  });

  it("projects only when everything is in and fits together", () => {
    expect(planUsable(plan(), Y)).toBe(true);
    expect(agesFit(plan(), Y)).toBe(true);
    expect(planUsable(plan({ plan_to_age: 60 }), Y)).toBe(false);
    expect(agesFit(plan({ plan_to_age: 60 }), Y)).toBe(false);
    expect(planUsable(plan({ people: [{ name: "A", birth_year: 0, retire_age: 65, savings: 0 }] }), Y)).toBe(false);
    expect(agesFit(plan({ people: [{ name: "A", birth_year: 0, retire_age: 65, savings: 0 }] }), Y)).toBe(true);
  });

  it("takes a birth year of someone 14 to 100 years old", () => {
    expect([bornOk(Y - 14, Y), bornOk(Y - 100, Y), bornOk(Y - 13, Y), bornOk(Y - 101, Y), bornOk("", Y)]).toEqual([true, true, false, false, false]);
  });
});

describe("projectable", () => {
  it("turns what's half-typed into numbers, and drops an income of a person who's gone", () => {
    const p = plan({
      people: [{ name: "A", birth_year: 1986, retire_age: 65, savings: NaN }], spending: NaN as unknown as number,
      income: [{ name: "Pension", amount: "" as unknown as number, person: 0, start_age: 67, end_age: NaN as unknown as number },
        { name: "Gone", amount: 5, person: 1, start_age: 67, end_age: null }],
      events: [{ name: "Roof", year: "" as unknown as number, amount: -10 }],
    });
    const out = projectable(p);
    expect(out.people[0].savings).toBe(0);
    expect(out.spending).toBe(0);
    expect(out.income).toEqual([{ name: "Pension", amount: 0, person: 0, start_age: 67, end_age: null }]);
    expect(out.events).toEqual([{ name: "Roof", year: 0, amount: -10 }]);
    expect(Number.isNaN(p.people[0].savings)).toBe(true);
  });
});

describe("plain words", () => {
  it("sums up the assumptions for one person and for a couple", () => {
    expect(assumptionsLine(plan({ income: [{ name: "Social Security", amount: 24_000, person: 0, start_age: 67, end_age: null }] })))
      .toBe("Born 1986 · to age 95 · Social Security $24,000 a year at 67 · 5% returns (3% retired), 3% inflation");
    expect(assumptionsLine(plan({ people: [{ name: "", birth_year: 1986, retire_age: 65, savings: 0 }, { name: "Sam", birth_year: 1988, retire_age: 65, savings: 0 }] })))
      .toBe("You born 1986, Sam born 1988 · to age 95 · no retirement income yet · 5% returns (3% retired), 3% inflation");
  });

  it("gives the chance the money lasts in a word", () => {
    expect([verdict(0.9), verdict(0.85), verdict(0.84), verdict(0.7), verdict(0.69)]).toEqual(["Likely", "Likely", "Uncertain", "Uncertain", "Unlikely"]);
  });

  it("says what vesting equity comes to once it's all vested, and nothing for anything else", () => {
    const shares: PlanAsset = { key: "equity:a", name: "Acme", kind: "equity", value: 10_000, yearly_change: null, owed: 0, value_by_year: [10_000, 20_000, 40_000], owed_by_year: [0], loan: null };
    expect(vestsTo(shares)).toBe(40_000);
    expect(vestsTo({ ...shares, value_by_year: [10_000] })).toBeNull();
    expect(vestsTo(home)).toBeNull();
  });
});

describe("a sale's tooltip", () => {
  it("says what the home's worth in the sale year, in today's dollars (a home that doesn't grow loses to inflation)", () => {
    expect(saleTitle(home, 2030, Y, plan(), "today")).toBe("House worth $266,546 in 2030, less $50,000 owed on the loan today. In today’s dollars.");
  });

  it("names the loan's terms and what's still owed when its payments are projected, or that it's paid off", () => {
    expect(saleTitle(withLoan({}), 2027, Y, plan(), "today")).toBe("House worth $291,262 in 2027, less $24,272 still owed on the loan at 6.25% and $700 a month as you set it. In today’s dollars.");
    expect(saleTitle(withLoan({ source: "plaid" }), 2029, Y, plan(), "today")).toBe("House worth $274,542 in 2029; the loan (6.25% and $700 a month from Plaid) is paid off by then. In today’s dollars.");
  });

  it("says so, and the inflation rate, in future dollars", () => {
    const t = saleTitle(home, 2036, Y, plan(), "future");
    expect(t).toContain("In 2036 dollars, at 3% a year inflation.");
    expect(t).toContain("owed on the loan today (");
    expect(t).toContain("in 2036 dollars)");
  });

  it("says vested for equity, at today's share price", () => {
    const shares: PlanAsset = { key: "equity:a", name: "Acme", kind: "equity", value: 10_000, yearly_change: null, owed: 0, value_by_year: [10_000, 20_000, 40_000], owed_by_year: [0], loan: null };
    expect(saleTitle(shares, 2028, Y, plan(), "today")).toBe("Acme: $40,000 vested by 2028, at today’s share price. In today’s dollars.");
    expect(saleTitle(shares, 2028, Y, plan(), "future")).toContain("vested by 2028, at today’s share price grown with inflation");
  });
});

describe("a loan's payment, in words", () => {
  it("says it's already in the spending and when the plan takes it off", () => {
    expect(paymentLine(withLoan({}), null, false)).toBe("Its $700/month loan payment is already in your spending, until it’s paid off in 2028; from 2029 the plan takes it off.");
    expect(paymentLine(withLoan({}), 2027, false)).toBe("Its $700/month loan payment is already in your spending, until it’s sold in 2027; from 2027 the plan takes it off.");
  });

  it("says it stays in the spending when nothing ends it", () => {
    expect(paymentLine(withLoan({ payoff_year: null }), null, false)).toBe("Its $700/month loan payment is already in your spending, and stays in it.");
  });

  it("says it's added when it was paid as a transfer, and doesn't claim to know when it can't tell", () => {
    expect(paymentLine(withLoan({ payment_counted: false }), null, false)).toBe(
      "Its $700/month loan payment was paid as a transfer, so it isn’t in your spending and the plan adds it to your spending in retirement until it’s paid off in 2028.");
    expect(paymentLine(withLoan({ payment_counted: null }), null, false)).toBe(
      "Runway couldn’t tell whether its $700/month loan payment is in your spending, so the plan leaves your spending as it is.");
  });

  it("goes on while spending is a figure you typed, and says why a payment never ends", () => {
    expect(paymentLine(withLoan({}), null, true)).toBe("Its $700/month loan payment goes on until it’s paid off in 2028.");
    expect(paymentLine(withLoan({ note: "payment_below_interest", payoff_year: null }), null, true)).toBe(
      "Its $700/month loan payment goes on past the end of the plan. It doesn’t cover the interest, so it never pays the loan down.");
    expect(paymentLine(withLoan({ note: "no_rate", payoff_year: null }), null, true)).toContain("Add the loan’s interest rate in Settings → Accounts");
  });
});
