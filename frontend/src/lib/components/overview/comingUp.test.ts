import { describe, expect, it } from "vitest";
import type { ForecastEvent } from "$lib/types";
import { comingUp } from "./comingUp";

const ev = (date: string, name: string, amount: number, kind = "recurring"): ForecastEvent => ({ date, name, amount, kind });

describe("comingUp", () => {
  it("puts annual fees among the events, by date and then amount", () => {
    const events = [ev("2026-10-01", "Rent", -1500), ev("2026-10-15", "Pay", 3000)];
    const fees = [ev("2026-10-10", "Sapphire annual fee", -95, "fee"), ev("2026-10-01", "Gold annual fee", -325, "fee")];
    expect(comingUp({ events, fees }).map((e) => e.name)).toEqual(["Rent", "Gold annual fee", "Sapphire annual fee", "Pay"]);
  });

  it("adds the recurring charges on cards when asked (Transactions), not otherwise (Overview)", () => {
    const events = [ev("2026-10-01", "Rent", -1500)];
    const charges = [{ ...ev("2026-10-01", "Streaming", -15), account_id: "card", account: "Travel Card" }, ev("2026-09-30", "Music", -11)];
    expect(comingUp({ events, charges }).map((e) => e.name)).toEqual(["Rent"]);
    const all = comingUp({ events, charges }, { charges: true });
    expect(all.map((e) => e.name)).toEqual(["Music", "Rent", "Streaming"]);
    expect(all[2]).toMatchObject({ account_id: "card", account: "Travel Card" });
    expect(comingUp({ events }, { charges: true }).map((e) => e.name)).toEqual(["Rent"]);
  });

  it("works without fees (an older server)", () => {
    expect(comingUp({ events: [ev("2026-10-01", "Rent", -1500)] }).map((e) => e.name)).toEqual(["Rent"]);
  });
});
