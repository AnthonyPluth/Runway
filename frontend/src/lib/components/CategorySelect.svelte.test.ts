// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { categories } from "$lib/categories.svelte";
import type { Category } from "$lib/types";
import CatIcon from "./CatIcon.svelte";
import CategorySelect from "./CategorySelect.svelte";

const cat = (name: string, extra: Partial<Category> = {}): Category => ({ name, path: [name], depth: 0, top: name, ...extra });
beforeEach(() => {
  categories.list = [
    cat("Travel", { icon: "✈️", color: "#3987e5" }),
    cat("Transit", { parent: "Travel", path: ["Travel", "Transit"], depth: 1, top: "Travel" }),
    cat("Salary", { is_income: true }),
    cat("Transfer", { is_transfer: true }),
  ];
});

describe("CategorySelect", () => {
  it("groups the categories as Spending, Money in and Not spending", () => {
    render(CategorySelect, { label: "Category for X" });
    const select = screen.getByRole("combobox", { name: "Category for X" });
    const groups = within(select).getAllByRole("group").map((g) => g.getAttribute("label"));
    expect(groups).toEqual(["Spending", "Money in", "Not spending"]);
  });

  it("writes a subcategory as its full path so same-named ones can be told apart", () => {
    render(CategorySelect);
    expect(screen.getByRole("option", { name: /Travel > Transit/ })).toBeInTheDocument();
  });

  it("offers a blank first option unless it's turned off", () => {
    const { unmount } = render(CategorySelect, { blank: "Pick one" });
    expect(screen.getAllByRole("option")[0]).toHaveTextContent("Pick one");
    unmount();
    render(CategorySelect, { blank: false });
    expect(screen.queryByRole("option", { name: "Choose…" })).not.toBeInTheDocument();
  });

  it("reports the picked category", async () => {
    const onchange = vi.fn();
    render(CategorySelect, { onchange });
    await userEvent.selectOptions(screen.getByRole("combobox"), "Salary");
    expect(onchange).toHaveBeenCalledWith("Salary");
  });

  it("only offers categories that can still take children when choosing a parent", () => {
    render(CategorySelect, { canHoldChildren: true });
    expect(screen.queryByRole("option", { name: /Transit/ })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Travel/ })).toBeInTheDocument();
  });

  it("can leave categories out", () => {
    render(CategorySelect, { exclude: (c) => c.name === "Salary" });
    expect(screen.queryByRole("option", { name: /Salary/ })).not.toBeInTheDocument();
  });

  it("in short form shows just the category's own name over the select", () => {
    render(CategorySelect, { short: true, value: "Transit" });
    // the drawn label is aria-hidden; the select keeps the full option for screen readers
    expect(document.querySelector("[aria-hidden=true].truncate")).toHaveTextContent("Transit");
    expect(screen.getByRole("combobox")).toHaveValue("Transit");
  });

  it("in short form shows the blank text while nothing is chosen", () => {
    render(CategorySelect, { short: true, value: "", blank: "Choose…" });
    expect(document.querySelector("[aria-hidden=true].truncate")).toHaveTextContent("Choose…");
  });

  it("can be disabled", () => {
    render(CategorySelect, { disabled: true });
    expect(screen.getByRole("combobox")).toBeDisabled();
  });
});

describe("CatIcon", () => {
  it("draws the category's emoji", () => {
    const { container } = render(CatIcon, { name: "Travel", size: 30 });
    expect(container.querySelector("span")).toHaveTextContent("✈️");
    expect(container.querySelector("span")).toHaveStyle({ width: "30px", height: "30px" });
  });

  it("falls back to a tag for a category that was removed", () => {
    const { container } = render(CatIcon, { name: "Gone" });
    expect(container.querySelector("span")).toHaveTextContent("🏷️");
  });

  it("shows the emoji with nothing behind it", () => {
    const { container } = render(CatIcon, { name: "Travel" });
    const style = container.querySelector("span")!.getAttribute("style")!;
    expect(style).not.toContain("background");
    expect(style).toContain("width: 24px");
  });
});
