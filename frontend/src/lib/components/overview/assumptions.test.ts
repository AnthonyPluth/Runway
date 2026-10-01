import { describe, expect, it } from "vitest";
import { perDay } from "./assumptions";

describe("perDay", () => {
  it("keeps the cents on a small daily amount", () => {
    expect(perDay(4.5)).toBe("about $4.50 a day");
    expect(perDay(42.4)).toBe("about $42 a day");
  });
});
