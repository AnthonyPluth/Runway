import { describe, expect, it } from "vitest";
import {
  KIND_LABEL, bankLeft, bankOrder, benefitBoard, benefitOrder, benefitState, benefitSummary, bonusLabel, canUse, cardOrder, currencyGroups, daysUntil,
  eligibilityText, feesDue, five24Line, guestsText, mine, ownerChoices, planLine, points, ratesPayload, ratesText, reorder,
  scoreProgress, spendProgress, splitWishes, usesText, valueSource, wishName,
} from "./churning";
import { benefit, card } from "./fixtures";
import type { BankBonus, Benefit, ChurnCard, Currency, Eligibility, Five24, Wish } from "./types";

const TODAY = "2026-09-29";
// Dates come with non-breaking spaces (so they never wrap); compare them as plain text.
const sp = (s: string) => s.replace(/\u00a0/g, " ");
const e = (x: Partial<Eligibility>): Eligibility => ({ status: "now", on: null, why: "", override: false, ...x });

describe("churning helpers", () => {
  it("filters by person", () => {
    const items = [{ owner: "Alex" }, { owner: "Sam" }, { owner: null }];
    expect(mine(items, "")).toHaveLength(3);
    expect(mine(items, "Sam")).toEqual([{ owner: "Sam" }]);
  });

  it("counts days and shortens points", () => {
    expect(daysUntil("2026-10-01", TODAY)).toBe(2);
    expect(daysUntil("2027-03-29", TODAY)).toBe(181);   // across a DST change, still whole days
    expect(points(60000)).toBe("60k");
    expect(points(1250)).toBe("1,250");
    expect(points(null)).toBe("—");
    expect(bonusLabel(200, "cash", "Cash back")).toBe("$200");
    expect(bonusLabel(75000, "ur", "Chase Ultimate Rewards")).toBe("75k Ultimate Rewards");
    expect(bonusLabel(null, "ur", "x")).toBe("");
  });

  it("works out spending progress", () => {
    expect(spendProgress(1500, 6000)).toEqual({ share: 0.25, left: 4500 });
    expect(spendProgress(7000, 6000)).toEqual({ share: 1, left: 0 });
    expect(spendProgress(null, null)).toEqual({ share: 0, left: 0 });
  });

  it("words eligibility", () => {
    expect(sp(eligibilityText(e({}), TODAY))).toBe("Now");
    expect(sp(eligibilityText(e({ status: "later", on: "2027-09-15" }), TODAY))).toBe("Sep 15, 2027");
    expect(sp(eligibilityText(e({ status: "later", on: "2027-09-15", override: true }), TODAY))).toBe("Sep 15, 2027 (your date)");
    expect(sp(eligibilityText(e({ status: "never" }), TODAY))).toBe("Never (once per lifetime)");
    expect(sp(eligibilityText(e({ status: "held", on: TODAY }), TODAY))).toBe("After you close or downgrade it");
    expect(sp(eligibilityText(e({ status: "held", on: "2027-01-01" }), TODAY))).toBe("After you close it, from Jan 1, 2027");
    expect(sp(eligibilityText(e({ status: "unknown" }), TODAY))).toBe("Rule unknown");
  });

  it("sums up 5/24", () => {
    const f = { count: 6, under: false, under_on: "2027-01-10", next_fall_off: "2026-10-15" } as Five24;
    expect(sp(five24Line(f).next)).toBe("Under 5/24 on Jan 10, 2027");
    expect(sp(five24Line({ ...f, count: 3, under: true, under_on: null }).next)).toBe("2/24 on Oct 15, 2026");
    expect(five24Line(undefined).count).toBe("0/24");
    expect(five24Line(undefined).next).toBe("No cards yet");
  });

  it("sorts, totals fees and lists what's left of a bank bonus", () => {
    const c = (id: number, status: string, opened: string, fee_due: string | null = null, annual_fee = 0) =>
      ({ id, status, opened_on: opened, fee_due, annual_fee }) as ChurnCard;
    const cards = [c(1, "closed", "2026-01-01"), c(2, "open", "2024-01-01", "2026-10-10", 95), c(3, "open", "2025-01-01", "2027-06-01", 550)];
    expect([...cards].sort(cardOrder).map((x) => x.id)).toEqual([3, 2, 1]);
    expect(feesDue(cards, TODAY)).toEqual({ total: 95, count: 1 });
    const b = { id: 1, state: "active", opened_on: "2026-08-01", dd_total: 1000, dd_count: 2, debit_count: 1, min_balance: 1500,
      progress: { dd_total: 400, dd_count: 1, debits: 1, balance: 1000, balance_ok: false } } as BankBonus;
    expect(bankLeft(b)).toEqual(["$600 more direct deposits", "1 more deposit", "$500 more to reach the minimum balance"]);
    const done = { ...b, id: 2, state: "received" } as BankBonus;
    expect([done, b].sort(bankOrder).map((x) => x.id)).toEqual([1, 2]);
  });
});

