import { describe, expect, it } from "vitest";
import { gainCls, niceTicks, pct, pctAbs, qty, signed } from "./numbers";

describe("pct", () => {
  it("signs gains and losses, with a real minus", () => {
    expect(pct(0.042)).toBe("+4.2%");
    expect(pct(-0.01)).toBe("−1.0%");
    expect(pct(-0.25, 0)).toBe("−25%");
    expect(pct(0.1234, 2)).toBe("+12.34%");
  });
  it("never shows −0.0%", () => {
    expect(pct(0)).toBe("0.0%");
    expect(pct(-0.0001)).toBe("0.0%");
    expect(pct(0.0004)).toBe("0.0%");
  });
  it("shows a dash when there's nothing to show", () => {
    expect(pct(null)).toBe("—");
    expect(pct(undefined)).toBe("—");
    expect(pct(NaN)).toBe("—");
    expect(pct(Infinity)).toBe("—");
  });
});

describe("pctAbs", () => {
  it("drops the sign, and never leaves half a number", () => {
    expect(pctAbs(0.012)).toBe("1.2%");
    expect(pctAbs(-0.012)).toBe("1.2%");
    expect(pctAbs(0)).toBe("0.0%");
    expect(pctAbs(null)).toBe("—");
  });
});

describe("signed", () => {
  it("writes gains with a plus and losses with a real minus", () => {
    expect(signed(1234)).toBe("+$1,234.00");
    expect(signed(-12)).toBe("−$12.00");
    expect(signed(0)).toBe("$0.00");
    expect(signed(-0.001)).toBe("$0.00");   // what rounds to nothing isn't a loss
    expect(signed(null)).toBe("—");
  });
});

describe("gainCls", () => {
  it("colors gains green and losses red", () => {
    expect(gainCls(5)).toBe("text-good");
    expect(gainCls(0)).toBe("");
    expect(gainCls(-5)).toBe("text-loss");
    expect(gainCls(null)).toBe("");
  });
});

describe("niceTicks", () => {
  it("steps by round numbers covering the range", () => {
    expect(niceTicks(0, 100)).toEqual([0, 20, 40, 60, 80, 100]);
    expect(niceTicks(0, 1234)).toEqual([0, 250, 500, 750, 1000, 1250]);
    expect(niceTicks(13, 87)).toEqual([0, 20, 40, 60, 80, 100]);
  });
  it("spans zero", () => {
    expect(niceTicks(-50, 50)).toEqual([-60, -40, -20, 0, 20, 40, 60]);
  });
  it("doesn't pile up floating-point dust", () => {
    expect(niceTicks(0, 0.7)).toEqual([0, 0.2, 0.4, 0.6, 0.8]);
  });
  it("copes with a flat line", () => {
    expect(niceTicks(5, 5)).toEqual([5]);
    expect(niceTicks(0, 0)).toEqual([0]);
  });
  it("takes a tick count", () => {
    expect(niceTicks(0, 100, 2)).toEqual([0, 50, 100]);
  });
});

describe("qty", () => {
  it("shows up to four decimals", () => {
    expect(qty(1.23456789)).toBe("1.2346");
    expect(qty(1000)).toBe("1,000");
    expect(qty(0.5)).toBe("0.5");
  });
});
