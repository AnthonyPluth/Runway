// @vitest-environment jsdom
import { fireEvent, render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("$lib/app.svelte", async (importOriginal) => ({ ...(await importOriginal<typeof import("$lib/app.svelte")>()), refreshState: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));

import { api } from "$lib/api";
import { refreshState } from "$lib/app.svelte";
import { toast } from "svelte-sonner";
import { viewport } from "$lib/phone.svelte";
import { tx } from "../../../test/fixtures";
import TxTable from "./TxTable.svelte";

const items = [
  tx({ id: "a", posted: "2026-03-10", amount: -10, payee: "Alpha" }),
  tx({ id: "b", posted: "2026-03-10", amount: -5, payee: "Bravo", source: "manual" }),
  tx({ id: "c", posted: "2026-03-09", amount: 100, payee: "Charlie", source: "manual" }),
];
const setup = (extra: Record<string, unknown> = {}) => {
  const p = { items, total: items.length, review: false, recurring: [], onsave: vi.fn(), onchanged: vi.fn(), ...extra };
  render(TxTable, p);
  return p;
};
const tick = (name: string) => screen.getByRole("checkbox", { name: `Select ${name}` });
const bar = () => screen.getByRole("region", { name: "Change the selected transactions" });
const deleteButton = (phone: boolean) => within(bar()).getByRole("button", { name: phone ? "Delete the selected transactions" : "Delete" });
const confirm = () => within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" });

beforeEach(() => {
  // Fake timers for the clock only, as in TxTable.svelte.test.ts: nothing here is debounced, but a save must not leak.
  vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-06-01T12:00:00") });
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({ ok: true, deleted: 2 });
});
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); viewport.phone = false; });

describe.each([false, true])("deleting the selected (phone: %s)", (phone) => {
  const open = (extra: Record<string, unknown> = {}) => { viewport.phone = phone; return setup({ selecting: phone, ...extra }); };

  it("has a Delete button only while something is selected", async () => {
    open();
    expect(screen.queryByRole("region", { name: "Change the selected transactions" })).not.toBeInTheDocument();
    await userEvent.click(tick("Alpha"));
    expect(deleteButton(phone)).toBeInTheDocument();
  });

  it("asks with the count, mentioning a later sync when a bank-synced one is selected, and deletes nothing on Cancel", async () => {
    const p = open();
    await userEvent.click(tick("Alpha"));
    await userEvent.click(tick("Bravo"));
    await userEvent.click(deleteButton(phone));
    const dialog = screen.getByRole("dialog", { name: "Delete 2 transactions?" });
    expect(dialog).toHaveTextContent("They leave reports, budgets and the list. This can’t be undone.");
    expect(dialog).toHaveTextContent("A later bank sync may bring some back.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(api).not.toHaveBeenCalled();
    expect(p.onchanged).not.toHaveBeenCalled();
    expect(screen.getByText("2 selected")).toBeInTheDocument();
  });

  it("speaks of one in the singular, and leaves the sync out when it is a manual one", async () => {
    open();
    await userEvent.click(tick("Bravo"));
    await userEvent.click(deleteButton(phone));
    const dialog = screen.getByRole("dialog", { name: "Delete 1 transaction?" });
    expect(dialog).toHaveTextContent("It leaves reports, budgets and the list. This can’t be undone.");
    expect(dialog).not.toHaveTextContent("bank sync");
  });

  it("leaves the sync out for several manual ones", async () => {
    open();
    await userEvent.click(tick("Bravo"));
    await userEvent.click(tick("Charlie"));
    await userEvent.click(deleteButton(phone));
    expect(screen.getByRole("dialog")).not.toHaveTextContent("bank sync");
  });

  it("deletes the ticked ids on confirm, says so, clears the selection and reloads", async () => {
    const p = open();
    await userEvent.click(tick("Alpha"));
    await userEvent.click(tick("Charlie"));
    await userEvent.click(deleteButton(phone));
    await userEvent.click(confirm());
    expect(api).toHaveBeenCalledWith("/api/transactions/bulk-delete", { method: "POST", body: { ids: ["a", "c"] } });
    expect(toast.success).toHaveBeenCalledWith("Deleted 2 transactions");
    expect(p.onchanged).toHaveBeenCalledOnce();
    expect(refreshState).toHaveBeenCalledOnce();
    expect(screen.queryByRole("region", { name: "Change the selected transactions" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(tick("Alpha")).not.toBeChecked();
  });

  it("keeps the selection and says why when it fails", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("Locked"));
    const p = open();
    await userEvent.click(tick("Alpha"));
    await userEvent.click(deleteButton(phone));
    await userEvent.click(confirm());
    expect(toast.error).toHaveBeenCalledWith("Locked");
    expect(toast.success).not.toHaveBeenCalled();
    expect(p.onchanged).not.toHaveBeenCalled();
    expect(refreshState).not.toHaveBeenCalled();
    expect(tick("Alpha")).toBeChecked();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  describe("Select all", () => {
    const every = { q: "coffee", ignored: "0" };
    const selectAll = async () => {
      await userEvent.click(screen.getByRole("checkbox", { name: "Select all shown" }));
      if (phone) {
        await userEvent.click(within(bar()).getByRole("button", { name: "More actions" }));
        await fireEvent.click(await screen.findByText("Select all 212", { selector: "[data-popover-content] button" }));
        await userEvent.keyboard("{Escape}");
      } else await userEvent.click(screen.getByRole("button", { name: "Select all 212" }));
    };

    it("counts every match, mentions a sync, and deletes by the filters", async () => {
      vi.mocked(api).mockResolvedValue({ ok: true, deleted: 212 });
      const p = open({ items: items.filter((t) => t.source === "manual"), total: 212, every, onmore: vi.fn() });
      await selectAll();
      await userEvent.click(deleteButton(phone));
      const dialog = screen.getByRole("dialog", { name: "Delete 212 transactions?" });
      expect(dialog).toHaveTextContent("A later bank sync may bring some back.");   // not all loaded: can't tell
      await userEvent.click(confirm());
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk-delete", { method: "POST", body: { filter: every } });
      expect(toast.success).toHaveBeenCalledWith("Deleted 212 transactions");
      expect(p.onchanged).toHaveBeenCalledOnce();
      expect(screen.queryByRole("region", { name: "Change the selected transactions" })).not.toBeInTheDocument();
    });

    it("keeps Select all N selected when it fails", async () => {
      vi.mocked(api).mockRejectedValueOnce(new Error("Locked"));
      open({ total: 212, every, onmore: vi.fn() });
      await selectAll();
      await userEvent.click(deleteButton(phone));
      await userEvent.click(confirm());
      expect(toast.error).toHaveBeenCalledWith("Locked");
      await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
      expect(bar()).toHaveTextContent("212 selected");
    });
  });
});
