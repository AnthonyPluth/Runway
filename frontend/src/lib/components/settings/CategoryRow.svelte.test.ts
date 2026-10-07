// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: null, version: 0 }, reload: vi.fn(), refreshState: vi.fn(async () => {}) }));

import { api } from "$lib/api";
import { categories } from "$lib/categories.svelte";
import { toast } from "svelte-sonner";
import { category } from "../../../test/fixtures";
import CategoryRow from "./CategoryRow.svelte";

const calls = (path: string) => vi.mocked(api).mock.calls.filter((c) => c[0] === path).map((c) => (c[1] as { body?: unknown }).body);
const removeButton = () => screen.getByRole("button", { name: "Remove Pharmacy" });

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({ ok: true, moved: 0 } as never);
  vi.mocked(toast).mockClear(); vi.mocked(toast.success).mockClear(); vi.mocked(toast.error).mockClear();
  categories.list = [category("Medical"), category("Pharmacy", { parent: "Medical", path: ["Medical", "Pharmacy"], depth: 1, top: "Medical" })];
});

describe("removing a category", () => {
  it("removes one nothing uses straight away, and Undo adds it back where it was, with its emoji", async () => {
    render(CategoryRow, { c: { ...categories.list[1], custom_icon: "💊", custom_color: null, transactions: 0, rules: 0, budgeted: false, items: 0 } });
    await userEvent.click(removeButton());
    await waitFor(() => expect(calls("/api/categories/remove")).toEqual([{ name: "Pharmacy", move_to: null }]));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Removed Pharmacy", expect.objectContaining({ action: expect.objectContaining({ label: "Undo" }) })));
    const { action } = vi.mocked(toast).mock.calls.at(-1)![1] as unknown as { action: { onClick: () => Promise<void> } };
    await action.onClick();
    expect(calls("/api/categories")).toEqual([{ name: "Pharmacy", parent: "Medical", is_transfer: false, is_income: false }]);
    expect(calls("/api/categories/look")).toEqual([{ name: "Pharmacy", icon: "💊", color: "" }]);
    expect(toast).toHaveBeenCalledWith("Undone", undefined);
  });

  it("shows the error, and offers no Undo, when the server won't remove it", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Category not found"));
    render(CategoryRow, { c: { ...categories.list[1], transactions: 0 } });
    await userEvent.click(removeButton());
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Category not found"));
    expect(toast).not.toHaveBeenCalled();
  });

  it("asks first when it's in use, saying what happens to its transactions, rules, budget and order items", async () => {
    render(CategoryRow, { c: { ...categories.list[1], transactions: 3, rules: 2, budgeted: true, items: 1 } });
    await userEvent.click(removeButton());
    const dialog = await screen.findByRole("dialog", { name: "Remove Pharmacy?" });
    expect(dialog).toHaveTextContent("Its 3 transactions:");
    expect(within(dialog).getByRole("combobox")).toHaveDisplayValue("Go to Review, uncategorized");
    expect(dialog).toHaveTextContent("2 rules stop setting it");
    expect(dialog).toHaveTextContent("Its budget is deleted.");
    expect(dialog).toHaveTextContent("1 order item will be categorized again automatically.");
    expect(calls("/api/categories/remove")).toHaveLength(0);

    await userEvent.selectOptions(within(dialog).getByRole("combobox"), "Medical");
    expect(dialog).toHaveTextContent("2 rules will set Medical instead.");
    vi.mocked(api).mockResolvedValue({ ok: true, moved: 3 } as never);
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(calls("/api/categories/remove")).toEqual([{ name: "Pharmacy", move_to: "Medical" }]));
    expect(toast.success).toHaveBeenCalledWith("Removed Pharmacy · 3 transactions moved to Medical");
    expect(toast).not.toHaveBeenCalled();
  });

  it("changes nothing when you cancel", async () => {
    render(CategoryRow, { c: { ...categories.list[1], transactions: 0, budgeted: true } });
    await userEvent.click(removeButton());
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(calls("/api/categories/remove")).toHaveLength(0);
  });

  it("can't remove a category that still has subcategories, or a built-in one", async () => {
    const { unmount } = render(CategoryRow, { c: { ...categories.list[0], has_children: true } });
    expect(screen.getByRole("button", { name: "Remove Medical" })).toBeDisabled();
    unmount();
    render(CategoryRow, { c: category("Transfer", { protected: true, is_transfer: true }) });
    expect(screen.queryByRole("button", { name: /^Remove/ })).not.toBeInTheDocument();
    expect(screen.getByText("built-in")).toBeInTheDocument();
  });
});
