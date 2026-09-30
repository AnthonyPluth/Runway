// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/categories.svelte", async (orig) => ({ ...(await orig<typeof import("$lib/categories.svelte")>()), loadCategories: vi.fn(async () => {}) }));

import { api } from "$lib/api";
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
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Type an emoji for Dining" })).toHaveFocus());
  });

  it("saves an emoji typed or pasted in, and nothing for letters", async () => {
    render(LookPicker, { c: cat });
    await userEvent.click(screen.getByRole("button", { name: "Emoji for Dining" }));
    const box = screen.getByRole("textbox", { name: "Type an emoji for Dining" });
    await userEvent.type(box, "taco");
    expect(vi.mocked(api)).not.toHaveBeenCalled();
    await userEvent.clear(box);
    await userEvent.click(box);
    await userEvent.paste("🌮");
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalledTimes(1));
    const [path, opts] = vi.mocked(api).mock.calls[0] as [string, { body: { name: string; icon: string; color: string } }];
    expect(path).toBe("/api/categories/look");
    expect(opts.body).toEqual({ name: "Dining", icon: "🌮", color: "#d95926" });
    expect(box).toHaveValue("");                                   // ready for another
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
});
