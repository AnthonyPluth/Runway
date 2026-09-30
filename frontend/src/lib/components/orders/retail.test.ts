import { describe, expect, it } from "vitest";
import { orderLabel } from "./retail";

describe("orderLabel", () => {
  it("names the store, where it was bought and how many items", () => {
    expect(orderLabel({ retailer: "amazon", items: 3 })).toBe("Amazon · 3 items");
    expect(orderLabel({ retailer: "target", channel: "store", items: 1 })).toBe("Target in store · 1 item");
    expect(orderLabel({ retailer: "target", channel: "online" })).toBe("Target");
  });
  it("falls back to the retailer's own name", () => {
    expect(orderLabel({ retailer: "wayfair", items: 0 })).toBe("wayfair");
  });
});
