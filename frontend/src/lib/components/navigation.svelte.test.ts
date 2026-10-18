// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn(), session: {} }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }) }));

import { app, route } from "$lib/app.svelte";
import type { AppState } from "$lib/types";
import MobileNav from "./MobileNav.svelte";
import Sidebar from "./Sidebar.svelte";

const state = (extra: Partial<AppState> = {}): AppState => ({ connected: true, last_sync_ok: new Date(Date.now() - 36e5).toISOString(), ...extra });
// A tap on the dimmed page behind a sheet. Bits UI judges "outside" by where the pointer is, which jsdom has no layout for.
const clickBackdrop = async () => {
  const overlay = document.querySelector<HTMLElement>("[data-slot=sheet-overlay]")!;
  await fireEvent.pointerDown(overlay, { clientX: 5, clientY: 5, button: 0 });
  await fireEvent.pointerUp(overlay, { clientX: 5, clientY: 5, button: 0 });
  await fireEvent.click(overlay);
};
beforeEach(() => { app.state = null; route.page = "overview"; route.sub = ""; });

describe("Sidebar", () => {
  it("links to every page and marks the current one", () => {
    app.state = state();
    route.page = "budget";
    render(Sidebar);
    const nav = screen.getByRole("navigation", { name: "Main" });
    for (const name of ["Overview", "Transactions", "Budget", "Investments", "Recurring", "Reports", "Net worth", "Churning"])
      expect(within(nav).getByRole("link", { name })).toHaveAttribute("href", expect.stringMatching(/^#/));
    expect(within(nav).getByRole("link", { name: "Budget" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "#setup");
  });

  it("lists Investments right after Budget", () => {
    app.state = state();
    render(Sidebar);
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getAllByRole("link").map((l) => l.textContent!.trim())).toEqual(
      ["Overview", "Transactions", "Budget", "Investments", "Recurring", "Reports", "Net worth", "Churning"]);
  });

  it.each([["investments", "", "Investments"], ["networth", "investments", "Investments"], ["networth", "equity", "Net worth"], ["networth", "retirement", "Net worth"], ["recurring", "", "Recurring"]])(
    "keeps %s/%s lit as %s", (page, sub, label) => {
      app.state = state();
      route.page = page; route.sub = sub;
      render(Sidebar);
      const nav = screen.getByRole("navigation", { name: "Main" });
      expect(within(nav).getByRole("link", { name: label })).toHaveAttribute("aria-current", "page");
      expect(within(nav).getAllByRole("link").filter((l) => l.hasAttribute("aria-current"))).toHaveLength(1);
    });

  it("shows Review as part of Transactions", () => {
    app.state = state();
    route.page = "review";
    render(Sidebar);
    expect(screen.getByRole("link", { name: /Transactions/ })).toHaveAttribute("aria-current", "page");
  });

  it("badges Transactions with what needs review", () => {
    app.state = state({ review_count: 7 });
    render(Sidebar);
    expect(screen.getByRole("link", { name: /Transactions/ })).toHaveTextContent("7");
  });

  it("shows the version and the sync status", () => {
    app.state = state({ version: "1.4.0" });
    render(Sidebar);
    expect(screen.getByText("1.4.0")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/^Up to date · /);
    expect(within(screen.getByRole("status")).queryByRole("link")).toBeNull();
  });

  it("shows who's signed in, with initials and a sign-out link, but nothing for the local user", () => {
    app.state = state({ user: { name: "Ann Lee", email: "ann@x.com" } });
    const { unmount } = render(Sidebar);
    expect(screen.getByText("AL")).toBeInTheDocument();
    expect(screen.getByText("Ann Lee")).toHaveAttribute("title", "ann@x.com");
    expect(screen.getByRole("link", { name: "Sign out" })).toBeInTheDocument();
    unmount();
    app.state = state({ user: { local: true } });
    render(Sidebar);
    expect(screen.queryByRole("link", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("uses the email's parts for initials when there's no name", () => {
    app.state = state({ user: { email: "jo.smith@x.com" } });
    render(Sidebar);
    expect(screen.getByText("JS")).toBeInTheDocument();
  });

  it("tells a failed sync apart", () => {
    app.state = state({ last_log: { ok: false, message: "Bank said no" } });
    render(Sidebar);
    expect(screen.getByRole("status")).toHaveTextContent("Last sync failed");
    expect(screen.getByRole("status")).toHaveAttribute("title", "Bank said no");
    expect(screen.getByText("Bank said no")).toBeInTheDocument();
    expect(within(screen.getByRole("status")).getByRole("link", { name: "Last sync failed" })).toHaveAttribute("href", "#setup/connections");
  });

  it("warns, in view, when a bank needs attention after a sync that worked", () => {
    app.state = state({ sync_warnings: ["Chase: log in again"] });
    render(Sidebar);
    const link = within(screen.getByRole("status")).getByRole("link", { name: "Synced · 1 bank needs attention" });
    expect(link).toHaveAttribute("href", "#setup/connections");
    expect(link).toHaveClass("text-warning");
    expect(screen.getByText("Chase: log in again")).toHaveClass("truncate");
  });

  it("says how old a sync from days ago is", () => {
    app.state = state({ last_sync_ok: new Date(Date.now() - 3 * 864e5).toISOString() });
    render(Sidebar);
    expect(within(screen.getByRole("status")).getByRole("link", { name: "Updated 3 days ago" })).toBeInTheDocument();
  });
});

describe("Sidebar as a tablet rail", () => {
  it("is 72px wide from 768 to 1023 and the full 256px sidebar from 1024", () => {
    app.state = state();
    render(Sidebar);
    const aside = screen.getByRole("complementary");
    // The rail: and wide: variants are the tablet and desktop media queries (app.css), so these are the widths at each size.
    expect(aside).toHaveClass("w-64", "rail:w-[72px]", "desktop:flex", "hidden");
    expect(aside.className).not.toMatch(/(^|\s)(md|lg):/);
  });

  it("keeps every destination's accessible name, and shows the label as a tooltip on hover and keyboard focus", () => {
    app.state = state();
    render(Sidebar);
    const nav = screen.getByRole("navigation", { name: "Main" });
    for (const name of ["Overview", "Transactions", "Budget", "Investments", "Net worth", "Churning"]) {
      const link = within(nav).getByRole("link", { name });
      expect(link).toHaveClass("rail:size-11", "group/nav");
      const tip = within(link).getByText(name);
      expect(tip).toHaveClass("rail:opacity-0", "rail:group-hover/nav:opacity-100", "rail:group-focus-visible/nav:opacity-100");
    }
    expect(screen.getByRole("link", { name: "Settings" })).toHaveClass("rail:size-11");
  });

  it("lights the current destination in the rail, and badges Transactions on the icon", () => {
    app.state = state({ review_count: 7 });
    route.page = "budget";
    render(Sidebar);
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Budget" })).toHaveClass("rail:before:bg-primary", "bg-sidebar-accent");
    expect(within(nav).getByRole("link", { name: "Overview" })).not.toHaveClass("rail:before:bg-primary");
    expect(within(nav).getByRole("link", { name: /Transactions/ })).toHaveTextContent("7");
    expect(screen.getByText("7")).toHaveClass("rail:absolute");
  });

  it("has a Menu button that opens a sheet with the full labels, the account and sync details and sign-out", async () => {
    app.state = state({ version: "1.4.0", user: { name: "Ann Lee", email: "ann@x.com" }, review_count: 3, sync_warnings: ["Chase: log in again"] });
    render(Sidebar);
    const menu = screen.getByRole("button", { name: "Menu, Synced · 1 bank needs attention" });
    expect(screen.getByTestId("sync-dot")).toHaveClass("bg-warning");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(menu);
    const sheet = screen.getByRole("dialog");
    const pages = within(sheet).getByRole("navigation", { name: "All pages" });
    expect(within(pages).getAllByRole("link").map((l) => l.textContent!.trim())).toEqual(
      ["Overview", "Transactions 3", "Budget", "Investments", "Recurring", "Reports", "Net worth", "Churning", "Settings"]);
    expect(within(sheet).getByRole("link", { name: "Net worth" })).toHaveAttribute("href", "#networth");
    expect(within(sheet).getByText("Ann Lee")).toBeInTheDocument();
    expect(within(sheet).getByRole("link", { name: "Synced · 1 bank needs attention" })).toHaveAttribute("href", "#setup/connections");
    expect(within(sheet).getByText("Chase: log in again")).toBeInTheDocument();
    expect(within(sheet).getByRole("link", { name: "Sign out" })).toHaveAttribute("href", "/auth/logout");
  });

  it("traps focus in the menu sheet and gives it back to Menu on Escape", async () => {
    app.state = state();
    render(Sidebar);
    const menu = screen.getByRole("button", { name: "Menu" });
    await userEvent.click(menu);
    const sheet = screen.getByRole("dialog");
    await userEvent.tab();
    await userEvent.tab();
    expect(sheet).toContainElement(document.activeElement as HTMLElement);
    for (let i = 0; i < 20; i++) await userEvent.tab();
    expect(sheet).toContainElement(document.activeElement as HTMLElement);
    await userEvent.keyboard("{Escape}");
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(menu).toHaveFocus();
  });

  it("slides the menu in from the left, where the rail is", async () => {
    app.state = state();
    render(Sidebar);
    await userEvent.click(screen.getByRole("button", { name: "Menu" }));
    const sheet = screen.getByRole("dialog");
    expect(sheet).toHaveClass("md:left-0", "md:data-[state=open]:slide-in-from-left", "md:data-[state=closed]:slide-out-to-left");
    expect(sheet).not.toHaveClass("md:data-[state=open]:slide-in-from-right", "md:right-0");
  });

  it("closes the menu when you follow the sync warning, even from Settings", async () => {
    app.state = state({ sync_warnings: ["Chase: log in again"] });
    route.page = "setup"; route.sub = "";
    render(Sidebar);
    await userEvent.click(screen.getByRole("button", { name: /^Menu/ }));
    const link = within(screen.getByRole("dialog")).getByRole("link", { name: "Synced · 1 bank needs attention" });
    link.addEventListener("click", (e) => e.preventDefault());   // (no hash change left over for the next test)
    await userEvent.click(link);
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("closes the menu when only the part after the slash changes", async () => {
    app.state = state();
    route.page = "setup"; route.sub = "";
    render(Sidebar);
    await userEvent.click(screen.getByRole("button", { name: "Menu" }));
    route.sub = "connections";
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("closes the menu when you go to a page", async () => {
    app.state = state();
    render(Sidebar);
    await userEvent.click(screen.getByRole("button", { name: "Menu" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("link", { name: "Reports" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("MobileNav", () => {
  it("has the main tabs and a More button, with the rest tucked away", () => {
    app.state = state();
    render(MobileNav);
    const bar = screen.getByRole("navigation", { name: "Main" });
    expect(within(bar).getAllByRole("link").map((l) => l.textContent!.trim())).toEqual(["Overview", "Transactions", "Budget", "Invest"]);
    expect(within(bar).getAllByRole("link").map((l) => l.getAttribute("href"))).toEqual(["#overview", "#transactions", "#budget", "#investments"]);
    expect(within(bar).getByRole("button", { name: "More" })).toHaveAttribute("aria-expanded", "false");
  });

  it.each([["investments", ""], ["networth", "investments"]])("lights the Invest tab on %s/%s", (page, sub) => {
    app.state = state();
    route.page = page; route.sub = sub;
    render(MobileNav);
    const lit = within(screen.getByRole("navigation", { name: "Main" })).getAllByRole("link").filter((l) => l.hasAttribute("aria-current"));
    expect(lit.map((l) => l.textContent!.trim())).toEqual(["Invest"]);
  });

  it("floats as a glass pill 12px in from the sides and the bottom (and the inset), 56px of tabs", () => {
    app.state = state();
    render(MobileNav);
    const bar = screen.getByRole("navigation", { name: "Main" });
    expect(bar).toHaveClass("fixed", "inset-x-3", "bottom-[calc(env(safe-area-inset-bottom)+0.75rem)]", "rounded-[22px]");
    // No outline (the border keeps its width but is transparent), a solid surface where backdrop-filter is missing, and the glass where it is.
    expect(bar).toHaveClass("border", "border-transparent", "bg-popover", "supports-[backdrop-filter]:bg-white/[0.04]",
      "supports-[backdrop-filter]:backdrop-blur-[28px]", "supports-[backdrop-filter]:backdrop-saturate-200", "supports-[backdrop-filter]:backdrop-brightness-110");
    expect(bar.className).toContain("shadow-[inset_0_1px_0_rgb(255_255_255/0.2)");
    expect(bar).not.toHaveClass("border-t", "bg-popover/90", "backdrop-blur-xl");
    // 56px less 4px of padding each side: tabs 48px tall, over the 44px a finger needs.
    expect(bar.firstElementChild).toHaveClass("h-[56px]", "grid-cols-5", "p-1");
    expect(bar.firstElementChild).not.toHaveClass("h-[66px]");
  });

  it("draws the inactive tabs lighter than muted-foreground so the labels stay readable over the glass, and the current one white", () => {
    app.state = state();
    render(MobileNav);
    const bar = screen.getByRole("navigation", { name: "Main" });
    expect(within(bar).getByRole("link", { name: "Transactions" })).toHaveClass("text-[#b9bec7]");
    expect(within(bar).getByRole("button", { name: /More/ })).toHaveClass("text-[#b9bec7]");
    expect(within(bar).getByRole("link", { name: "Overview" })).toHaveClass("bg-foreground/12", "text-foreground");
  });

  it("is the height app.css calls --tabbar-h, which the page padding and the toasts are measured from", () => {
    const css = readFileSync(resolve(__dirname, "../../app.css"), "utf8");
    // 1px border twice + 56px of tabs + 12px lifted + the inset: 70px with no inset, 104px with an iPhone's 34px.
    expect(css).toMatch(/--tabbar-h:\s*calc\(70px \+ env\(safe-area-inset-bottom\)\)/);
    const shell = readFileSync(resolve(__dirname, "../../App.svelte"), "utf8");
    expect(shell).toContain("pb-[calc(var(--tabbar-h)+2rem)]");
  });

  it("dots More when the sync needs attention, so it's seen without opening the sheet", async () => {
    app.state = state();
    const { unmount } = render(MobileNav);
    expect(screen.queryByTestId("sync-dot")).toBeNull();
    unmount();
    app.state = state({ sync_warnings: ["Chase: log in again"] });
    render(MobileNav);
    expect(screen.getByTestId("sync-dot")).toHaveClass("bg-warning");
    const more = screen.getByRole("button", { name: "More, Synced · 1 bank needs attention" });
    await userEvent.click(more);
    const sheet = screen.getByRole("dialog", { name: "More pages" });
    expect(within(sheet).getByRole("link", { name: "Synced · 1 bank needs attention" })).toHaveAttribute("href", "#setup/connections");
    expect(within(sheet).getByText("Chase: log in again")).toBeInTheDocument();
  });

  it("dots More in red when the last sync failed", () => {
    app.state = state({ last_log: { ok: false, message: "Down" } });
    render(MobileNav);
    expect(screen.getByTestId("sync-dot")).toHaveClass("bg-destructive");
  });

  it("caps the review badge at 99+", () => {
    app.state = state({ review_count: 120 });
    render(MobileNav);
    expect(screen.getByText("99+")).toBeInTheDocument();
  });

  it("opens a sheet of the other pages, and closes it on Escape", async () => {
    app.state = state({ version: "1.4.0", user: { name: "Ann", email: "ann@x.com" } });
    render(MobileNav);
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    const sheet = screen.getByRole("dialog", { name: "More pages" });
    for (const name of ["Reports", "Net worth", "Churning", "Settings"]) expect(within(sheet).getByRole("link", { name })).toBeInTheDocument();
    expect(within(sheet).queryByRole("link", { name: "Investments" })).not.toBeInTheDocument();
    expect(within(sheet).getByRole("link", { name: "Recurring" })).toHaveAttribute("href", "#recurring");
    expect(sheet).toHaveTextContent("Runway 1.4.0");
    expect(within(sheet).getByRole("link", { name: /Sign out ann@x\.com/ })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes the sheet when you go to a page", async () => {
    app.state = state();
    render(MobileNav);
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    route.page = "budget";
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("lights up More when the current page lives in it", () => {
    app.state = state();
    route.page = "networth";
    render(MobileNav);
    expect(screen.getByRole("button", { name: "More" })).toHaveClass("text-foreground");
  });

  it("lights up More on Recurring, which lives in the sheet, with no tab lit", () => {
    app.state = state();
    route.page = "recurring"; route.sub = "";
    render(MobileNav);
    expect(screen.getByRole("button", { name: "More" })).toHaveClass("text-foreground");
    expect(within(screen.getByRole("navigation", { name: "Main" })).getAllByRole("link").some((l) => l.hasAttribute("aria-current"))).toBe(false);
  });

  describe("More sheet focus", () => {
    // jsdom has no layout, so every element looks hidden to the focus trap's tabbable check; give each a box.
    beforeEach(() => { vi.spyOn(Element.prototype, "getClientRects").mockImplementation(() => [{}] as unknown as DOMRectList); });
    afterEach(() => { vi.restoreAllMocks(); });
    const openMore = async () => {
      app.state = state({ version: "1.4.0", user: { name: "Ann", email: "ann@x.com" } });
      render(MobileNav);
      const more = screen.getByRole("button", { name: "More" });
      await userEvent.click(more);
      return { more, sheet: await screen.findByRole("dialog", { name: "More pages" }) };
    };
    const tabbables = (sheet: HTMLElement) => Array.from(sheet.querySelectorAll<HTMLElement>("a[href], button:not([disabled])"));

    it("stays a bottom sheet at md widths, where a phone turned sideways is", async () => {
      const { sheet } = await openMore();
      expect(sheet).toHaveClass("md:inset-x-0", "md:h-auto", "md:rounded-t-2xl");
      expect(sheet).not.toHaveClass("md:right-0", "md:w-[440px]", "md:inset-y-0");
    });

    it("moves focus into the sheet when it opens", async () => {
      const { sheet } = await openMore();
      await vi.waitFor(() => expect(sheet).toContainElement(document.activeElement as HTMLElement));
    });

    it("traps Tab going forward, past the last control and round again", async () => {
      const { sheet } = await openMore();
      await vi.waitFor(() => expect(sheet).toContainElement(document.activeElement as HTMLElement));
      const seq: string[] = [];
      for (let i = 0; i < tabbables(sheet).length * 2 + 2; i++) {
        seq.push((document.activeElement?.textContent || document.activeElement?.tagName || "").trim().slice(0, 12));
        await userEvent.tab();
        expect(sheet, `steps ${seq.join(" > ")}`).toContainElement(document.activeElement as HTMLElement);
      }
    });

    it("traps Shift+Tab going backward, past the first control and round again", async () => {
      const { sheet } = await openMore();
      await vi.waitFor(() => expect(sheet).toContainElement(document.activeElement as HTMLElement));
      const seq: string[] = [];
      for (let i = 0; i < tabbables(sheet).length * 2 + 2; i++) {
        seq.push((document.activeElement?.textContent || document.activeElement?.tagName || "").trim().slice(0, 12));
        await userEvent.tab({ shift: true });
        expect(sheet, `steps ${seq.join(" > ")}`).toContainElement(document.activeElement as HTMLElement);
      }
    });

    it("makes the bottom tabs inert while open, and live again after", async () => {
      const { sheet } = await openMore();
      const bar = screen.getByRole("navigation", { name: "Main", hidden: true });
      expect(bar).toHaveAttribute("inert");
      await userEvent.keyboard("{Escape}");
      await vi.waitFor(() => expect(sheet).not.toBeInTheDocument());
      expect(bar).not.toHaveAttribute("inert");
    });

    it("returns focus to More on Escape", async () => {
      const { more } = await openMore();
      await userEvent.keyboard("{Escape}");
      await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await vi.waitFor(() => expect(more).toHaveFocus());
    });

    it("returns focus to More on the Close button", async () => {
      const { more, sheet } = await openMore();
      await userEvent.click(within(sheet).getByRole("button", { name: "Close" }));
      await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await vi.waitFor(() => expect(more).toHaveFocus());
    });

    it("closes from the backdrop and returns focus to More", async () => {
      const { more } = await openMore();
      await clickBackdrop();
      await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await vi.waitFor(() => expect(more).toHaveFocus());
    });

    it("closes on choosing a page without pulling focus back to More", async () => {
      const { more, sheet } = await openMore();
      await vi.waitFor(() => expect(sheet).toContainElement(document.activeElement as HTMLElement));
      route.page = "reports";
      await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await new Promise((r) => setTimeout(r, 50));
      expect(more).not.toHaveFocus();
    });

    it("can be opened and closed repeatedly without leaving the page locked", async () => {
      const { more } = await openMore();
      for (let i = 0; i < 3; i++) {
        await userEvent.keyboard("{Escape}");
        await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        await vi.waitFor(() => expect(document.body.style.pointerEvents).not.toBe("none"));
        await userEvent.click(more);
        await screen.findByRole("dialog", { name: "More pages" });
      }
      await userEvent.keyboard("{Escape}");
      await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await vi.waitFor(() => expect(document.body.style.pointerEvents).not.toBe("none"));
      expect(document.querySelectorAll("[data-bits-focus-guard], [data-focus-guard]")).toHaveLength(0);
    });
  });

  it("closes the sheet from the backdrop", async () => {
    app.state = state();
    render(MobileNav);
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    await clickBackdrop();
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
