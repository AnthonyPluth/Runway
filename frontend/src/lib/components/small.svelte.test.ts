// @vitest-environment jsdom
// The little shared display components: an account's label, the tab strip, the "Needs attention" row.
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import { txFilters } from "$lib/filters.svelte";
import { toast } from "svelte-sonner";
import AcctLabel from "./AcctLabel.svelte";
import MissedAlert from "./MissedAlert.svelte";
import SubTabs from "./SubTabs.svelte";

afterEach(() => { app.state = null; vi.clearAllMocks(); });

describe("AcctLabel", () => {
  it("shows the account's name alone when its institution isn't known", () => {
    render(AcctLabel, { id: "a1", name: "Checking" });
    expect(screen.getByText("Checking")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("shows the institution's logo", () => {
    app.state = { connected: true, brands: { a1: { institution: "Chase", src: "/api/merchants/brand%3Achase/logo" } } };
    render(AcctLabel, { id: "a1", name: "Checking" });
    const img = document.querySelector("img")!;
    expect(img).toHaveAttribute("src", "/api/merchants/brand%3Achase/logo");
    expect(img).toHaveAttribute("title", "Chase");
  });

  it("shows a logo Runway fetched from Logo.dev for an institution it doesn't bundle one for", () => {
    app.state = { connected: true, brands: { a1: { institution: "Wealthfront", src: "/api/merchants/brand%3Awealthfront/logo", initial: "W" } } };
    render(AcctLabel, { id: "a1", name: "Roth IRA" });
    expect(document.querySelector("img")).toHaveAttribute("src", "/api/merchants/brand%3Awealthfront/logo");
  });

  it("falls back to the institution's initial when it has no logo", () => {
    app.state = { connected: true, brands: { a1: { institution: "Local CU", initial: "L" } } };
    render(AcctLabel, { id: "a1", name: "Savings" });
    expect(screen.getByTitle("Local CU")).toHaveTextContent("L");
  });
});

describe("SubTabs", () => {
  const tabs = [{ href: "#transactions", label: "All", id: "all" }, { href: "#review", label: "Review", id: "review", badge: 4 }];

  it("marks the current tab as the current page and shows a badge for what needs attention", () => {
    render(SubTabs, { tabs, current: "review", label: "Transactions views" });
    expect(screen.getByRole("navigation", { name: "Transactions views" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Review/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "All" })).not.toHaveAttribute("aria-current");
    expect(screen.getByText("4")).toBeInTheDocument();
  });

  it("links to each tab's own route", () => {
    render(SubTabs, { tabs, current: "all", label: "x" });
    expect(screen.getByRole("link", { name: "All" })).toHaveAttribute("href", "#transactions");
  });
});

describe("MissedAlert", () => {
  const m = { key: "rent-2026-03", name: "Rent", amount: -1500, date: "2026-03-01", account_id: "a1", account_name: "Checking" };
  beforeEach(() => { location.hash = ""; });

  it("says what's missing, how much and where", () => {
    render(MissedAlert, { m });
    expect(screen.getByText(/\$1,500\.00 expected/)).toHaveTextContent("Rent: $1,500.00 expected Mar 1 hasn't shown up in Checking.");
  });

  it("says 'expected in' for money that should have come in", () => {
    render(MissedAlert, { m: { ...m, name: "Pay", amount: 2000 } });
    expect(screen.getByText(/expected in/)).toBeInTheDocument();
  });

  it("'Find it' opens Transactions on that account and month", async () => {
    render(MissedAlert, { m });
    await userEvent.click(screen.getByRole("link", { name: "Find it" }));
    expect(txFilters.transactions).toMatchObject({ account: "a1", from: "2026-03-01", to: "2026-03-31" });
    expect(location.hash).toBe("#transactions?account=a1&from=2026-03-01&to=2026-03-31");
    expect(toast).toHaveBeenCalled();
  });

  it("'Dismiss' forgets it, tells the page and removes the row", async () => {
    vi.mocked(api).mockResolvedValue({});
    const ondismiss = vi.fn();
    render(MissedAlert, { m, ondismiss });
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(api).toHaveBeenCalledWith("/api/recurring/dismiss", { method: "POST", body: { key: m.key } });
    expect(ondismiss).toHaveBeenCalledWith(m.key);
    expect(screen.queryByText("Rent")).not.toBeInTheDocument();
  });

  it("keeps the row and shows the error when dismissing fails", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Try later"));
    render(MissedAlert, { m });
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(toast.error).toHaveBeenCalledWith("Try later");
    expect(screen.getByText("Rent")).toBeInTheDocument();
  });
});
