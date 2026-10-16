// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
vi.mock("$lib/api", () => ({ api: vi.fn() }));
vi.mock("$lib/app.svelte", () => ({ refreshState: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
import { api } from "$lib/api";
import { refreshState } from "$lib/app.svelte";
import { toast } from "svelte-sonner";
import { tx } from "../../../test/fixtures";
import TxDelete from "./TxDelete.svelte";

afterEach(() => vi.clearAllMocks());
describe("deleting transactions", () => {
  it.each([0, 1])("deletes a bank transaction after confirmation (needs_review: %s)", async (needs_review) => {
    const onchanged = vi.fn(), onclose = vi.fn();
    render(TxDelete, { t: tx({ id: "a|pl:x", needs_review }), compact: true, onchanged, onclose });
    await userEvent.click(screen.getByRole("button", { name: "Delete Blue Bottle" }));
    const dialog = screen.getByRole("dialog", { name: "Delete Blue Bottle?" });
    expect(api).not.toHaveBeenCalled();
    expect(dialog).toHaveTextContent("A later bank sync may bring it back.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(api).toHaveBeenCalledWith("/api/transactions/a%7Cpl%3Ax", { method: "DELETE" });
    expect(onchanged).toHaveBeenCalledOnce();
    expect(onclose).toHaveBeenCalledOnce();
    expect(refreshState).toHaveBeenCalledOnce();
  });
  it("cancels without deleting", async () => {
    render(TxDelete, { t: tx({ source: "manual" }), onchanged: vi.fn() });
    await userEvent.click(screen.getByRole("button", { name: "Delete transaction" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).not.toHaveTextContent("bank sync");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(api).not.toHaveBeenCalled();
  });
  it("keeps the dialog and transaction when deletion fails, and allows retry", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("Locked"));
    const onchanged = vi.fn();
    render(TxDelete, { t: tx(), onchanged });
    await userEvent.click(screen.getByRole("button", { name: "Delete transaction" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    expect(toast.error).toHaveBeenCalledWith("Locked");
    expect(onchanged).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    expect(onchanged).toHaveBeenCalledOnce();
  });
});
