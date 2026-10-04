// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({
  app: { state: { owners: [], primary_account: null, horizon_days: 90 }, version: 0 },
  reload: vi.fn(), refreshState: vi.fn(),
}));

import { api } from "$lib/api";
import { reload } from "$lib/app.svelte";
import { toast } from "svelte-sonner";
import AccountsSection from "./AccountsSection.svelte";
import PlaidItemRow from "./PlaidItemRow.svelte";
import type { PlaidItem } from "./types";
import { acct, card, ignored, item, matchCall, matched, serve, status, unmatched } from "../../../test/plaidSettings";

beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(reload).mockClear(); vi.mocked(toast.success).mockClear(); });

describe("Settings → Accounts: hidden accounts", () => {
  it("collapses them into a counted line that expands, leaving the others shown", async () => {
    serve(status([]));
    render(AccountsSection, { accounts: [acct(), acct({ id: "old1", name: "Old checking", hidden: true }), acct({ id: "old2", name: "Old savings", kind: "savings", hidden: true })] });
    const section = await screen.findByRole("region", { name: "Hidden accounts" });
    expect(section).toHaveTextContent("2 hidden accounts");
    expect(screen.getByText("Checking")).toBeInTheDocument();
    expect(screen.queryByText("Old checking")).toBeNull();
    await userEvent.click(within(section).getByRole("button", { name: "Show" }));
    expect(within(section).getByText("Old checking")).toBeInTheDocument();
    expect(within(section).getByText("Old savings")).toBeInTheDocument();
    await userEvent.click(within(section).getByRole("button", { name: "Hide" }));
    expect(screen.queryByText("Old checking")).toBeNull();
    expect(section).toHaveTextContent("Left out of lists, totals and the forecast; their transactions are kept.");
  });

  it("moves an account you hide into them without reloading, and back with Undo", async () => {
    serve(status([]));
    render(AccountsSection, { accounts: [acct(), acct({ id: "sav", name: "Savings", kind: "savings" }), acct({ id: "old1", name: "Old checking", hidden: true })] });
    expect(await screen.findByRole("region", { name: "Hidden accounts" })).toHaveTextContent("1 hidden account");
    const row = screen.getByText("Savings").closest("details")!;
    await userEvent.click(within(row).getByRole("button", { name: "Hide" }));
    await waitFor(() => expect(screen.getByRole("region", { name: "Hidden accounts" })).toHaveTextContent("2 hidden accounts"));
    expect(screen.queryByText("Savings")).toBeNull();
    expect(screen.getByText("Checking")).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
    const undo = vi.mocked(toast).mock.calls.findLast(([m]) => m === "Savings hidden")![1] as unknown as { action: { onClick: () => Promise<void> } };
    await undo.action.onClick();
    await waitFor(() => expect(screen.getByText("Savings")).toBeInTheDocument());
    expect(screen.getByRole("region", { name: "Hidden accounts" })).toHaveTextContent("1 hidden account");
  });

  it("shows no hidden line when none are hidden", async () => {
    serve(status([]));
    render(AccountsSection, { accounts: [acct()] });
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/accounts/deleted"));
    expect(screen.queryByRole("region", { name: "Hidden accounts" })).toBeNull();
  });
});

describe("Settings → Accounts: deleted accounts", () => {
  it("counts them, and restores one, saying the next sync brings it back", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/api/accounts/deleted") return [{ id: "A1", name: "Old Visa" }, { id: "A2", name: "Old Savings" }];
      if (path === "/api/plaid/status") return status([]);
      return { ok: true };
    });
    render(AccountsSection, { accounts: [acct()] });
    const section = await screen.findByRole("region", { name: "Deleted accounts" });
    expect(section).toHaveTextContent("2 deleted accounts");
    await userEvent.click(within(section).getByRole("button", { name: "Show" }));
    expect(within(section).getByRole("list")).toHaveAttribute("title", expect.stringContaining("lets the next sync bring it back, with whatever history the bank still offers"));
    await userEvent.click(within(within(section).getByText("Old Visa").closest("li")!).getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/accounts/A1/restore", { method: "POST" }));
    expect(toast.success).toHaveBeenCalledWith("Old Visa comes back with the next sync");
    expect(reload).toHaveBeenCalled();
  });

  it("shows nothing when none were deleted", async () => {
    serve(status([]));
    render(AccountsSection, { accounts: [acct()] });
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/accounts/deleted"));
    expect(screen.queryByRole("region", { name: "Deleted accounts" })).toBeNull();
  });
});

