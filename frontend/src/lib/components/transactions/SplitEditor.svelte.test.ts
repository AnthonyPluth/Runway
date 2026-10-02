// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn().mockResolvedValue({}), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { categories } from "$lib/categories.svelte";
import { toast } from "svelte-sonner";
import { category, tx } from "../../../test/fixtures";
import SplitEditor from "./SplitEditor.svelte";
import type { Tx } from "./types";

const setup = (t: Tx = tx({ amount: -30, category: "Coffee" })) => {
  const cbs = { onclose: vi.fn(), onsaved: vi.fn() };
  render(SplitEditor, { t, ...cbs });
  return cbs;
};
const pick = (part: number, name: string) => userEvent.selectOptions(screen.getByRole("combobox", { name: `Category of part ${part}` }), name);
const amount = (part: number) => screen.getByLabelText(`Amount of part ${part}`);

beforeEach(() => {
  vi.mocked(api).mockClear(); vi.mocked(toast.error).mockClear();
  categories.list = [category("Coffee"), category("Groceries")];
});

describe("SplitEditor", () => {
  it("starts with the whole amount on the current category and an empty second part", () => {
    setup();
    expect(screen.getByText("Split $30.00")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Category of part 1" })).toHaveValue("Coffee");
    expect(amount(1)).toHaveValue("30.00");
    expect(amount(2)).toHaveValue("");
    expect(screen.getByText("adds up")).toBeInTheDocument();
  });

  it("shows how much is left as you type, and when the parts go over", async () => {
    setup();
    await userEvent.clear(amount(1));
    await userEvent.type(amount(1), "12.5");
    expect(screen.getByText("$17.50 left to assign")).toBeInTheDocument();
    await userEvent.type(amount(2), "20");
    expect(screen.getByText("$2.50 over")).toHaveClass("text-destructive");
  });

  it("adds a part prefilled with what's left, and removes one", async () => {
    setup();
    await userEvent.clear(amount(1));
    await userEvent.type(amount(1), "10");
    await userEvent.click(screen.getByRole("button", { name: "+ Add a part" }));
    expect(amount(3)).toHaveValue("20.00");
    await userEvent.click(screen.getAllByRole("button", { name: "Remove this part" })[2]);
    expect(screen.queryByLabelText("Amount of part 3")).not.toBeInTheDocument();
  });

  it("won't save a part without a category", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Save split" }));
    expect(toast.error).toHaveBeenCalledWith("Give every part a category");
    expect(api).not.toHaveBeenCalled();
  });

  it("won't save a part without an amount", async () => {
    setup();
    await pick(2, "Groceries");
    await userEvent.click(screen.getByRole("button", { name: "Save split" }));
    expect(toast.error).toHaveBeenCalledWith("Give every part an amount");
  });

  it("saves the parts with the transaction's own sign (a charge stays negative)", async () => {
    const { onsaved } = setup();
    await userEvent.clear(amount(1));
    await userEvent.type(amount(1), "20");
    await pick(2, "Groceries");
    await userEvent.type(amount(2), "10");
    await userEvent.type(screen.getByRole("textbox", { name: "Note for part 2" }), "bread");
    await userEvent.click(screen.getByRole("button", { name: "Save split" }));
    expect(api).toHaveBeenCalledWith("/api/transactions/t1/split", { method: "POST", body: { splits: [
      { category: "Coffee", amount: -20, note: "" }, { category: "Groceries", amount: -10, note: "bread" }] } });
    expect(onsaved).toHaveBeenCalled();
  });

  it("keeps a deposit's parts positive", async () => {
    setup(tx({ amount: 100, category: "Coffee" }));
    await pick(2, "Groceries");
    await userEvent.clear(amount(1));
    await userEvent.type(amount(1), "60");
    await userEvent.type(amount(2), "40");
    await userEvent.click(screen.getByRole("button", { name: "Save split" }));
    expect(vi.mocked(api).mock.calls[0][1]).toMatchObject({ body: { splits: [{ amount: 60 }, { amount: 40 }] } });
  });

  it("loads an existing split and can remove it", async () => {
    const { onsaved } = setup(tx({ amount: -30, is_split: 1, splits: [{ category: "Coffee", amount: -10, note: "x" }, { category: "Groceries", amount: -20 }] }));
    expect(amount(1)).toHaveValue("10.00");
    expect(screen.getByRole("combobox", { name: "Category of part 2" })).toHaveValue("Groceries");
    await userEvent.click(screen.getByRole("button", { name: "Remove split" }));
    expect(api).toHaveBeenCalledWith("/api/transactions/t1/split", { method: "POST", body: { splits: [] } });
    expect(onsaved).toHaveBeenCalled();
  });

  it("shows the server's error and stays open when saving fails", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("Doesn't add up"));
    const { onsaved } = setup();
    await pick(2, "Groceries");
    await userEvent.type(amount(2), "5");
    await userEvent.click(screen.getByRole("button", { name: "Save split" }));
    expect(toast.error).toHaveBeenCalledWith("Doesn't add up");
    expect(onsaved).not.toHaveBeenCalled();
  });

  it("closes on Cancel", async () => {
    const { onclose } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onclose).toHaveBeenCalled();
  });
});
