// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: {}, version: 0 }, reload: vi.fn(), refreshState: vi.fn() }));
vi.mock("./plaid.svelte", () => ({ openPlaidLink: vi.fn() }));

import { api } from "$lib/api";
import { reload } from "$lib/app.svelte";
import { toast } from "svelte-sonner";
import PlaidItemRow from "./PlaidItemRow.svelte";
import type { PlaidItem } from "./types";

const item = (over: Partial<PlaidItem> = {}): PlaidItem => ({
  item_id: "it1", institution_name: "Chase", products: ["transactions"], bank: true, last_sync: "2026-09-30 15:00:00",
  accounts: [{ id: "pa1", name: "Checking", type: "depository", account_id: "sf-chk" },
             { id: "pa2", name: "Freedom", type: "credit", account_id: "pl:pa2" },
             { id: "pa3", name: "Savings", type: "depository", account_id: "sf-sav" }], ...over });
const row = (it: PlaidItem) => render(PlaidItemRow, { it, items: [it] });

beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(reload).mockClear(); vi.mocked(toast.error).mockClear(); });

describe("a Plaid connection's problem", () => {
  it("offers Reconnect when the bank wants you to sign in again", async () => {
    row(item({ error: "PENDING_EXPIRATION" }));
    expect(screen.getByText("Your bank’s permission expires soon, reconnect to keep syncing")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reconnect" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sync" })).not.toBeInTheDocument();
  });

  it("keeps Sync for a bank that's down, and says so in words", async () => {
    row(item({ error: "INSTITUTION_DOWN" }));
    expect(screen.getByText("The bank isn’t answering right now; Runway will try again")).toBeInTheDocument();
    expect(screen.getByText(/last successful sync Sep 30/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sync" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reconnect" })).not.toBeInTheDocument();
    expect(screen.queryByText("INSTITUTION_DOWN")).not.toBeInTheDocument();
    expect(screen.queryByText("Details")).not.toBeInTheDocument();
  });

  it("says an error it has no words for plainly, with Plaid's own behind Details, in the warning tone", () => {
    row(item({ error: "WEIRD_NEW_CODE" }));
    expect(screen.getByText("Couldn’t sync; Runway will try again")).toHaveClass("text-warning");
    const raw = screen.getByText("WEIRD_NEW_CODE");
    expect(raw.closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText(/last successful sync Sep 30/)).toBeInTheDocument();
  });
});