describe("churning 2 helpers", () => {
  it("labels every kind of Upcoming item", () => {
    for (const k of ["plan", "benefit", "apply", "offer_ends"] as const) expect(KIND_LABEL[k]).toBeTruthy();
  });

  it("words a card's plan and its benefits", () => {
    const c = (x: Partial<ChurnCard>) => ({ plan: "undecided", plan_target: null, plan_due: null, plan_done_on: null, ...x }) as ChurnCard;
    expect(planLine(c({}))).toBe("");
    expect(planLine(c({ plan: "keep" }))).toBe("Keeping it");
    expect(sp(planLine(c({ plan: "product_change", plan_target: "Freedom", plan_due: "2026-10-20" })))).toBe("Product change to Freedom by Oct 20");
    expect(sp(planLine(c({ plan: "close", plan_done_on: "2026-09-01" })))).toBe("Done Sep 1, 2026");
    expect(benefitSummary({ benefits: [], benefits_value: 0, net_fee: 95 })).toBe("");
    const two = [{}, {}] as Benefit[];
    expect(benefitSummary({ benefits: two, benefits_value: 650, net_fee: -100 })).toBe("Benefits $650/yr · net fee −$100");
    expect(benefitSummary({ benefits: two, benefits_value: 300, net_fee: 250 })).toBe("Benefits $300/yr · net fee $250");
  });

  it("marks portal-only rates and builds the rates a form sends", () => {
    expect(ratesText([{ category: "Travel", multiplier: 5 }, { category: "Hotels", multiplier: 10, portal_only: true }], "Capital One Travel"))
      .toBe("5x Travel, 10x Hotels (via Capital One Travel)");
    expect(ratesText([{ category: "Hotels", multiplier: 10, portal_only: true }])).toBe("10x Hotels (portal)");
    expect(ratesPayload("2", [{ category: "Hotels", multiplier: "10", portal_only: true }])).toEqual([
      { category: "*", multiplier: 2, portal_only: false }, { category: "Hotels", multiplier: 10, portal_only: true }]);
    expect(ratesPayload("", [{ category: "", multiplier: "", portal_only: false }])).toEqual([{ category: "", multiplier: null, portal_only: false }]);
  });

  it("offers the owners, the current one even if it's no longer a person, and Joint only when asked", () => {
    expect(ownerChoices(["Alex", "Sam"], "Alex")).toEqual(["Alex", "Sam"]);
    expect(ownerChoices(["Alex"], "Pat")).toEqual(["Alex", "Pat"]);
    expect(ownerChoices(["Alex", "Joint"], "Joint")).toEqual(["Alex"]);
    expect(ownerChoices(["Alex"], "Joint", true)).toEqual(["Alex", "Joint"]);
    // alphabetical whatever order people signed in, an old name slotted in, and Joint still last
    expect(ownerChoices(["Sam", "alex", "Pat"], "Chris", true)).toEqual(["alex", "Chris", "Pat", "Sam", "Joint"]);
  });

  it("groups currencies and says where each value came from", () => {
    const cur = (key: string, over: Partial<Currency> = {}) => ({ key, name: key, kind: "bank", overridden: false, custom: false, as_of: null, ...over }) as Currency;
    const d = { currencies: [cur("ur"), cur("aa", { kind: "airline" }), cur("mine", { custom: true }), cur("ua", { kind: "airline" })],
      currency_groups: [{ kind: "bank", label: "Bank points", keys: ["ur"] }, { kind: "airline", label: "Airlines", keys: ["aa", "ua"] }, { kind: "hotel", label: "Hotels", keys: ["gone"] }] };
    const g = currencyGroups(d as never);
    expect(g.map((x) => [x.label, x.currencies.map((c) => c.key)])).toEqual([["Bank points", ["ur"]], ["Airlines", ["aa", "ua"]], ["Other", ["mine"]]]);
    expect(sp(valueSource(cur("ur"), "2026-06-15"))).toBe("estimate (as of Jun 2026)");
    expect(sp(valueSource(cur("ur"), "2026-09"))).toBe("estimate (as of Sep 2026)");   // the server dates them by month
    expect(valueSource(cur("ur", { overridden: true }), "2026-06-15")).toBe("your value");
    expect(valueSource(cur("mine", { custom: true }), "2026-06-15")).toBe("your currency");
  });

  it("describes a benefit's period and whether it can be marked used", () => {
    const b = (x: Partial<Benefit>) => ({ kind: "credit", period: "annual", amount: 300, used: 0, remaining: 300, used_count: 0, period_end: "2026-12-31", ...x }) as Benefit;
    expect(sp(benefitState(b({})))).toBe("$0 of $300 used · resets Dec 31");
    expect(sp(benefitState(b({ used: 300, remaining: 0 })))).toBe("All $300 used · resets Dec 31");
    expect(benefitState(b({ kind: "other", amount: null, period_end: null }))).toBe("Not used this period");
    expect(benefitState(b({ kind: "other", amount: null, period_end: null, used_count: 3 }))).toBe("Used 3 times this period");
    expect(sp(benefitState(b({ kind: "other", amount: null, used_count: 1 })))).toBe("Used once this period · resets Dec 31");
    // A perk: who gets in, and how often you've been; never "not used".
    expect(benefitState(b({ kind: "access", amount: null, guests: 2 }))).toBe("Cardholder + 2 guests");
    expect(benefitState(b({ kind: "access", amount: null, guests: 0, used_count: 2 }))).toBe("Cardholder only · used twice this period");
    expect(benefitState(b({ kind: "access", amount: null, guests: null, used_count: 1 }))).toBe("Used once this period");
    expect(benefitState(b({ kind: "status", amount: null, guests: 1 }))).toBe("Included");   // guests are a lounge's, not status's
    expect(canUse(b({}))).toBe(true);
    expect(canUse(b({ used: 300, remaining: 0 }))).toBe(false);
    expect(canUse(b({ kind: "other", amount: null, used_count: 1 }))).toBe(false);
    expect(canUse(b({ kind: "access", amount: null, used_count: 1 }))).toBe(true);   // another lounge visit
  });

  it("words guests and uses, and orders a card's benefits credits first", () => {
    expect([guestsText(null), guestsText(0), guestsText(1), guestsText(2)]).toEqual(["", "Cardholder only", "Cardholder + 1 guest", "Cardholder + 2 guests"]);
    expect(usesText(benefit({ uses: [] }))).toBe("");
    expect(sp(usesText(benefit({ uses: [{ id: 1, amount_used: 100, used_on: "2026-09-01" }, { id: 2, amount_used: 50, used_on: "2026-09-20" }] })))).toBe("Used $100 on Sep 1, $50 on Sep 20");
    expect(sp(usesText(benefit({ uses: [{ id: 1, amount_used: null, used_on: "2026-09-01" }] })))).toBe("Used Sep 1");
    const list = [benefit({ name: "Priority Pass lounges", kind: "access" }), benefit({ name: "Hotel status", kind: "status" }),
      benefit({ name: "Free night", kind: "other" }), benefit({ name: "Travel credit" }), benefit({ name: "Capital One Lounges", kind: "access" })];
    expect(list.sort(benefitOrder).map((x) => x.name)).toEqual(["Travel credit", "Free night", "Capital One Lounges", "Priority Pass lounges", "Hotel status"]);
  });

  it("sorts every open card's benefits into expiring, still to use and used", () => {
    const soon = benefit({ id: 1, name: "Lyft", amount: 10, remaining: 10, expiring: true, days_left: 5 });
    const later = benefit({ id: 2, name: "Uber", expiring: true, days_left: 2 });
    const open = benefit({ id: 3, name: "Hotel", amount: 300, remaining: 150, used: 150 });
    const done = benefit({ id: 4, name: "Dining", amount: 100, remaining: 0, used: 100, value_per_year: 100 });
    const off = benefit({ id: 5, name: "Old", active: 0 });
    const pp = benefit({ id: 7, name: "Priority Pass lounges", kind: "access", amount: null, used: null, remaining: null, value_per_year: 0, guests: 2 });
    const c1 = benefit({ id: 8, name: "Capital One Lounges", kind: "access", amount: null, used: null, remaining: null, value_per_year: 0, used_count: 1 });
    const closedCard = card({ id: 2, status: "closed", benefits: [benefit({ id: 6, name: "Gone" })] });
    const b = benefitBoard([card({ benefits: [soon, later, open, done, off, pp, c1] }), closedCard]);
    expect(b.expiring.map((r) => r.b.name)).toEqual(["Uber", "Lyft"]);   // soonest first
    expect(b.available.map((r) => r.b.name)).toEqual(["Hotel"]);
    expect(b.used.map((r) => r.b.name)).toEqual(["Dining"]);
    expect(b.perks.map((r) => r.b.name)).toEqual(["Capital One Lounges", "Priority Pass lounges"]);   // on all year, used or not
    expect([b.left, b.usedAmount, b.value]).toEqual([460, 250, 1000]);   // left 10+300+150; used 150+100; worth 300×3+100
  });

  it("shows a score against what a planned item wants, and reorders a person's list", () => {
    const w = (id: number, owner: string, priority: number, over: Partial<Wish> = {}) => ({ id, owner, priority, status: "wanted", kind: "card", min_score: null, ...over }) as Wish;
    const scores = { Alex: { owner: "Alex", score: 705, as_of: "2026-09-01", source: null, history: [] } };
    expect(scoreProgress(w(1, "Alex", 1, { min_score: 740 }), scores)).toBe("705 of 740 wanted");
    expect(scoreProgress(w(1, "Sam", 1, { min_score: 740 }), scores)).toBe("740 wanted · no score entered");
    expect(scoreProgress(w(1, "Alex", 1), scores)).toBeNull();
    const open = [w(1, "Alex", 1), w(2, "Sam", 1), w(3, "Alex", 2), w(4, "Alex", 3)];
    expect(reorder(open, 3, -1)).toEqual([{ id: 3, priority: 1 }, { id: 1, priority: 2 }]);
    expect(reorder(open, 1, -1)).toEqual([]);
    expect(reorder(open, 4, 1)).toEqual([]);
    expect(splitWishes([w(1, "A", 1), w(2, "A", 2, { status: "applied" }), w(3, "A", 3, { status: "dropped" }), w(4, "A", 4, { status: "ready" })]).open.map((x) => x.id)).toEqual([1, 4]);
    expect(wishName({ kind: "bank_bonus", bank: "Chase", product: "Total Checking" })).toBe("Chase Total Checking");
    expect(wishName({ kind: "card", bank: null, product: "Sapphire" })).toBe("Sapphire");
  });
});
