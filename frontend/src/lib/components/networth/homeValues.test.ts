import { describe, expect, it } from "vitest";
import { HOME_VALUES, valueSource } from "./homeValues";

describe("valueSource", () => {
  it("names where a home value came from, including the provider Runway used before", () => {
    expect(valueSource("realie")).toBe("Realie estimate");
    expect(valueSource("rentcast")).toBe("RentCast estimate");
  });

  it("calls anything else the user's own estimate", () => {
    expect(valueSource("manual")).toBe("Your estimate");
    expect(valueSource(null)).toBe("Your estimate");
    expect(valueSource(undefined)).toBe("Your estimate");
  });
});

describe("HOME_VALUES", () => {
  it("summarizes a refresh, with the range only when both ends are known", () => {
    expect(HOME_VALUES.refreshed({ value: 500000, low: 450000, high: 550000 })).toBe("Realie estimate: $500,000 (range $450,000–$550,000)");
    expect(HOME_VALUES.refreshed({ value: 500000, low: null, high: 550000 })).toBe("Realie estimate: $500,000");
  });

  it("says how many free lookups are used", () => {
    expect(HOME_VALUES.lookups(3, 25)).toBe("3 of 25 free lookups used this month");
  });
});
