// @vitest-environment jsdom
import { fireEvent, render } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

const shape = vi.hoisted(() => ({ value: { coverage: 1, square: true, light: false } }));
vi.mock("$lib/logoTone", () => ({ badgeShape: () => shape.value, isDark: () => false }));

import { app } from "$lib/app.svelte";
import BankBadge from "./BankBadge.svelte";

const badge = () => document.querySelector<HTMLElement>("[data-account-badge]");
async function loaded() {
  const img = badge()!.querySelector("img")!;
  Object.defineProperty(img, "naturalWidth", { value: 64, configurable: true });
  await fireEvent.load(img);
  return img;
}

beforeEach(() => {
  app.state = { connected: true, brands: { a1: { institution: "Sample Bank", initial: "S", src: "/logo.png" }, a2: { institution: "Letter Bank", initial: "L" } } };
});

describe("BankBadge", () => {
  it("draws a solid, square logo as it is, with no outline", async () => {
    shape.value = { coverage: 0.95, square: true, light: false };
    render(BankBadge, { accountId: "a1", name: "Checking" });
    const img = await loaded();
    expect(badge()).not.toHaveAttribute("data-tile");
    expect(img.parentElement).not.toHaveClass("bg-white");
    expect(badge()!.className).not.toMatch(/\bring-/);
    expect(badge()).toHaveAttribute("title", "Checking");
  });

  it("puts a white tile behind a sparse wordmark", async () => {
    shape.value = { coverage: 0.2, square: false, light: false };
    render(BankBadge, { accountId: "a1" });
    const img = await loaded();
    expect(badge()).toHaveAttribute("data-tile", "light");
    expect(img.parentElement).toHaveClass("bg-white", "p-[2px]");
    expect(img).toHaveClass("object-contain");
  });

  it("puts a dark tile behind a near-white mark", async () => {
    shape.value = { coverage: 0.3, square: true, light: true };
    render(BankBadge, { accountId: "a1" });
    const img = await loaded();
    expect(img.parentElement).toHaveClass("bg-card");
  });

  it("shows the institution's letter without a logo, and nothing for an unknown account", () => {
    const { unmount } = render(BankBadge, { accountId: "a2" });
    expect(badge()).toHaveTextContent("L");
    unmount();
    render(BankBadge, { accountId: "zz" });
    expect(badge()).toBeNull();
  });

  it("takes its placement from the caller", () => {
    render(BankBadge, { accountId: "a2", class: "-right-0.5 bottom-0" });
    expect(badge()).toHaveClass("-right-0.5", "bottom-0");
    expect(badge()).not.toHaveClass("-right-1.5");
  });
});
