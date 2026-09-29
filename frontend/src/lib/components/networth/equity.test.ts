import { describe, expect, it } from "vitest";
import { isOption, shares, vestingSeries } from "./equity";
import { valueSource } from "./homeValues";
import type { Company, Grant } from "./types";

function grant(over: Partial<Grant>): Grant {
  return {
    id: "g", company_id: "c", kind: "rsu", label: null, granted_on: null, quantity: 0, strike: null, vest_start: null,
    vest_months: null, cliff_months: null, vest_every: null, exercised: null, expires_on: null, vested: 0,
    fully_vested_on: null, schedule: [], vested_value: 0, unvested_value: 0, ...over,
  };
}
function company(over: Partial<Company>): Company {
  return { id: "c", name: "Acme", share_price: 10, price_as_of: null, in_networth: 0, source: "manual", grants: [], vested_value: 0, unvested_value: 0, ...over };
}

describe("grant kinds", () => {
  it("knows which grants are options", () => {
    expect(isOption("iso")).toBe(true);
    expect(isOption("nso")).toBe(true);
    expect(isOption("rsu")).toBe(false);
    expect(isOption("shares")).toBe(false);
  });
  it("writes share counts", () => {
    expect(shares(1234.567)).toBe("1,234.57");
    expect(shares(null)).toBe("—");
  });
});

describe("vestingSeries", () => {
  it("has nothing to draw without a schedule", () => {
    expect(vestingSeries([])).toBeNull();
    expect(vestingSeries([company({ grants: [grant({})] })])).toBeNull();
  });

  it("values vested shares month by month, options only above their strike", () => {
    const co = company({
      share_price: 10,
      grants: [
        // Cumulative vested shares on each date.
        grant({ kind: "rsu", schedule: [["2026-01-15", 100], ["2026-03-15", 200]] }),
        grant({ kind: "iso", strike: 4, schedule: [["2026-02-01", 50]] }),
      ],
    });
    const s = vestingSeries([co])!;
    expect(s.xs).toEqual(["2026-01-31", "2026-02-28", "2026-03-31"]);
    expect(s.lines).toEqual([{ name: "Acme", values: [1_000, 1_000 + 300, 2_000 + 300] }]);
  });

  it("draws at least two points and no more than three companies", () => {
    const one = grant({ schedule: [["2026-05-10", 10]] });
    const cos = ["A", "B", "C", "D"].map((name) => company({ name, grants: [one] }));
    const s = vestingSeries(cos)!;
    expect(s.xs).toEqual(["2026-05-31", "2026-06-30"]);
    expect(s.lines.map((l) => l.name)).toEqual(["A", "B", "C"]);
  });

  it("counts underwater options as worth nothing", () => {
    const co = company({ share_price: 3, grants: [grant({ kind: "nso", strike: 5, schedule: [["2026-01-01", 100]] })] });
    expect(vestingSeries([co])!.lines[0].values.every((v) => v === 0)).toBe(true);
  });
});

describe("valueSource", () => {
  it("names where a home value came from", () => {
    expect(valueSource("realie")).toBe("Realie estimate");
    expect(valueSource("rentcast")).toBe("RentCast estimate");
    expect(valueSource("manual")).toBe("Your estimate");
    expect(valueSource(null)).toBe("Your estimate");
  });
});
