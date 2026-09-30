import { describe, expect, it } from "vitest";
import type { ForecastEvent, Overview } from "$lib/types";
import { assumptions, perDay } from "./assumptions";

type Acct = Overview["accounts"][number];
const acct = (extra: Partial<Acct> = {}): Acct => ({ id: "chk", name: "Checking", kind: "checking", balance: 1000, daily_spend: 0, daily_spend_on: false, ...extra });
const ev = (extra: Partial<ForecastEvent> = {}): ForecastEvent => ({ date: "2026-10-01", name: "Rent", amount: -1500, kind: "recurring", balance_after: 0, ...extra });
const pay = ev({ name: "Pay", amount: 2500 });
const card = (estimated: boolean) => ev({ name: "Visa statement", kind: "card", estimated });

describe("assumptions", () => {
  it("counts paychecks, bills and card payments, and says everyday spending is left out", () => {
    expect(assumptions({ events: [pay, ev(), pay, ev(), ev(), pay, ev(), card(false), card(true)], accounts: [acct()] })).toEqual({
      text: "Includes 7 paychecks and bills and 2 card payments (1 estimated). Everyday spending isn’t included", action: "Turn on" });
  });

  it("names what's there when it's only one kind", () => {
    expect(assumptions({ events: [pay, card(true)], accounts: [acct()] }).text).toBe("Includes 1 paycheck and 1 card payment (estimated). Everyday spending isn’t included");
    expect(assumptions({ events: [ev(), ev()], accounts: [acct()] }).text).toMatch(/^Includes 2 bills\./);
    expect(assumptions({ events: [], accounts: [acct()] }).text).toBe("Nothing scheduled in this range yet. Everyday spending isn’t included");
  });

  it("includes everyday spending when it's on, with what it takes out a day", () => {
    expect(assumptions({ events: [pay, ev(), card(false)], accounts: [acct({ daily_spend_on: true, daily_spend: 42.4 })] })).toEqual({
      text: "Includes 2 paychecks and bills, 1 card payment and everyday spending of about $42 a day", action: "Change" });
  });

  it("says which accounts it's on for when only some of them are", () => {
    const accounts = [acct({ daily_spend_on: true, daily_spend: 20 }), acct({ id: "sav", name: "Savings" })];
    expect(assumptions({ events: [ev()], accounts })).toEqual({
      text: "Includes 1 bill. Everyday spending is taken out of Checking only", action: "Change" });
  });

  it("keeps the cents on a small daily amount", () => {
    expect(perDay(4.5)).toBe("about $4.50 a day");
    expect(perDay(42.4)).toBe("about $42 a day");
  });
});
