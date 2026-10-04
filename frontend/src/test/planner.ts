import { render } from "@testing-library/svelte";
import { vi } from "vitest";
import { api } from "$lib/api";
import RetirementPlanner from "../lib/components/investments/RetirementPlanner.svelte";
import type {
  PlanAsset,
  PlanData,
  RetirementPlan,
} from "../lib/components/investments/types";

export const plan = (extra: Partial<RetirementPlan> = {}): RetirementPlan => ({
  people: [{ name: "Ann", birth_year: 1986, retire_age: 65, savings: 20000 }],
  plan_to_age: 95,
  spending: 60000,
  return_before: 0.05,
  return_after: 0.04,
  volatility: 0.1,
  inflation: 0.025,
  income: [],
  events: [],
  assets: [],
  ...extra,
});
export const data = (
  extra: Partial<PlanData> = {},
  p: Partial<RetirementPlan> = {},
): PlanData => ({
  plan: plan(p),
  is_default: false,
  current: 400000,
  year: 2026,
  assets: [],
  computed: {
    annual_spending: 55000,
    yearly_savings: 18000,
    expected_return: 0.05,
  },
  ...extra,
});
export const setup = (d = data()) => render(RetirementPlanner, { data: d });
export const homeAsset = (over: Partial<PlanAsset> = {}): PlanAsset => ({
  key: "home1",
  name: "Home",
  kind: "home",
  value: 500000,
  yearly_change: 0.03,
  owed: 0,
  ...over,
});
export const repaying = (
  loan: Partial<NonNullable<PlanAsset["loan"]>> = {},
): PlanAsset =>
  homeAsset({
    owed: 200000,
    owed_by_year: [200000, 0],
    loan: {
      rate: 6,
      payment: 1500,
      source: "manual",
      note: null,
      account_id: "mtg",
      payoff_year: 2045,
      payment_counted: true,
      ...loan,
    },
  });

export function resetPlanner() {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({});
  localStorage.clear();
}
