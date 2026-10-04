// What the retirement planner's tests share: a plan and its data, a way to show it, and the timers it needs reset.
import { render } from "@testing-library/svelte";
import { vi } from "vitest";
import { api } from "$lib/api";
import RetirementPlanner from "../lib/components/investments/RetirementPlanner.svelte";
import type { PlanAsset, PlanData, RetirementPlan } from "../lib/components/investments/types";

export const plan = (extra: Partial<RetirementPlan> = {}): RetirementPlan => ({
  people: [{ name: "Ann", birth_year: 1986, retire_age: 65, savings: 20000 }], plan_to_age: 95, spending: 60000,
  return_before: 0.05, return_after: 0.04, volatility: 0.1, inflation: 0.025, income: [], events: [], assets: [], ...extra,
});
export const data = (extra: Partial<PlanData> = {}, p: Partial<RetirementPlan> = {}): PlanData => ({
  plan: plan(p), is_default: false, current: 400000, year: 2026, assets: [],
  computed: { annual_spending: 55000, yearly_savings: 18000, expected_return: 0.05 }, ...extra,
});
export const setup = (d = data()) => render(RetirementPlanner, { data: d });
export const homeAsset = (over: Partial<PlanAsset> = {}): PlanAsset => ({
  key: "home1", name: "Home", kind: "home", value: 500000, yearly_change: 0.03, owed: 0, ...over,
});
// $200,000 at 6% and $1,500 a month from October 2026: the last payment in 2045 (runway/loans.py works out the year).
export const repaying = (loan: Partial<NonNullable<PlanAsset["loan"]>> = {}): PlanAsset => homeAsset({
  owed: 200000, owed_by_year: [200000, 0],
  loan: { rate: 6, payment: 1500, source: "manual", note: null, account_id: "mtg", payoff_year: 2045, payment_counted: true, ...loan },
});

/** Every edit schedules a save 700ms later. On real timers a test that edits and ends leaves that save pending, and it
 * lands in the next test (a second call where one is expected). Fake timers are dropped after each test, and
 * shouldAdvanceTime keeps user-event's own small delays moving. The dollars switch is remembered in localStorage, so
 * each test starts without a choice. Call it from beforeEach. */
export function resetPlanner() {
  vi.useFakeTimers({ shouldAdvanceTime: true }); vi.mocked(api).mockReset(); vi.mocked(api).mockResolvedValue({}); localStorage.clear();
}
