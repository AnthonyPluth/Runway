import { describe, expect, it } from "vitest";
import { CHART_LINES, isOption, shares, stalePrice, todayIndex, vestingPreview, vestingSeries } from "./equity";
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
        grant({ kind: "rsu", schedule: [["2026-01-15", 100], ["2026-03-15", 200]] }),
        grant({ kind: "iso", strike: 4, schedule: [["2026-02-01", 50]] }),
      ],
    });
    const s = vestingSeries([co])!;
    expect(s.xs).toEqual(["2026-01-31", "2026-02-28", "2026-03-31"]);
    expect(s.lines).toEqual([{ name: "Acme", values: [1_000, 1_000 + 300, 2_000 + 300] }]);
  });

  it("draws at least two points, and a line for every company when there are a few", () => {
    const one = grant({ schedule: [["2026-05-10", 10]] });
    const cos = ["A", "B", "C", "D"].slice(0, CHART_LINES).map((name) => company({ name, grants: [one] }));
    const s = vestingSeries(cos)!;
    expect(s.xs).toEqual(["2026-05-31", "2026-06-30"]);
    expect(s.lines.map((l) => l.name)).toEqual(["A", "B", "C", "D"]);
  });

  it("adds the smaller companies up as Other past the limit, so nothing is left off the chart", () => {
    const sized = (name: string, n: number) => company({ name, share_price: 1, grants: [grant({ schedule: [["2026-05-10", n]] })] });
    const cos = [sized("A", 10), sized("B", 500), sized("C", 30), sized("D", 400), sized("E", 20), sized("F", 300)];
    const s = vestingSeries(cos)!;
    expect(s.lines.map((l) => l.name)).toEqual(["B", "D", "F", "Other"]);
    expect(s.lines.at(-1)!.values).toEqual([60, 60]);
    const all = (i: number) => s.lines.reduce((a, l) => a + l.values[i], 0);
    expect(all(1)).toBe(10 + 500 + 30 + 400 + 20 + 300);
  });

  it("counts underwater options as worth nothing", () => {
    const co = company({ share_price: 3, grants: [grant({ kind: "nso", strike: 5, schedule: [["2026-01-01", 100]] })] });
    expect(vestingSeries([co])!.lines[0].values.every((v) => v === 0)).toBe(true);
  });
});

describe("stalePrice", () => {
  it("says how old a share price is once it's about three months old", () => {
    expect(stalePrice("2026-07-15", "2026-09-30")).toBeNull();
    expect(stalePrice("2026-06-30", "2026-09-30")).toBe("price from 3 months ago");
    expect(stalePrice("2026-02-28", "2026-09-30")).toBe("price from 7 months ago");
    expect(stalePrice("2025-06-01", "2026-09-30")).toBe("price from over a year ago");
    expect(stalePrice("2023-01-01", "2026-09-30")).toBe("price from 3 years ago");
  });
  it("is quiet without a date", () => {
    expect(stalePrice(null, "2026-09-30")).toBeNull();
    expect(stalePrice(undefined, "2026-09-30")).toBeNull();
  });
});

describe("vestingPreview", () => {
  const f = (over: Partial<Record<string, string>>) => ({ kind: "iso", vest_start: "2026-01-15", granted_on: "", vest_months: "48", cliff_months: "12", vest_every: "1", ...over });
  const plain = (s: string | null) => s?.replace(/\u00a0/g, " ") ?? null;
  it("says the cliff and what comes after it", () => {
    expect(plain(vestingPreview(f({})))).toBe("25% on Jan 2027, then monthly until Jan 2030");
    expect(plain(vestingPreview(f({ vest_every: "3" })))).toBe("25% on Jan 2027, then quarterly until Jan 2030");
    expect(plain(vestingPreview(f({ vest_months: "36", cliff_months: "6" })))).toBe("17% on Jul 2026, then monthly until Jan 2029");
  });
  it("without a cliff, starts with the first step", () => {
    expect(plain(vestingPreview(f({ cliff_months: "" })))).toBe("Monthly from Feb 2026 until Jan 2030");
    expect(plain(vestingPreview(f({ cliff_months: "0", vest_every: "12", vest_months: "48" })))).toBe("Yearly from Jan 2027 until Jan 2030");
  });
  it("vests all at once when there's no length, or the cliff is the whole of it", () => {
    expect(plain(vestingPreview(f({ vest_months: "" })))).toBe("All on Jan 2026");
    expect(plain(vestingPreview(f({ vest_months: "12", cliff_months: "12" })))).toBe("All on Jan 2027");
  });
  it("starts from the grant date when there's no vesting start, and keeps the day within short months", () => {
    expect(plain(vestingPreview(f({ vest_start: "", granted_on: "2025-08-31", cliff_months: "6", vest_months: "24" })))).toBe("25% on Feb 2026, then monthly until Aug 2027");
  });
  it("says nothing for plain shares, a missing start or a half-typed number", () => {
    expect(vestingPreview(f({ kind: "shares" }))).toBeNull();
    expect(vestingPreview(f({ vest_start: "" }))).toBeNull();
    expect(vestingPreview(f({ vest_start: "2026-01" }))).toBeNull();
    expect(vestingPreview(f({ vest_months: "4.5" }))).toBeNull();
    expect(vestingPreview(f({ cliff_months: "-1" }))).toBeNull();
  });
});

describe("todayIndex", () => {
  const xs = ["2026-01-31", "2026-02-28", "2026-03-31"];
  it("puts today between the two dates it falls in", () => {
    expect(todayIndex(xs, "2026-01-31")).toBe(0);
    expect(todayIndex(xs, "2026-02-28")).toBe(1);
    expect(todayIndex(xs, "2026-02-14")).toBeCloseTo(0.5, 1);
    expect(todayIndex(xs, "2026-03-31")).toBe(2);
  });
  it("is null before the first date, after the last, or with too few dates", () => {
    expect(todayIndex(xs, "2026-01-30")).toBeNull();
    expect(todayIndex(xs, "2026-04-01")).toBeNull();
    expect(todayIndex(["2026-01-31"], "2026-01-31")).toBeNull();
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
