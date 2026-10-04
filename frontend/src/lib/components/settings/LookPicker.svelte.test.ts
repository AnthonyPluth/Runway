// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/categories.svelte", async (orig) => ({ ...(await orig<typeof import("$lib/categories.svelte")>()), loadCategories: vi.fn(async () => {}) }));

import { api } from "$lib/api";
import { viewport } from "$lib/phone.svelte";
import LookPicker from "./LookPicker.svelte";
import type { Category } from "$lib/types";

const cat = { name: "Dining", icon: "🍽️", color: "#3987e5", custom_icon: null, custom_color: "#d95926" } as unknown as Category;

beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(api).mockResolvedValue({ ok: true } as never); });

describe("a category's emoji picker", () => {
  it("offers emoji only, no colors, with a box to type one in that has the focus", async () => {
    render(LookPicker, { c: cat });
    await userEvent.click(screen.getByRole("button", { name: "Emoji for Dining" }));
    const dialog = screen.getByRole("dialog", { name: "Emoji for Dining" });
    expect(dialog).toHaveTextContent("Emoji");
    expect(dialog).not.toHaveTextContent("Color");
    expect(screen.queryByRole("button", { name: /Use color/ })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("searchbox", { name: "Search or type an emoji for Dining" })).toHaveFocus());
  });

  it("finds emoji by name, and Enter takes the first", async () => {
    render(LookPicker, { c: cat });
    await userEvent.click(screen.getByRole("button", { name: "Emoji for Dining" }));
    const box = screen.getByRole("searchbox", { name: "Search or type an emoji for Dining" });
    await userEvent.type(box, "coff");
    const shown = screen.getAllByRole("button", { name: /^Use / }).map((b) => b.textContent);
    expect(shown).toEqual(["☕"]);
    await userEvent.clear(box);
    await userEvent.type(box, "zzzz");
    expect(screen.queryAllByRole("button", { name: /^Use / })).toHaveLength(0);
    expect(screen.getByRole("dialog")).toHaveTextContent("No matches");
    await userEvent.clear(box);
    await userEvent.type(box, "gas{Enter}");
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalledTimes(1));
    expect((vi.mocked(api).mock.calls[0] as [string, { body: { icon: string } }])[1].body.icon).toBe("⛽");
    expect(box).toHaveValue("");
  });

  it("saves an emoji typed or pasted in, and nothing for letters", async () => {
    render(LookPicker, { c: cat });
    await userEvent.click(screen.getByRole("button", { name: "Emoji for Dining" }));
    const box = screen.getByRole("searchbox", { name: "Search or type an emoji for Dining" });
    await userEvent.type(box, "taco");
    expect(vi.mocked(api)).not.toHaveBeenCalled();
    await userEvent.clear(box);
    await userEvent.click(box);
    await userEvent.paste("🌮");
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalledTimes(1));
    const [path, opts] = vi.mocked(api).mock.calls[0] as [string, { body: { name: string; icon: string; color: string } }];
    expect(path).toBe("/api/categories/look");
    expect(opts.body).toEqual({ name: "Dining", icon: "🌮", color: "#d95926" });
    expect(box).toHaveValue("");
  });

  it("saves the emoji you pick and leaves a color set before as it is", async () => {
    render(LookPicker, { c: cat });
    await userEvent.click(screen.getByRole("button", { name: "Emoji for Dining" }));
    await userEvent.click(screen.getAllByRole("button", { name: /^Use / })[0]);
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalled());
    const [path, opts] = vi.mocked(api).mock.calls[0] as [string, { body: { name: string; icon: string; color: string } }];
    expect(path).toBe("/api/categories/look");
    expect(opts.body).toMatchObject({ name: "Dining", color: "#d95926" });
    expect(opts.body.icon).not.toBe("");
  });

  it("is a dialog that takes the focus, and gives it back to the icon on Escape or Done", async () => {
    render(LookPicker, { c: cat });
    const icon = screen.getByRole("button", { name: "Emoji for Dining" });
    expect(icon).toHaveAttribute("aria-haspopup", "dialog");
    await userEvent.click(icon);
    expect(icon).toHaveAttribute("aria-expanded", "true");
    await waitFor(() => expect(screen.getByRole("searchbox")).toHaveFocus());
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(icon).toHaveFocus();
    expect(icon).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(icon);
    await waitFor(() => expect(screen.getByRole("searchbox")).toHaveFocus());
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(icon).toHaveFocus();
  });

  it("closes on a click elsewhere, leaving the focus there", async () => {
    const other = document.createElement("button");
    document.body.append(other);
    render(LookPicker, { c: cat });
    await userEvent.click(screen.getByRole("button", { name: "Emoji for Dining" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.click(other);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(other).toHaveFocus();
    other.remove();
  });
});

describe("on a phone", () => {
  const withCat = { ...cat, custom_icon: "🍕" } as unknown as Category;
  beforeEach(() => {
    viewport.phone = true;
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q === "(pointer: coarse)", media: q, addEventListener() {}, removeEventListener() {} }));
  });
  afterEach(() => { viewport.phone = false; vi.unstubAllGlobals(); });

  it("opens just a field, focused within the tap itself, with no grid, search or hint", async () => {
    render(LookPicker, { c: cat });
    screen.getByRole("button", { name: "Emoji for Dining" }).click();
    const box = screen.getByPlaceholderText("Type an emoji");
    expect(document.activeElement).toBe(box);
    expect(box).toHaveAccessibleName("Type an emoji for Dining");
    expect(screen.queryAllByRole("button", { name: /^Use / })).toHaveLength(0);
    expect(screen.queryAllByRole("searchbox")).toHaveLength(0);
    expect(screen.queryAllByText(/emoji key|emoji panel|Or pick one/)).toHaveLength(0);
  });

  it("saves a typed emoji and closes, putting the keyboard away", async () => {
    render(LookPicker, { c: cat });
    screen.getByRole("button", { name: "Emoji for Dining" }).click();
    const box = screen.getByPlaceholderText("Type an emoji");
    expect(document.activeElement).toBe(box);
    await userEvent.paste("🌮");
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalledTimes(1));
    const [path, opts] = vi.mocked(api).mock.calls[0] as [string, { body: unknown }];
    expect(path).toBe("/api/categories/look");
    expect(opts.body).toEqual({ name: "Dining", icon: "🌮", color: "#d95926" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(document.body);
  });

  it("ignores letters: nothing saved, the field is cleared and stays open", async () => {
    render(LookPicker, { c: cat });
    screen.getByRole("button", { name: "Emoji for Dining" }).click();
    const box = screen.getByPlaceholderText("Type an emoji");
    await userEvent.keyboard("taco");
    expect(vi.mocked(api)).not.toHaveBeenCalled();
    expect(box).toHaveValue("");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(document.activeElement).toBe(box);
  });

  it("resets to the default, and offers that only when something custom is set", async () => {
    const { unmount } = render(LookPicker, { c: { ...cat, custom_color: null } as unknown as Category });
    screen.getByRole("button", { name: "Emoji for Dining" }).click();
    expect(screen.getByRole("button", { name: "Reset to default" })).toBeDisabled();
    unmount();
    render(LookPicker, { c: withCat });
    screen.getByRole("button", { name: "Emoji for Dining" }).click();
    await userEvent.click(screen.getByRole("button", { name: "Reset to default" }));
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalledTimes(1));
    expect((vi.mocked(api).mock.calls[0] as [string, { body: unknown }])[1].body).toEqual({ name: "Dining", icon: "", color: "" });
  });

  it("closes on Escape and on a tap elsewhere", async () => {
    render(LookPicker, { c: cat });
    screen.getByRole("button", { name: "Emoji for Dining" }).click();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    screen.getByRole("button", { name: "Emoji for Dining" }).click();
    await userEvent.click(document.body);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps the full picker in a narrow window with no touch (a computer)", async () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} }));
    render(LookPicker, { c: cat });
    await userEvent.click(screen.getByRole("button", { name: "Emoji for Dining" }));
    expect(screen.getByRole("searchbox")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Use / }).length).toBeGreaterThan(10);
  });
});
