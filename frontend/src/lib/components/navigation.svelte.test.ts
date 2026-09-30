// @vitest-environment jsdom
// The sidebar (computers) and the tab bar (phones): the same destinations, the same sync line.
import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }) }));

import { app, route } from "$lib/app.svelte";
import type { AppState } from "$lib/types";
import MobileNav from "./MobileNav.svelte";
import Sidebar from "./Sidebar.svelte";

const state = (extra: Partial<AppState> = {}): AppState => ({ connected: true, last_sync_ok: "2020-01-05T09:00:00", ...extra });
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

  it.each([["networth", "investments", "Net worth"], ["networth", "equity", "Net worth"], ["budget", "recurring", "Budget"]])(
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
    expect(screen.getByRole("status")).toHaveTextContent("Up to date · Jan 5");
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
