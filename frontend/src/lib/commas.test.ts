// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { commas, withCommas, withoutCommas } from "./commas";

const shown = (el: HTMLInputElement) => Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.get!.call(el);

let input: HTMLInputElement;
beforeEach(() => {
  document.body.innerHTML = `<input type="number" step="1000" value="450000"><button>elsewhere</button>`;
  input = document.querySelector("input")!;
});

describe("withCommas", () => {
  it("puts commas in the whole-number part only", () => {
    expect(withCommas("1234567")).toBe("1,234,567");
    expect(withCommas("1234567.891")).toBe("1,234,567.891");
    expect(withCommas("-25000")).toBe("-25,000");
    expect(withCommas("999")).toBe("999");
    expect(withCommas("")).toBe("");
    expect(withoutCommas("1,234,567.5")).toBe("1234567.5");
  });
});

describe("commas", () => {
  it("shows the commas while you're elsewhere, but its value never has them", () => {
    commas(input);
    expect(input.type).toBe("text");
    expect(shown(input)).toBe("450,000");
    expect(input.value).toBe("450000");
    expect(input.inputMode).toBe("decimal");
  });

  it("is a number field while you edit it, and shows the commas again when you leave", () => {
    commas(input);
    input.focus();
    expect(input.type).toBe("number");
    expect(shown(input)).toBe("450000");
    input.value = "1250000";   // typing
    document.querySelector("button")!.focus();
    expect(input.type).toBe("text");
    expect(shown(input)).toBe("1,250,000");
    expect(input.value).toBe("1250000");
  });

  it("shows a value set from outside with its commas", () => {
    commas(input);
    input.value = String(98765.5);
    expect(shown(input)).toBe("98,765.5");
    input.value = "";
    expect(shown(input)).toBe("");
  });

  it("puts the field back as it was when it's removed", () => {
    const undo = commas(input);
    undo();
    expect(input.type).toBe("number");
    expect(input.value).toBe("450000");
    expect(Object.getOwnPropertyDescriptor(input, "value")).toBeUndefined();
  });
});
