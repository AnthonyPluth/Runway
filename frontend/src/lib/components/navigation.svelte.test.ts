// @vitest-environment jsdom
// The sidebar (computers) and the tab bar (phones): the same destinations, the same sync line.
import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn(), session: {} }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }) }));

import { app, route } from "$lib/app.svelte";
import type { AppState } from "$lib/types";
import MobileNav from "./MobileNav.svelte";
import Sidebar from "./Sidebar.svelte";

// A sync an hour ago: up to date.
const state = (extra: Partial<AppState> = {}): AppState => ({ connected: true, last_sync_ok: new Date(Date.now() - 36e5).toISOString(), ...extra });
beforeEach(() => { app.state = null; route.page = "overview"; route.sub = ""; });

describe("Sidebar", () => {
  it("links to every page and marks the current one", () => {
    app.state = state();
    route.page = "budget";
    render(Sidebar);
    const nav = screen.getByRole("navigation", { name: "Main" });
    for (const name of ["Overview", "Transactions", "Budget", "Reports", "Net worth", "Churning"])
      expect(within(nav).getByRole("link", { name })).toHaveAttribute("href", expect.stringMatching(/^#/));
    expect(within(nav).getByRole("link", { name: "Budget" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "#setup");
  });

  it("no longer lists Investments or Recurring as pages of their own", () => {
    app.state = state();
    render(Sidebar);
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getAllByRole("link").map((l) => l.textContent!.trim())).toEqual(
      ["Overview", "Transactions", "Budget", "Reports", "Net worth", "Churning"]);
  });

  it.each([["networth", "investments", "Net worth"], ["networth", "equity", "Net worth"], ["networth", "retirement", "Net worth"], ["budget", "recurring", "Budget"]])(
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
    // The reason is in view (not only on hover), and the status links to where it can be fixed.
    expect(screen.getByText("Bank said no")).toBeInTheDocument();
    expect(within(screen.getByRole("status")).getByRole("link", { name: "Last sync failed" })).toHaveAttribute("href", "#setup/connections");
  });

  it("warns, in view, when a bank needs attention after a sync that worked", () => {
    app.state = state({ sync_warnings: ["Chase: log in again"] });
    render(Sidebar);
    const link = within(screen.getByRole("status")).getByRole("link", { name: "Synced · 1 bank needs attention" });
    expect(link).toHaveAttribute("href", "#setup/connections");
    expect(link).toHaveClass("text-amber-500");
    expect(screen.getByText("Chase: log in again")).toHaveClass("truncate");
  });

  it("says how old a sync from days ago is", () => {
    app.state = state({ last_sync_ok: new Date(Date.now() - 3 * 864e5).toISOString() });
    render(Sidebar);
    expect(within(screen.getByRole("status")).getByRole("link", { name: "Updated 3 days ago" })).toBeInTheDocument();
  });
});

describe("MobileNav", () => {
  it("has the main tabs and a More button, with the rest tucked away", () => {
    app.state = state();
    render(MobileNav);
    const bar = screen.getByRole("navigation", { name: "Main" });
    expect(within(bar).getAllByRole("link").map((l) => l.textContent!.trim())).toEqual(["Overview", "Transactions", "Budget", "Reports"]);
    expect(within(bar).getByRole("button", { name: "More" })).toHaveAttribute("aria-expanded", "false");
  });

  it("dots More when the sync needs attention, so it's seen without opening the sheet", async () => {
    app.state = state();
    const { unmount } = render(MobileNav);
    expect(screen.queryByTestId("sync-dot")).toBeNull();
    unmount();
    app.state = state({ sync_warnings: ["Chase: log in again"] });
    render(MobileNav);
    expect(screen.getByTestId("sync-dot")).toHaveClass("bg-amber-500");
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
    for (const name of ["Net worth", "Churning", "Settings"]) expect(within(sheet).getByRole("link", { name })).toBeInTheDocument();
    expect(within(sheet).queryByRole("link", { name: "Investments" })).not.toBeInTheDocument();
    expect(within(sheet).queryByRole("link", { name: "Recurring" })).not.toBeInTheDocument();
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

  it("lights up Budget, not More, on its Bills & income tab", () => {
    app.state = state();
    route.page = "budget"; route.sub = "recurring";
    render(MobileNav);
    expect(screen.getByRole("link", { name: "Budget" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "More" })).not.toHaveClass("text-foreground");
  });

  it("closes the sheet from the backdrop", async () => {
    app.state = state();
    render(MobileNav);
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Close" })[0]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
