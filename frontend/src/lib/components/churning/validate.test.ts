import { describe, expect, it } from "vitest";
import { fieldProps } from "./form";
import { validateBank, validateBenefit, validateCard, validateWish } from "./validate";

describe("validateCard", () => {
  const ok = { owner: "Alex", issuer: "chase", product: "Sapphire Preferred", opened_on: "2026-09-01", annual_fee: "95", bonus: "", bonus_spend: "", manual_spend: "" };
  it("accepts a complete card, and amounts left empty", () => {
    expect(validateCard(ok)).toEqual({});
    expect(validateCard({ ...ok, annual_fee: "" })).toEqual({});
    expect(validateCard({ ...ok, annual_fee: 0 })).toEqual({});
  });
  it("names each missing field in its own words", () => {
    const e = validateCard({ ...ok, owner: "", issuer: "", product: "  ", opened_on: "" });
    expect(e).toEqual({
      owner: "Choose whose card it is.", issuer: "Pick the bank.", product: "Enter the card’s name, like Sapphire Preferred.", opened_on: "Enter the day it was opened.",
    });
  });
  it("refuses a date that isn't one and amounts below 0", () => {
    expect(validateCard({ ...ok, opened_on: "2026-13-45x" }).opened_on).toBeDefined();
    expect(validateCard({ ...ok, annual_fee: "-5" }).annual_fee).toBe("The annual fee can’t be below 0.");
    expect(validateCard({ ...ok, bonus_spend: "-1" }).bonus_spend).toBe("The spend can’t be below 0.");
  });
});

describe("validateBank", () => {
  const ok = { owner: "Alex", bank: "Chase", opened_on: "2026-09-01", bonus: "300" };
  it("accepts a complete bonus", () => expect(validateBank(ok)).toEqual({}));
  it("needs the bank, the day and a bonus above 0", () => {
    expect(validateBank({ ...ok, bank: "", opened_on: "", bonus: "" })).toEqual({
      bank: "Enter the bank, like Chase.", opened_on: "Enter the day it was opened.", bonus: "Enter the bonus.",
    });
    expect(validateBank({ ...ok, bonus: "0" }).bonus).toBe("Enter a bonus above $0.");
    expect(validateBank({ ...ok, dd_total: "-100" }).dd_total).toBe("Direct deposits can’t be below 0.");
  });
});

describe("validateWish", () => {
  it("needs a card's bank and name, or a bank bonus's bank", () => {
    expect(validateWish({ owner: "Alex", kind: "card", issuer: "chase", product: "" }).product).toBeDefined();
    expect(validateWish({ owner: "Alex", kind: "card", issuer: "chase", product: "Gold" })).toEqual({});
    expect(validateWish({ owner: "Alex", kind: "bank_bonus", bank: " " }).bank).toBe("Enter the bank, like Chase.");
    expect(validateWish({ owner: "Alex", kind: "bank_bonus", bank: "Chase", product: "" })).toEqual({});   // the offer is optional
  });
});

describe("validateBenefit", () => {
  it("needs a name, and an amount that isn't below 0", () => {
    expect(validateBenefit({ name: "", kind: "credit", amount: "" }).name).toBe("Enter the benefit’s name, like Lyft credit.");
    expect(validateBenefit({ name: "Lyft", kind: "credit", amount: "-5" }).amount).toBe("The amount can’t be below 0.");
    expect(validateBenefit({ name: "Lounge", kind: "access", amount: "-5" })).toEqual({});   // an access benefit has no amount
  });
});

describe("form helpers", () => {
  it("marks a field invalid and described by its note, and required for assistive tech", () => {
    expect(fieldProps({ product: "Enter it." }, "u1", "product", true)).toEqual({ "aria-invalid": "true", "aria-describedby": "u1-product-err", "aria-required": "true" });
    expect(fieldProps({}, "u1", "product", false)).toEqual({ "aria-invalid": undefined, "aria-describedby": undefined, "aria-required": undefined });
  });
});
