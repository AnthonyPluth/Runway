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
    expect(screen.getByText(/synced Sep 30/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sync" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reconnect" })).not.toBeInTheDocument();
    expect(screen.queryByText("INSTITUTION_DOWN")).not.toBeInTheDocument();
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
