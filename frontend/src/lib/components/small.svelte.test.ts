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

  it("fades an edge while there are more tabs past it", async () => {
    render(SubTabs, { tabs, current: "all", label: "x" });
    const nav = screen.getByRole("navigation", { name: "x" });
    expect(nav.style.getPropertyValue("--fade-end")).toBe("0px");   // everything fits
    Object.defineProperty(nav, "scrollWidth", { configurable: true, value: 500 });
    Object.defineProperty(nav, "clientWidth", { configurable: true, value: 300 });
    nav.scrollLeft = 0;
    nav.dispatchEvent(new Event("scroll"));
    expect(nav.style.getPropertyValue("--fade-start")).toBe("0px");
    expect(nav.style.getPropertyValue("--fade-end")).toBe("24px");
    nav.scrollLeft = 200;
    nav.dispatchEvent(new Event("scroll"));
    expect(nav.style.getPropertyValue("--fade-start")).toBe("24px");
    expect(nav.style.getPropertyValue("--fade-end")).toBe("0px");
  });
});

describe("MissedAlert", () => {
  const m = { key: "rec:7:2026-03-02", name: "Rent", amount: -1500, date: "2026-03-02", account_id: "a1", account_name: "Checking", recurring_id: 7 };
  const today = "2026-03-11";
  beforeEach(() => { location.hash = ""; });
  const undo = async () => {
    const opts = vi.mocked(toast).mock.calls.at(-1)![1] as unknown as { action: { onClick: () => Promise<void> } };
    await opts.action.onClick();
  };

  it("says what's missing, how much, how late and where", () => {
    render(MissedAlert, { m, today });
    expect(screen.getByText("Rent")).toBeInTheDocument();
    expect(screen.getByText("−$1,500.00")).toBeInTheDocument();
    expect(screen.getByText("9 days late")).toHaveClass("text-warning");
    expect(screen.getByText(/due Mar 2 · Checking/)).toBeInTheDocument();
    render(MissedAlert, { m: { ...m, key: "k2", name: "Pay", amount: 2000 }, today: "2026-03-03" });
    expect(screen.getByText("+$2,000.00")).toBeInTheDocument();
    expect(screen.getByText("1 day late")).toBeInTheDocument();
  });

  it("'Link a transaction' lists the payments it could be, right here, and links the one you pick", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => path.includes("/candidates")
      ? [{ id: "t1", posted: "2026-03-05", amount: -1500, name: "City Rnt Pmt" }, { id: "t2", posted: "2026-03-03", amount: -1480, name: "Sofa Store" }]
      : { ok: true }) as never);
    const ondone = vi.fn();
    render(MissedAlert, { m, today, ondone });
    await userEvent.click(screen.getByRole("button", { name: "Link a transaction" }));
    expect(api).toHaveBeenCalledWith("/api/recurring/7/candidates?date=2026-03-02");
    const list = await screen.findByRole("list", { name: "Payments that could be Rent" });
    expect(list).toHaveTextContent(/Mar.5\s*City Rnt Pmt\s*−\$1,500\.00/);
    await userEvent.click(screen.getByRole("button", { name: /^Link City Rnt Pmt on Mar.5$/ }));
    expect(api).toHaveBeenCalledWith("/api/transactions/t1/recurring", { method: "POST", body: { recurring_id: 7 } });
    expect(api).toHaveBeenCalledWith("/api/recurring/dismiss", { method: "POST", body: { key: m.key } });
    expect(toast.success).toHaveBeenCalledWith("Linked to Rent");
    expect(ondone).toHaveBeenCalledWith(m.key);
    expect(screen.queryByText("Rent")).not.toBeInTheDocument();
  });

  it("offers to match a linked payment's text from now on", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => path.includes("/candidates") ? [{ id: "t1", posted: "2026-03-05", amount: -1500, name: "City Rnt Pmt" }]
      : path.endsWith("/recurring") ? { ok: true, suggest_text: "city rnt pmt" } : { ok: true, linked: 0 }) as never);
    render(MissedAlert, { m, today });
    await userEvent.click(screen.getByRole("button", { name: "Link a transaction" }));
    await userEvent.click(await screen.findByRole("button", { name: /^Link City/ }));
    const [msg, opts] = vi.mocked(toast.success).mock.calls.at(-1)! as unknown as [string, { action: { onClick: () => Promise<void> } }];
    expect(msg).toBe("Linked to Rent");
    await opts.action.onClick();
    expect(api).toHaveBeenCalledWith("/api/recurring/7/match", { method: "POST", body: { text: "city rnt pmt" } });
  });

  it("keeps the row when linking fails", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => {
      if (path.includes("/candidates")) return [{ id: "t1", posted: "2026-03-05", amount: -1500, name: "City Rnt Pmt" }];
      throw new Error("Try later");
    }) as never);
    render(MissedAlert, { m, today });
    await userEvent.click(screen.getByRole("button", { name: "Link a transaction" }));
    await userEvent.click(await screen.findByRole("button", { name: /^Link City/ }));
    expect(toast.error).toHaveBeenCalledWith("Try later");
    expect(screen.getByText("Rent")).toBeInTheDocument();
  });

  it("with nothing close, opens Transactions on that account and month to look further", async () => {
    vi.mocked(api).mockResolvedValue([] as never);
    render(MissedAlert, { m, today });
    await userEvent.click(screen.getByRole("button", { name: "Link a transaction" }));
    await userEvent.click(await screen.findByRole("link", { name: "Look in Transactions" }));
    expect(txFilters.transactions).toMatchObject({ account: "a1", from: "2026-03-01", to: "2026-03-31" });
    expect(location.hash).toBe("#transactions?account=a1&from=2026-03-01&to=2026-03-31");
  });

  it("says when it couldn't look, with a Retry", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("offline")).mockResolvedValue([] as never);
    render(MissedAlert, { m, today });
    await userEvent.click(screen.getByRole("button", { name: "Link a transaction" }));
    expect(await screen.findByText(/Couldn’t look for payments/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(/Nothing close to Mar 2/)).toBeInTheDocument();
  });

  it("'Skip this one' is a $0 for that date, tells the page and removes the row; Undo puts it back", async () => {
    vi.mocked(api).mockResolvedValue({});
    const ondone = vi.fn(), onundone = vi.fn();
    render(MissedAlert, { m, today, ondone, onundone });
    await userEvent.click(screen.getByRole("button", { name: "Skip this one" }));
    expect(api).toHaveBeenCalledWith("/api/overrides", { method: "POST", body: { key: m.key, amount: 0 } });
    expect(ondone).toHaveBeenCalledWith(m.key);
    expect(screen.queryByText("Rent")).not.toBeInTheDocument();
    expect(toast).toHaveBeenLastCalledWith("Skipped Rent on Mar\u00a02", expect.anything());
    await undo();
    expect(api).toHaveBeenCalledWith("/api/overrides", { method: "DELETE", body: { key: m.key } });
    expect(onundone).toHaveBeenCalledWith(m.key);
    expect(await screen.findByText("Rent")).toBeInTheDocument();
  });

  it("keeps the row and shows the error when skipping fails", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Try later"));
    render(MissedAlert, { m, today });
    await userEvent.click(screen.getByRole("button", { name: "Skip this one" }));
    expect(toast.error).toHaveBeenCalledWith("Try later");
    expect(screen.getByText("Rent")).toBeInTheDocument();
  });
});