describe("Settings → Accounts: New from Plaid", () => {
  it("lists Plaid accounts nobody has decided about at the top, with the three choices", async () => {
    serve(status([item([unmatched, matched])]));
    render(AccountsSection, { accounts: [acct({ plaid_account_id: "pa2" }), card] });
    const group = await screen.findByRole("region", { name: "New from Plaid" });
    expect(within(group).getByText("Freedom")).toBeInTheDocument();
    expect(within(group).queryByText("Total Checking")).toBeNull();
    const select = within(group).getByRole("combobox");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Add as its own account", "Same as Amex Gold", "Don't use"]);
    expect(select).toHaveValue("new");
    expect(api).not.toHaveBeenCalledWith("/api/plaid/match", expect.anything());
    expect(within(group).getByRole("button", { name: "Add Freedom" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Add all/ })).toBeNull();
  });

  it("adds them all at once", async () => {
    serve(status([item([unmatched, { ...unmatched, id: "pa9", name: "Sapphire", mask: "0009" }])]));
    render(AccountsSection, { accounts: [acct()] });
    await userEvent.click(await screen.findByRole("button", { name: "Add all 2" }));
    await waitFor(() => expect(vi.mocked(api).mock.calls.filter(([p]) => p === "/api/plaid/match").map(([, o]) => (o as { body: unknown }).body))
      .toEqual([{ plaid_account_id: "pa1", target: "new" }, { plaid_account_id: "pa9", target: "new" }]));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Added 2 accounts"));
    expect(reload).toHaveBeenCalledOnce();
  });

  it("has no group when there's nothing to decide, or no Plaid", async () => {
    serve(status([item([matched])]));
    const { unmount } = render(AccountsSection, { accounts: [acct()] });
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/plaid/status"));
    expect(screen.queryByRole("region", { name: "New from Plaid" })).toBeNull();
    unmount();
    serve(new Error("nope"));
    render(AccountsSection, { accounts: [acct()] });
    expect(screen.queryByRole("region", { name: "New from Plaid" })).toBeNull();
  });

  it("adds one as its own account through POST /api/plaid/match, and says so", async () => {
    serve(status([item([unmatched])]));
    render(AccountsSection, { accounts: [acct()] });
    const group = await screen.findByRole("region", { name: "New from Plaid" });
    await userEvent.click(within(group).getByRole("button", { name: "Add Freedom" }));
    await waitFor(() => expect(matchCall()).toBeTruthy());
    expect(matchCall()![1]).toEqual({ method: "POST", body: { plaid_account_id: "pa1", target: "new" } });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Added to your accounts"));
    expect(reload).toHaveBeenCalled();
  });

  it("matches one to an account you already have, or leaves it out", async () => {
    serve(status([item([unmatched])]));
    render(AccountsSection, { accounts: [acct(), card] });
    const group = await screen.findByRole("region", { name: "New from Plaid" });
    await userEvent.selectOptions(within(group).getByRole("combobox"), "amex");
    await waitFor(() => expect(matchCall()![1]).toMatchObject({ body: { plaid_account_id: "pa1", target: "amex" } }));
    expect(toast.success).toHaveBeenCalledWith("Matched. Choose where its data comes from under Accounts.");
    vi.mocked(api).mockClear();
    await userEvent.selectOptions(within(group).getByRole("combobox"), "ignore");
    await waitFor(() => expect(matchCall()![1]).toMatchObject({ body: { plaid_account_id: "pa1", target: "ignore" } }));
    expect(toast.success).toHaveBeenCalledWith("Left out");
  });

  it("keeps the ones you left out reachable, and no longer warns about investments elsewhere", async () => {
    serve(status([item([unmatched, ignored])]));
    render(AccountsSection, { accounts: [acct()] });
    expect(await screen.findByText(/1 Plaid account you're not using/)).toBeInTheDocument();
    expect(screen.queryByText("Old Savings")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(screen.getByText("Old Savings")).toBeInTheDocument();
    expect(screen.queryByText(/investment account/)).toBeNull();
    expect(screen.queryByRole("link", { name: "Connections" })).toBeNull();
  });
});

describe("Settings → Connections: a Plaid connection", () => {
  const items = (it: PlaidItem) => render(PlaidItemRow, { it, items: [it] });

  it("has no per-account selects, just a count and a way to Accounts", () => {
    items(item([unmatched, matched, ignored]));
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByText(/3 accounts/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "1 needs a decision →" })).toHaveAttribute("href", "#setup/accounts");
    expect(screen.getByRole("button", { name: "Sync" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
  });

  it("points at Accounts when everything is decided", () => {
    items(item([matched]));
    expect(screen.getByText(/1 account/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Manage in Accounts" })).toHaveAttribute("href", "#setup/accounts");
  });

  it("has no per-account selects on an investment connection either", () => {
    const iv = { id: "iv1", name: "Roth IRA", subtype: "ira", mask: "0001", balance: 5000, account_id: null };
    items(item([iv, { ...iv, id: "iv2", account_id: "ignore" }, { ...iv, id: "iv3", account_id: "roth" }],
      { bank: false, products: ["investments"], candidates: [{ id: "roth", name: "Roth", balance: 5000, linked_to: null }] }));
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByText(/3 accounts/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "1 needs a decision →" })).toHaveAttribute("href", "#setup/accounts");
    expect(screen.getByRole("button", { name: "Sync" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
  });

  it("says when an investment connection has no accounts yet", () => {
    items(item([], { bank: false, products: ["investments"] }));
    expect(screen.getByText("no accounts yet")).toBeInTheDocument();
  });
});
