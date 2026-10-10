// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn().mockResolvedValue({}), newPage: vi.fn(), session: {} }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/categories.svelte", () => ({ loadCategories: vi.fn(async () => {}), categories: { list: [] }, catLabel: (x: string) => x, catLook: () => ({ color: "#888" }), categoryGroups: () => [], catParentOf: () => null }));

import { api } from "$lib/api";
import { app, route } from "$lib/app.svelte";
import { viewport } from "$lib/phone.svelte";
import type { AppState } from "$lib/types";
import { tx } from "../../test/fixtures";
import type { BudgetCategory } from "./budget/types";
import BudgetRow from "./budget/BudgetRow.svelte";
import Benefits from "./churning/Benefits.svelte";
import MobileNav from "./MobileNav.svelte";
import NotConnected from "./NotConnected.svelte";
import OrderDetail from "./orders/OrderDetail.svelte";
import RecurringFields from "./recurring/RecurringFields.svelte";
import SplitEditor from "./transactions/SplitEditor.svelte";

beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(api).mockResolvedValue({}); });
afterEach(() => { cleanup(); viewport.phone = false; });

describe("NotConnected", () => {
  it("links a phone to Settings → Connections, and keeps the secondary action", () => {
    viewport.phone = true;
    const onclick = vi.fn();
    render(NotConnected, { title: "T", secondary: { label: "Add an asset by hand", onclick } });
    expect(screen.getByRole("link", { name: "Connect a bank" })).toHaveAttribute("href", "#setup/connections");
    expect(screen.getByRole("button", { name: "Add an asset by hand" })).toBeInTheDocument();
    expect(screen.queryByText(/Open Runway on a computer/)).not.toBeInTheDocument();
  });
});

describe("splitting a transaction", () => {
  const t = tx({ amount: -30, category: "Coffee" });

  it("is the editor on a phone too, and Cancel closes it", async () => {
    viewport.phone = true;
    const onclose = vi.fn();
    render(SplitEditor, { t, onclose, onsaved: vi.fn() });
    expect(screen.getByRole("button", { name: "Save split" })).toBeInTheDocument();
    expect(screen.queryByTestId("desktop-only")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onclose).toHaveBeenCalledOnce();
  });

  it("is the editor on a computer", () => {
    render(SplitEditor, { t, onclose: vi.fn(), onsaved: vi.fn() });
    expect(screen.getByRole("button", { name: "Save split" })).toBeInTheDocument();
  });
});

describe("an order's items", () => {
  it("load on a phone", async () => {
    vi.mocked(api).mockResolvedValue({ id: "x", retailer: "target", channel: "store", order_number: "1", placed: "2026-09-26", total: 10,
      items: [], charges: [], url: "https://www.target.com/orders" } as never);
    viewport.phone = true;
    render(OrderDetail, { orderId: "x" });
    expect(await screen.findByRole("link", { name: "Open on target.com" })).toBeInTheDocument();
    expect(api).toHaveBeenCalledTimes(1);
    expect(api).toHaveBeenCalledWith("/api/retail/orders/x", { keep: true });
    expect(screen.queryByTestId("desktop-only")).not.toBeInTheDocument();
  });
});

describe("a recurring item's fields", () => {
  const v = () => ({ name: "Rent", account_id: "a1", amount: -1200, amount_mode: "fixed", frequency: "monthly", dates: "", anchor_date: "2026-10-01", match: "", amount_min: "", amount_max: "", end_date: "", category: "" });
  const accounts = [{ id: "a1", name: "Checking", kind: "checking" }] as never;

  it("have More options on a phone", () => {
    viewport.phone = true;
    render(RecurringFields, { v: v(), accounts });
    expect(screen.getByRole("textbox", { name: /Name/ })).toHaveValue("Rent");
    expect(screen.getByText(/More options/)).toBeInTheDocument();
    expect(screen.getByLabelText("Merchant text")).toBeInTheDocument();
    expect(screen.queryByTestId("desktop-only")).not.toBeInTheDocument();
  });
});

describe("a budget's row", () => {
  const c: BudgetCategory = { name: "Groceries", parent: null, path: ["Groceries"], depth: 0, top: "Groceries", has_children: false, budget: 500, pay_with: null,
    usual_account: null, spent: 200, own_spent: 200, left: 300, rollover_from: null } as BudgetCategory;
  const show = () => render(BudgetRow, { c, month: "2026-03", pace: 0.5, budgets: true, payAccounts: [], onsave: vi.fn(), onchanged: vi.fn() });

  it("edits in place on a phone: the amount and rolling over, from the row's ⋯ menu", async () => {
    viewport.phone = true;
    show();
    expect(screen.getByLabelText("Budget for Groceries")).toHaveValue("500");
    await userEvent.click(screen.getByRole("button", { name: "Actions for Groceries" }));
    expect(screen.getByRole("button", { name: /Roll over/ })).toBeInTheDocument();
    expect(screen.getByText("$300 left")).toBeInTheDocument();
  });
});

describe("churning benefits with none yet", () => {
  it("are one muted line, with no computer note (the Churning page says that once)", () => {
    viewport.phone = true;
    render(Benefits, { cards: [], showOwner: false, onchanged: vi.fn() });
    expect(screen.getByText(/^none yet\./)).toBeInTheDocument();
    expect(screen.queryByText(/Open Runway on a computer/)).not.toBeInTheDocument();
  });

  it("are one muted line on a computer screen too, saying only where benefits come from", () => {
    render(Benefits, { cards: [], showOwner: false, onchanged: vi.fn() });
    expect(screen.getByText(/^none yet\./)).toBeInTheDocument();
    expect(screen.queryByText(/Edit a card and add its lounge access/)).not.toBeInTheDocument();
  });
});

describe("the More sheet", () => {
  beforeEach(() => { app.state = { connected: true, last_sync_ok: new Date(Date.now() - 36e5).toISOString() } as AppState; route.page = "overview"; route.sub = ""; });

  it("only lists pages a phone can use: Recurring, Net worth, Churning and Settings", async () => {
    render(MobileNav);
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    const sheet = screen.getByRole("dialog", { name: "More pages" });
    expect(Array.from(sheet.querySelectorAll("nav a")).map((a) => a.textContent!.trim())).toEqual(["Recurring", "Net worth", "Churning", "Settings"]);
  });
});
