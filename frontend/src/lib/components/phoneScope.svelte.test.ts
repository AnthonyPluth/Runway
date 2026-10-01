// @vitest-environment jsdom
// What a phone leaves to a computer: each place says so in a muted note, and a computer's version is as it was.
import { cleanup, render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn().mockResolvedValue({}), newPage: vi.fn(), session: {} }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/categories.svelte", () => ({ loadCategories: vi.fn(async () => {}), categories: { list: [] }, catLabel: (x: string) => x, catLook: () => ({ color: "#888" }), categoryGroups: () => [] }));

import { api } from "$lib/api";
import { app, route } from "$lib/app.svelte";
import { viewport } from "$lib/phone.svelte";
import type { AppState } from "$lib/types";
import { tx } from "../../test/fixtures";
import type { BudgetCategory } from "./budget/types";
import BudgetRow from "./budget/BudgetRow.svelte";
import MobileNav from "./MobileNav.svelte";
import NotConnected from "./NotConnected.svelte";
import OrderDetail from "./orders/OrderDetail.svelte";
import RecurringFields from "./recurring/RecurringFields.svelte";
import SplitEditor from "./transactions/SplitEditor.svelte";

const NOTE = (what: string) => `Open Runway on a computer to ${what}.`;
beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(api).mockResolvedValue({}); });
afterEach(() => { cleanup(); viewport.phone = false; });

describe("NotConnected", () => {
  it("sends a phone to a computer to connect a bank, with no link to the page that isn't there", () => {
    viewport.phone = true;
    render(NotConnected, { title: "T", secondary: { label: "Add an asset by hand", onclick: vi.fn() } });
    expect(screen.getByText(NOTE("connect a bank"))).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Connect a bank" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add an asset by hand" })).not.toBeInTheDocument();
  });
});

describe("splitting a transaction", () => {
  const t = tx({ amount: -30, category: "Coffee" });

  it("shows the note on a phone, and Close puts it away", async () => {
    viewport.phone = true;
    const onclose = vi.fn();
    render(SplitEditor, { t, onclose, onsaved: vi.fn() });
    expect(screen.getByText(NOTE("split transactions"))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save split" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onclose).toHaveBeenCalledOnce();
  });

  it("is the editor on a computer", () => {
    render(SplitEditor, { t, onclose: vi.fn(), onsaved: vi.fn() });
    expect(screen.getByRole("button", { name: "Save split" })).toBeInTheDocument();
    expect(screen.queryByTestId("desktop-only")).not.toBeInTheDocument();
  });
});

describe("an order's items", () => {
  it("are left to a computer, without even asking for them", () => {
    viewport.phone = true;
    render(OrderDetail, { orderId: "x" });
    expect(screen.getByText(NOTE("see what was in an order"))).toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
  });
});

describe("a recurring item's fields", () => {
  const v = () => ({ name: "Rent", account_id: "a1", amount: -1200, amount_mode: "fixed", frequency: "monthly", dates: "", anchor_date: "2026-10-01", match: "" });
  const accounts = [{ id: "a1", name: "Checking", kind: "checking" }] as never;

  it("keep the basics on a phone and leave More options to a computer", () => {
    viewport.phone = true;
    render(RecurringFields, { v: v(), accounts });
    expect(screen.getByRole("textbox", { name: /Name/ })).toHaveValue("Rent");
    expect(screen.getByText(NOTE("change the account, amount to forecast or merchant text"))).toBeInTheDocument();
    expect(screen.queryByText(/More options/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Merchant text")).not.toBeInTheDocument();
  });

  it("have More options on a computer", () => {
    render(RecurringFields, { v: v(), accounts });
    expect(screen.getByText(/More options/)).toBeInTheDocument();
    expect(screen.getByLabelText("Merchant text")).toBeInTheDocument();
    expect(screen.queryByTestId("desktop-only")).not.toBeInTheDocument();
  });
});

describe("a budget's row", () => {
  const c: BudgetCategory = { name: "Groceries", parent: null, path: ["Groceries"], depth: 0, top: "Groceries", has_children: false, budget: 500, pay_with: null,
    usual_account: null, spent: 200, own_spent: 200, left: 300, rollover_from: null } as BudgetCategory;
  const show = () => render(BudgetRow, { c, month: "2026-03", pace: 0.5, budgets: true, counts: true, payAccounts: [], onsave: vi.fn(), onchanged: vi.fn() });

  it("is progress only on a phone: the figures and the bar, nothing to edit", () => {
    viewport.phone = true;
    show();
    expect(screen.getByText("$500.00")).toBeInTheDocument();
    expect(screen.getByText("$300.00 left")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "40% of budget used" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Budget for Groceries")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Roll over|Set card/ })).not.toBeInTheDocument();
  });

  it("edits in place on a computer", () => {
    show();
    expect(screen.getByLabelText("Budget for Groceries")).toHaveValue(500);
    expect(screen.getByRole("button", { name: /Roll over/ })).toBeInTheDocument();
  });
});

describe("the More sheet", () => {
  beforeEach(() => { app.state = { connected: true, last_sync_ok: new Date(Date.now() - 36e5).toISOString() } as AppState; route.page = "overview"; route.sub = ""; });

  it("only lists pages a phone can use: Net worth, Churning and Settings", async () => {
    render(MobileNav);
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    const sheet = screen.getByRole("dialog", { name: "More pages" });
    expect(Array.from(sheet.querySelectorAll("nav a")).map((a) => a.textContent!.trim())).toEqual(["Net worth", "Churning", "Settings"]);
  });
});
