// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { reviewAction } from "./reviewKeys";

const press = (key: string, target: Element = document.body, extra: Partial<KeyboardEvent> = {}) =>
  ({ key, target, ctrlKey: false, metaKey: false, altKey: false, defaultPrevented: false, ...extra });

describe("reviewAction", () => {
  it("maps the keys", () => {
    const row = document.createElement("div");
    expect(["j", "ArrowDown", "k", "ArrowUp", "Enter", "c", "i", "t", "s", "Escape"].map((k) => reviewAction(press(k, row), true)))
      .toEqual(["next", "next", "prev", "prev", "enter", "pick", "ignore", "transfer", "split", "clear"]);
    expect(reviewAction(press("J", row), true)).toBe("next");   // caps lock on
    expect(reviewAction(press("x", row), true)).toBeNull();
  });

  it("only moves onto a row until one has the keyboard", () => {
    expect(reviewAction(press("j"), false)).toBe("next");
    expect(reviewAction(press("Enter"), false)).toBeNull();
    expect(reviewAction(press("i"), false)).toBeNull();
  });

  it("leaves typing alone", () => {
    for (const tag of ["input", "textarea", "select"]) expect(reviewAction(press("j", document.createElement(tag)), true)).toBeNull();
    const editable = document.createElement("div");
    editable.contentEditable = "true";
    Object.defineProperty(editable, "isContentEditable", { value: true });
    expect(reviewAction(press("c", editable), true)).toBeNull();
  });

  it("leaves dialogs, the picker's list and toasts alone", () => {
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    const inside = document.createElement("button");
    dialog.append(inside);
    expect(reviewAction(press("j", inside), true)).toBeNull();
  });

  it("lets a focused button keep Enter and the arrows, but not the letters", () => {
    const button = document.createElement("button");
    expect(reviewAction(press("Enter", button), true)).toBeNull();
    expect(reviewAction(press("ArrowDown", button), true)).toBeNull();
    expect(reviewAction(press("j", button), true)).toBe("next");
    expect(reviewAction(press("i", button), true)).toBe("ignore");
  });

  it("ignores keys with a modifier, or already handled", () => {
    expect(reviewAction(press("c", document.body, { metaKey: true }), true)).toBeNull();   // copy
    expect(reviewAction(press("j", document.body, { ctrlKey: true }), true)).toBeNull();
    expect(reviewAction(press("j", document.body, { defaultPrevented: true }), true)).toBeNull();
  });
});
