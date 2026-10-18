// @vitest-environment jsdom
// The app's toasts: drawn from the theme's tokens, a close button that is always there, and a bottom that follows the tab bar.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import { toast } from "svelte-sonner";
import { afterEach, describe, expect, it, vi } from "vitest";
import { viewport } from "$lib/phone.svelte";
import { BAR_GAP, EDGE_GAP, TOAST_CLASSES, TOAST_MS, toasterProps, toastBottom } from "./config";
import { Toaster } from "./index";

// jsdom has no pointer capture, which sonner asks for on every pointer-down.
HTMLElement.prototype.setPointerCapture = () => {};
afterEach(() => { cleanup(); toast.dismiss(); viewport.phone = false; document.documentElement.className = ""; });

describe("toasterProps", () => {
  it("sits above the tab bar on a phone, by the bar's height and a small gap", () => {
    const p = toasterProps(true, "dark");
    const bottom = `calc(var(--tabbar-h) + ${BAR_GAP})`;
    expect(toastBottom(true)).toBe(bottom);
    // Both of sonner's offsets, so the 600px to 767px phones (the bar is showing there too) agree.
    expect(p.offset).toEqual({ bottom });
    expect(p.mobileOffset).toEqual({ bottom });
  });

  it("sits at the bottom of the page when there is no tab bar, not lifted by the bar", () => {
    const p = toasterProps(false, "dark");
    const bottom = `calc(env(safe-area-inset-bottom) + ${EDGE_GAP})`;
    expect(p.offset).toEqual({ bottom });
    expect(p.mobileOffset).toEqual({ bottom });
    expect(bottom).not.toContain("--tabbar-h");
  });

  it("has an always-visible close button, swiping and a duration that leaves time to reach an action", () => {
    const p = toasterProps(true, "light");
    expect(p).toMatchObject({ closeButton: true, closeButtonAriaLabel: "Dismiss", position: "bottom-center", theme: "light", duration: TOAST_MS });
    expect(TOAST_MS).toBeGreaterThanOrEqual(5000);
    expect(p.swipeDirections).toEqual(expect.arrayContaining(["left", "right"]));
  });

  it("draws from the theme's tokens and the app's buttons, with no colour of its own", () => {
    expect(toasterProps(true, "dark").toastOptions).toMatchObject({ unstyled: true });
    expect(TOAST_CLASSES.toast).toMatch(/\bbg-popover\b/);
    expect(TOAST_CLASSES.toast).toMatch(/\btext-popover-foreground\b/);
    expect(TOAST_CLASSES.toast).toMatch(/\bborder\b/);
    expect(TOAST_CLASSES.toast).toMatch(/\brounded-xl\b/);
    expect(TOAST_CLASSES.toast).toMatch(/\bshadow-lg\b/);
    expect(TOAST_CLASSES.actionButton).toContain("bg-primary");
    expect(TOAST_CLASSES.actionButton).toContain("phone:min-h-11");
    expect(TOAST_CLASSES.cancelButton).toContain("bg-secondary");
    expect(TOAST_CLASSES.closeButton).toContain("size-11");
    for (const c of Object.values(TOAST_CLASSES)) expect(c).not.toMatch(/#[0-9a-f]{3,8}\b|\b(?:black|white)\b|rgb\(|oklch\(/i);
  });
});

describe("Toaster", () => {
  const shown = async (title?: string) => { const find = () => [...document.querySelectorAll<HTMLElement>("[data-sonner-toast]")].find((e) => !title || e.textContent?.includes(title)); await waitFor(() => expect(find()).toBeDefined()); return find()!; };

  it("shows a toast in the theme's classes with a 44px close button and working action buttons", async () => {
    render(Toaster);
    const undo = vi.fn();
    const also = vi.fn();
    toast("Moved to Groceries", { cancel: { label: "Undo", onClick: undo }, action: { label: "Always", onClick: also } });
    const el = await shown();
    expect(el).toHaveAttribute("data-styled", "false");
    expect(el).toHaveClass("bg-popover", "text-popover-foreground", "rounded-xl", "border");
    const close = screen.getByRole("button", { name: "Dismiss" });
    expect(close).toHaveClass("size-11");
    const always = screen.getByRole("button", { name: "Always" });
    expect(always).toHaveClass("bg-primary");
    await fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(undo).toHaveBeenCalledOnce();
    await fireEvent.click(always);
    expect(also).toHaveBeenCalledOnce();
  });

  it("is dismissed by its close button", async () => {
    render(Toaster);
    toast("One");
    const el = await shown("One");
    await fireEvent.click(within(el).getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(el).toHaveAttribute("data-removed", "true"));
  });

  it("is dismissed by a tap on its text, but a tap on a button is that button's", async () => {
    render(Toaster);
    const run = vi.fn();
    toast("Two", { action: { label: "Do it", onClick: run, } });
    const el = await shown("Two");
    await fireEvent.pointerDown(el.querySelector("[data-title]")!, { clientX: 10, clientY: 10 });
    await fireEvent.click(el.querySelector("[data-title]")!, { clientX: 10, clientY: 10 });
    await waitFor(() => expect(el).toHaveAttribute("data-removed", "true"));
    expect(run).not.toHaveBeenCalled();
  });

  it("ignores the click that ends a drag", async () => {
    render(Toaster);
    toast("Three");
    const el = await shown("Three");
    await fireEvent.pointerDown(el.querySelector("[data-title]")!, { clientX: 10, clientY: 10 });
    await fireEvent.click(el.querySelector("[data-title]")!, { clientX: 80, clientY: 10 });
    expect(el).not.toHaveAttribute("data-removed", "true");
  });

  it("puts the bottom above the tab bar on a phone, and at the page's bottom otherwise", async () => {
    viewport.phone = true;
    render(Toaster);
    toast("Four");
    await shown("Four");
    const list = document.querySelector<HTMLElement>("[data-sonner-toaster]")!;
    expect(list.style.getPropertyValue("--offset-bottom")).toBe(`calc(var(--tabbar-h) + ${BAR_GAP})`);
    expect(list.style.getPropertyValue("--mobile-offset-bottom")).toBe(`calc(var(--tabbar-h) + ${BAR_GAP})`);
    viewport.phone = false;
    await waitFor(() => expect(list.style.getPropertyValue("--offset-bottom")).toBe(`calc(env(safe-area-inset-bottom) + ${EDGE_GAP})`));
    expect(list.style.getPropertyValue("--mobile-offset-bottom")).not.toContain("--tabbar-h");
  });

  it("follows the page's theme, not a fixed one", async () => {
    document.documentElement.className = "";
    render(Toaster);
    toast("Five");
    await shown("Five");
    const list = document.querySelector<HTMLElement>("[data-sonner-toaster]")!;
    expect(list).toHaveAttribute("data-sonner-theme", "light");
    document.documentElement.classList.add("dark");
    await waitFor(() => expect(list).toHaveAttribute("data-sonner-theme", "dark"));
  });
});