describe("syncing a Plaid connection", () => {
  it("counts what came in, in the singular for one", async () => {
    vi.mocked(api).mockResolvedValue({ bank: true, new_transactions: 1, statements: 1 });
    const { unmount } = row(item());
    await userEvent.click(screen.getByRole("button", { name: "Sync" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Synced · 1 new transaction · 1 statement"));
    unmount();
    vi.mocked(api).mockResolvedValue({ bank: true, new_transactions: 3, statements: 0 });
    row(item());
    await userEvent.click(screen.getByRole("button", { name: "Sync" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Synced · 3 new transactions · 0 statements"));
  });
});

describe("removing a Plaid connection", () => {
  it("says what happens to a bank connection's accounts before removing it", async () => {
    vi.mocked(api).mockResolvedValue({ ok: true });
    row(item());
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Remove" }));
    const dialog = await screen.findByRole("dialog", { name: "Remove Chase?" });
    expect(dialog).toHaveTextContent("Revokes Runway’s access at Plaid. You’d have to connect it again from scratch.");
    expect(dialog).toHaveTextContent("2 accounts that also come through SimpleFIN keep syncing there.");
    expect(dialog).toHaveTextContent("1 account only Plaid had keeps its history but stops updating.");
    expect(dialog).not.toHaveTextContent("investment");
    expect(within(dialog).getByRole("link", { name: "Download a backup first" })).toHaveAttribute("href", "#setup/advanced");
    expect(api).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(api).toHaveBeenCalledWith("/api/plaid/items/it1/remove", { method: "POST" });
    expect(reload).toHaveBeenCalled();
  });

  it("counts the investment accounts it deletes, and stays open when removing fails", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Plaid didn’t answer"));
    row(item({ institution_name: "Fidelity", bank: false, products: ["investments"],
      accounts: [{ id: "i1", name: "Brokerage", account_id: "pl:i1" }, { id: "i2", name: "IRA", account_id: "ignore" }] }));
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Remove" }));
    const dialog = await screen.findByRole("dialog", { name: "Remove Fidelity?" });
    expect(dialog).toHaveTextContent("Deletes 2 investment accounts with their holdings and activity.");
    expect(dialog).not.toHaveTextContent("SimpleFIN");
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Plaid didn’t answer"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("refreshing a Plaid connection early", () => {
  it("shows the switch as it stands, and turns it off", async () => {
    vi.mocked(api).mockResolvedValue({ refresh: false, refresh_paused: null, refresh_paused_at: null });
    row(item({ refresh: true }));
    const box = screen.getByRole("checkbox", { name: "Refresh transactions early" });
    expect(box).toBeChecked();
    await userEvent.click(box);
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/plaid/items/it1/refresh", { method: "POST", body: { on: false } }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Waits for the daily sync from now on"));
    expect(box).not.toBeChecked();
  });

  it("updates in place: the page isn't redrawn and the row's elements stay the same ones", async () => {
    let finish: (v: unknown) => void = () => {};
    vi.mocked(api).mockReturnValue(new Promise((res) => { finish = res; }));
    const it0 = item({ refresh: true, refresh_paused: "Plaid: too many refreshes", refresh_paused_at: "2026-09-30 15:00:00" });
    const { container } = row(it0);
    const box = screen.getByRole("checkbox", { name: "Refresh transactions early" });
    const root = container.firstElementChild!;
    const name = screen.getByText("Chase");
    const seen: MutationRecord[] = [];
    const mo = new MutationObserver((m) => seen.push(...m));
    mo.observe(root, { childList: true, subtree: true });
    await userEvent.click(box);
    expect(box).toBeDisabled();   // saving: the same switch, only disabled
    finish({ refresh: false, refresh_paused: null, refresh_paused_at: null });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Waits for the daily sync from now on"));
    await waitFor(() => expect(box).not.toBeDisabled());
    await Promise.resolve();
    mo.disconnect();
    expect(reload).not.toHaveBeenCalled();
    expect(screen.getByRole("checkbox", { name: "Refresh transactions early" })).toBe(box);
    expect(screen.getByText("Chase")).toBe(name);
    expect(container.firstElementChild).toBe(root);
    expect(box).not.toBeChecked();
    expect(screen.queryByText(/Refresh paused/)).not.toBeInTheDocument();   // the pause ended with the reply
    // Only the pause note came out; nothing else was added or removed.
    expect(seen.flatMap((m) => [...m.addedNodes])).toHaveLength(0);
    expect(seen.flatMap((m) => [...m.removedNodes]).every((n) => (n as Element).textContent?.includes("Refresh paused") || n.nodeType === 3)).toBe(true);
  });

  it("shows a pause that arrives with fresher data after a toggle", async () => {
    vi.mocked(api).mockResolvedValue({ refresh: true, refresh_paused: null, refresh_paused_at: null });
    const it0 = item({ refresh: false });
    const r = row(it0);
    await userEvent.click(screen.getByRole("checkbox", { name: "Refresh transactions early" }));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Refresh transactions early" })).not.toBeDisabled());
    await r.rerender({ it: item({ refresh: true, refresh_paused: "Plaid: too many refreshes", refresh_paused_at: "2026-09-30 15:00:00" }), items: [it0] });
    expect(await screen.findByText(/^Refresh paused: Plaid: too many refreshes/)).toBeInTheDocument();
  });

  it("puts the switch back when saving fails", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Connection not found"));
    row(item({ refresh: false }));
    const box = screen.getByRole("checkbox", { name: "Refresh transactions early" });
    await userEvent.click(box);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Connection not found"));
    expect(box).not.toBeChecked();
    expect(reload).not.toHaveBeenCalled();
  });

  it("says why it paused, and turning it on again asks to try again", async () => {
    vi.mocked(api).mockResolvedValue({ refresh: true, refresh_paused: null, refresh_paused_at: null });
    row(item({ refresh: true, refresh_paused: "Plaid: too many refreshes (TRANSACTIONS_LIMIT)", refresh_paused_at: "2026-09-30 15:00:00" }));
    const note = screen.getByText(/^Refresh paused: Plaid: too many refreshes \(TRANSACTIONS_LIMIT\) · since Sep 30/);
    expect(note).toHaveClass("text-(--low)");
    const box = screen.getByRole("checkbox", { name: "Refresh transactions early" });
    await userEvent.click(box);   // off
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/plaid/items/it1/refresh", { method: "POST", body: { on: false } }));
    await userEvent.click(box);   // on again
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/plaid/items/it1/refresh", { method: "POST", body: { on: true } }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Refreshes early from now on"));
  });

  it("has no switch for a connection without transactions, and no note when it isn't paused", () => {
    const { unmount } = row(item({ products: ["liabilities"], refresh: true }));
    expect(screen.queryByRole("checkbox", { name: "Refresh transactions early" })).not.toBeInTheDocument();
    unmount();
    const inv = row(item({ bank: false, products: ["investments"], accounts: [] }));
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    inv.unmount();
    row(item({ refresh: false }));
    expect(screen.getByRole("checkbox", { name: "Refresh transactions early" })).not.toBeChecked();
    expect(screen.queryByText(/Refresh paused/)).not.toBeInTheDocument();
  });
});
