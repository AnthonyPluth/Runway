// @vitest-environment jsdom
import { fireEvent } from "@testing-library/svelte";
import { toast } from "svelte-sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { autosave, markSaved } from "./autosave";

vi.mock("svelte-sonner", () => ({ toast: { error: vi.fn() } }));

let label: HTMLLabelElement, input: HTMLInputElement;
beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = `<label><input type="text" value="a"></label>`;
  label = document.querySelector("label")!;
  input = document.querySelector("input")!;
});
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

const edit = async (f: HTMLInputElement, v: string) => { f.value = v; await fireEvent.change(f); };

describe("autosave", () => {
  it("saves when the value changed and flashes 'Saved' on the label around it", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    autosave(input, save);
    await edit(input, "b");
    expect(save).toHaveBeenCalledWith(input);
    expect(label).toHaveClass("just-saved");
    vi.advanceTimersByTime(1700);
    expect(label).not.toHaveClass("just-saved");
  });

  it("does nothing when the value is unchanged", async () => {
    const save = vi.fn();
    autosave(input, save);
    await fireEvent.change(input);
    expect(save).not.toHaveBeenCalled();
  });

  it("tells a screen reader it saved", async () => {
    autosave(input, vi.fn().mockResolvedValue(undefined));
    const said = document.querySelector("[data-autosave-status]")!;
    expect(said).toHaveAttribute("role", "status");
    expect(said).toHaveTextContent("");   // there before the first save, so that one is read out too
    await edit(input, "b");
    expect(said).toHaveTextContent("Saved");
    vi.advanceTimersByTime(1700);
    expect(said).toHaveTextContent("");
  });

  it("marks the field and keeps a Not saved line with Retry under it until a save goes through", async () => {
    const save = vi.fn().mockRejectedValueOnce(new Error("Too long")).mockResolvedValue(undefined);
    autosave(input, save);
    await edit(input, "b");
    expect(input).toHaveAttribute("aria-invalid", "true");
    const line = label.querySelector("[data-autosave-error]")!;
    expect(line).toHaveTextContent("Not saved · Retry");
    expect(input).toHaveAccessibleDescription("Not saved · Retry");
    vi.advanceTimersByTime(5000);
    expect(line.isConnected).toBe(true);   // it stays
    await fireEvent.click(line.querySelector("button")!);
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    expect(input).not.toHaveAttribute("aria-invalid");
    expect(input).not.toHaveAttribute("aria-describedby");
    expect(label.querySelector("[data-autosave-error]")).toBeNull();
    expect(label).toHaveClass("just-saved");
  });

  it("puts a checkbox's Not saved line under its whole row, and its Saved beside the text", async () => {
    document.body.innerHTML = `<div><label><input type="checkbox"> Count it</label><label><input type="checkbox"> Next</label></div>`;
    const [first] = document.querySelectorAll("label");
    const box = first.querySelector("input")!;
    const save = vi.fn().mockRejectedValueOnce(new Error("Nope")).mockResolvedValue(undefined);
    autosave(box, save);
    box.checked = true;
    await fireEvent.change(box);
    expect(first.nextElementSibling).toHaveAttribute("data-autosave-error");
    await fireEvent.change(box);
    expect(document.querySelector("[data-autosave-error]")).toBeNull();
    const flash = first.querySelector("[data-saved-flash]")!;
    expect(flash).toHaveTextContent("Saved ✓");
    expect(flash).toHaveAttribute("aria-hidden", "true");
    expect(first).not.toHaveClass("just-saved");   // no flash above it, over the row before
    vi.advanceTimersByTime(1700);
    expect(first.querySelector("[data-saved-flash]")).toBeNull();
  });

  it("shows the error and keeps the typed value when saving fails, then retries next time", async () => {
    const save = vi.fn().mockRejectedValueOnce(new Error("Too long")).mockResolvedValue(undefined);
    autosave(input, save);
    await edit(input, "b");
    expect(toast.error).toHaveBeenCalledWith("Too long");
    expect(input.value).toBe("b");
    expect(label).not.toHaveClass("just-saved");
    await fireEvent.change(input);   // still differs from the last saved value, so it tries again
    expect(save).toHaveBeenCalledTimes(2);
    expect(label).toHaveClass("just-saved");
  });

  it("saves a checkbox by its checked state", async () => {
    document.body.innerHTML = `<input type="checkbox">`;
    const box = document.querySelector("input")!;
    const save = vi.fn().mockResolvedValue(undefined);
    autosave(box, save);
    box.checked = true;
    await fireEvent.change(box);
    expect(save).toHaveBeenCalledOnce();
  });

  it("commits a text field on Enter by leaving it", async () => {
    autosave(input, vi.fn());
    input.focus();
    await fireEvent.keyDown(input, { key: "Enter" });
    expect(document.activeElement).not.toBe(input);
  });

  it("uses the newest save function after update() and stops after destroy()", async () => {
    const first = vi.fn().mockResolvedValue(undefined), second = vi.fn().mockResolvedValue(undefined);
    const action = autosave(input, first);
    action.update(second);
    await edit(input, "b");
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalled();
    action.destroy();
    await edit(input, "c");
    expect(second).toHaveBeenCalledOnce();
  });
});

describe("markSaved", () => {
  it("falls back to the parent element when there is no label", () => {
    document.body.innerHTML = `<div><input></div>`;
    markSaved(document.querySelector("input")!);
    expect(document.querySelector("div")).toHaveClass("just-saved");
  });
});
