// Churning data for component tests: a page's reply with just enough in it, each piece overridable.
import { api } from "$lib/api";
import { vi } from "vitest";
import type { BankBonus, Benefit, ChurnCard, Churning, FoundDraft, Wish } from "./types";

/** The requests made to a path (or paths matching a pattern), from a test file that mocks `$lib/api`. */
export const calls = (path: string | RegExp) =>
  vi.mocked(api).mock.calls.filter((c) => (typeof path === "string" ? c[0] === path : path.test(c[0] as string)));
/** A request's JSON body. */
export const bodyOf = (call: unknown[]) => (call[1] as { body?: Record<string, unknown> } | undefined)?.body;

export const benefit = (over: Partial<Benefit> = {}): Benefit => ({
  id: 1, card_id: 1, name: "Travel credit", kind: "credit", amount: 300, period: "annual", basis: "anniversary", annual_value: null, guests: null,
  counts: 1, remind: 1, remind_days: null, expires_on: null, preset: "travel_credit", notes: null, active: 1, period_start: "2026-03-14",
  period_end: "2027-03-13", used: 0, used_count: 0, remaining: 300, value_per_year: 300, lead_days: 30, days_left: 165, expiring: false,
  remind_now: false, uses: [], ...over,
});

export const card = (over: Partial<ChurnCard> = {}): ChurnCard => ({
  id: 1, owner: "Alex", issuer: "capital_one", product: "Venture X", family: null, account_id: null, opened_on: "2025-03-14", closed_on: null,
  status: "open", changed_from: null, authorized_user: 0, business: 0, annual_fee: 395, currency: "c1", base_rate: 2,
  earn_note: null, bonus: null, bonus_spend: null, bonus_months: 3, bonus_deadline: null, bonus_earned_on: null, manual_spend: null,
  eligible_on: null, notes: null, portal_name: null, plan: "undecided", plan_target: null, plan_date: null, plan_remind_days: 30,
  plan_done_on: null, plan_new_id: null, hide_upcoming: 0, plan_due: null, plan_active: false, benefits: [], benefits_value: 0,
  net_fee: 395, points_note: null, rates: [], currency_name: "Capital One miles", cents: 1.4, bonus_value: null, fee_due: "2027-03-14",
  deadline: null, spent: null, spend_source: "manual", bonus_state: null, counts_524: true, falls_off: "2027-03-14",
  eligibility: { status: "now", on: null, why: "", override: false }, points_ytd: null, value_ytd: null, ...over,
});

export const bankBonus = (over: Partial<BankBonus> = {}): BankBonus => ({
  id: 1, owner: "Alex", bank: "Chase", account_type: "checking", account_id: null, opened_on: "2026-01-05", bonus: 300, dd_total: 500, dd_count: null,
  debit_count: null, min_balance: null, hold_until: null, other_reqs: null, deadline_days: 90, deadline: "2026-04-05", post_days: 30, manual_dd: null,
  manual_debits: null, status: "open", received_on: null, received_amount: null, closed_on: null, monthly_fee: 0, fee_waiver: null, early_close_fee: null,
  keep_open_days: null, repeat_months: null, once_per_lifetime: 0, eligible_on: null, notes: null,
  progress: { dd_total: 0, dd_count: 0, debits: 0, balance: null, balance_ok: null, source: "auto" }, state: "active", due: "2026-12-05", expected_on: null,
  safe_close_on: null, fee_reminder: null, eligibility: { status: "now", on: null, why: "", override: false }, ...over,
}) as unknown as BankBonus;

export const found = (over: Partial<FoundDraft> = {}): FoundDraft => ({
  account_id: "acct-1", account_name: "Chase Sapphire Reserve (8814)", org: "Chase Bank Alex", owner: "Alex", issuer: "chase",
  product: "Sapphire Reserve", business: 0, annual_fee: 795, opened_on: "2024-03-02", opened_on_estimate: true, ...over,
});

export const wish = (over: Partial<Wish> = {}): Wish => ({
  id: 1, owner: "Alex", kind: "card", issuer: "chase", bank: null, product: "Sapphire Preferred", family: null, business: 0, annual_fee: 95,
  bonus: 75000, currency: "ur", bonus_spend: 5000, bonus_months: 3, account_type: null, requirements: null, repeat_months: null,
  once_per_lifetime: 0, offer_expires_on: null, priority: 1, status: "wanted", wait_until: null, min_score: null, assume_prior_planned: 0,
  notes: null, apply_url: null, applied_on: null, applied_id: null, blockers: [], hints: [], earliest_apply: null, ready: false, ...over,
});

export const churning = (over: Partial<Churning> = {}): Churning => ({
  today: "2026-09-30", people: ["Alex", "Sam"], owners: ["Alex", "Sam"], cards: [], bank: [], bank_income: {}, five24: {}, upcoming: [],
  rewards: {}, tasks: [],
  currencies: [
    { key: "cash", name: "Cash back", kind: "cash", cents: 1, default: 1, custom: false, overridden: false, estimate: false, as_of: null, source_note: "" },
    { key: "ur", name: "Chase Ultimate Rewards", kind: "bank", cents: 1.5, default: 1.5, custom: false, overridden: false, estimate: true, as_of: "2026-06-01", source_note: "Typical redemption value." },
    { key: "aa", name: "American AAdvantage", kind: "airline", cents: 1.4, default: 1.4, custom: false, overridden: true, estimate: false, as_of: "2026-06-01", source_note: "" },
    { key: "united", name: "United MileagePlus", kind: "airline", cents: 1.2, default: 1.2, custom: false, overridden: false, estimate: true, as_of: "2026-06-01", source_note: "" },
    { key: "hyatt", name: "World of Hyatt", kind: "hotel", cents: 1.7, default: 1.7, custom: false, overridden: false, estimate: true, as_of: "2026-06-01", source_note: "" },
  ],
  currency_groups: [
    { kind: "bank", label: "Bank points", keys: ["ur"] }, { kind: "airline", label: "Airline miles", keys: ["aa", "united"] },
    { kind: "hotel", label: "Hotel points", keys: ["hyatt"] }, { kind: "cash", label: "Cash back", keys: ["cash"] },
  ],
  values_as_of: "2026-06-01", values_note: "Estimates, not official values.",
  issuers: [{ key: "chase", name: "Chase", rule: "5/24" }, { key: "capital_one", name: "Capital One", rule: "" }],
  plans: ["undecided", "keep", "close", "product_change"],
  categories: [{ name: "Travel", parent: null }, { name: "Restaurants", parent: null }, { name: "Hotels", parent: "Travel" }],
  base_marker: "*",
  benefit_presets: [{ group: "Lounges & airline", key: "lounge", name: "Lounge access", kind: "access", period: "annual", basis: "calendar" }],
  benefit_kinds: [{ key: "credit", name: "Credit" }, { key: "access", name: "Access" }, { key: "status", name: "Status" }, { key: "other", name: "Other" }],
  benefit_periods: [{ key: "annual", name: "Yearly" }, { key: "monthly", name: "Monthly" }, { key: "one_time", name: "One time" }],
  benefit_bases: [{ key: "calendar", name: "Calendar year" }, { key: "anniversary", name: "Cardmember year" }],
  wishlist: [], scores: {}, alert_prefs: [], accounts: [], bank_accounts: [], ...over,
});
