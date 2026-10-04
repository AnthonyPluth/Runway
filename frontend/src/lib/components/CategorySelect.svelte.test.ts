// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { categories } from "$lib/categories.svelte";
import type { Category } from "$lib/types";
import CatIcon from "./CatIcon.svelte";
import CategorySelect from "./CategorySelect.svelte";
import { pickSections, recentCategories, rememberRecent } from "./categoryPicker";
import { viewport } from "$lib/phone.svelte";
import { pickCategory, pickedValue } from "../../test/pick";

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
  beforeEach(() => { localStorage.clear(); viewport.phone = false; });
  const open = async (name = "Category") => { await userEvent.click(screen.getByRole("combobox", { name })); return screen.getByRole("listbox"); };
  const optionNames = (list: HTMLElement) => within(list).getAllByRole("option").map((o) => o.textContent!.trim());

  it("is a light button until opened: no list on the page while it's closed", () => {
    render(CategorySelect, { value: "Salary" });
    expect(screen.getByRole("combobox", { name: "Category" })).toHaveTextContent("Salary");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.queryAllByRole("option")).toHaveLength(0);
  });

  it("groups the categories as Spending, Money in and Not spending", async () => {
    render(CategorySelect, { label: "Category for X" });
    const list = await open("Category for X");
    const groups = within(list).getAllByRole("group").map((g) => g.getAttribute("aria-labelledby") && document.getElementById(g.getAttribute("aria-labelledby")!)?.textContent);
    expect(groups.filter(Boolean)).toEqual(["Spending", "Money in", "Not spending"]);
  });

  it("writes a subcategory as its full path, its emoji after the name", async () => {
    render(CategorySelect);
    const list = await open();
    expect(within(list).getByRole("option", { name: /Travel > Transit/ })).toBeInTheDocument();
    expect(within(list).getByRole("option", { name: "Travel" })).toHaveTextContent("Travel ✈️");
  });

  it("offers a blank first option unless it's turned off", async () => {
    const { unmount } = render(CategorySelect, { blank: "Pick one" });
    expect(optionNames(await open())[0]).toBe("Pick one");
    unmount();
    render(CategorySelect, { blank: false });
    expect(optionNames(await open())).not.toContain("Choose…");
  });

  it("narrows the list to what's typed, by name or path, groups kept", async () => {
    render(CategorySelect);
    await open();
    await userEvent.keyboard("tra");
    const list = screen.getByRole("listbox");
    expect(optionNames(list)).toEqual(["Travel ✈️", "Travel > Transit", "Transfer"]);
    await userEvent.keyboard("nsi");
    expect(optionNames(list)).toEqual(["Travel > Transit"]);
    await userEvent.clear(screen.getByRole("combobox", { name: "Search categories" }));
    await userEvent.keyboard("zzz");
    expect(within(list).queryAllByRole("option")).toHaveLength(0);
    expect(list).toHaveTextContent("No category matches");
  });

  it("starts the search with a letter typed on the closed picker", async () => {
    render(CategorySelect);
    screen.getByRole("combobox", { name: "Category" }).focus();
    await userEvent.keyboard("s");
    expect(screen.getByRole("combobox", { name: "Search categories" })).toHaveValue("s");
    expect(screen.getByRole("combobox", { name: "Search categories" })).toHaveFocus();
  });

  it("reports the picked category and closes, focus back on the button", async () => {
    const onchange = vi.fn();
    render(CategorySelect, { onchange });
    await pickCategory(screen.getByRole("combobox", { name: "Category" }), "Salary");
    expect(onchange).toHaveBeenCalledWith("Salary");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Category" })).toHaveFocus();
    expect(pickedValue(screen.getByRole("combobox", { name: "Category" }))).toBe("Salary");
  });

  it("doesn't report picking the category it already has, unless asked to", async () => {
    const onchange = vi.fn();
    const { unmount } = render(CategorySelect, { value: "Salary", onchange });
    await pickCategory(screen.getByRole("combobox", { name: "Category" }), "Salary");
    expect(onchange).not.toHaveBeenCalled();
    unmount();
    render(CategorySelect, { value: "Salary", onchange, repick: true });
    await pickCategory(screen.getByRole("combobox", { name: "Category" }), "Salary");
    expect(onchange).toHaveBeenCalledWith("Salary");
  });

  it("moves with the arrows and picks with Enter", async () => {
    const onchange = vi.fn();
    render(CategorySelect, { onchange, blank: false });
    await open();
    await userEvent.keyboard("{ArrowDown}{ArrowDown}");
    const search = screen.getByRole("combobox", { name: "Search categories" });
    expect(document.getElementById(search.getAttribute("aria-activedescendant")!)).toHaveTextContent("Salary");
    await userEvent.keyboard("{Enter}");
    expect(onchange).toHaveBeenCalledWith("Salary");
  });

  it("picks the first match with Enter after typing", async () => {
    const onchange = vi.fn();
    render(CategorySelect, { onchange });
    await open();
    await userEvent.keyboard("sal{Enter}");
    expect(onchange).toHaveBeenCalledWith("Salary");
  });

  it("opens on the category it has, so Enter keeps it", async () => {
    render(CategorySelect, { value: "Salary" });
    await open();
    const search = screen.getByRole("combobox", { name: "Search categories" });
    await vi.waitFor(() => expect(document.getElementById(search.getAttribute("aria-activedescendant")!)).toHaveTextContent("Salary"));
  });

  it("closes with Escape without picking, focus back on the button", async () => {
    const onchange = vi.fn();
    render(CategorySelect, { onchange });
    await open();
    await userEvent.keyboard("{ArrowDown}{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(onchange).not.toHaveBeenCalled();
    expect(screen.getByRole("combobox", { name: "Category" })).toHaveFocus();
  });

  it("closes with Tab and focus moves on", async () => {
    render(CategorySelect);
    const after = document.createElement("button"); after.textContent = "Next"; document.body.append(after);
    await open();
    await userEvent.tab();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(after).toHaveFocus();
    after.remove();
  });

  it("lists the last few categories picked under Recent, newest first", async () => {
    const { unmount } = render(CategorySelect);
    await pickCategory(screen.getByRole("combobox", { name: "Category" }), "Transfer");
    await pickCategory(screen.getByRole("combobox", { name: "Category" }), "Salary");
    unmount();
    render(CategorySelect);
    const list = await open();
    const recent = within(list).getAllByRole("group").find((g) => g.textContent!.startsWith("Recent"))!;
    expect(within(recent).getAllByRole("option").map((o) => o.dataset.value)).toEqual(["Salary", "Transfer"]);
  });

  it("keeps Recent to five, and leaves out what this picker doesn't offer", () => {
    for (const n of ["A", "B", "C", "D", "E", "F"]) rememberRecent(n);
    expect(recentCategories()).toEqual(["F", "E", "D", "C", "B"]);
    const s = pickSections({ groups: [{ label: "Spending", items: categories.list.slice(0, 1) }], query: "", recent: ["Salary", "Travel"], blank: false });
    expect(s.find((x) => x.label === "Recent")!.items.map((o) => o.value)).toEqual(["Travel"]);
  });

  it("still picks when the browser keeps nothing", async () => {
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    const onchange = vi.fn();
    render(CategorySelect, { onchange });
    await pickCategory(screen.getByRole("combobox", { name: "Category" }), "Salary");
    expect(onchange).toHaveBeenCalledWith("Salary");
    set.mockRestore();
  });

  it("takes options of its own before the groups", async () => {
    render(CategorySelect, { value: "__new__", extra: [{ value: "__new__", label: "✦ New: Pets" }] });
    expect(screen.getByRole("combobox", { name: "Category" })).toHaveTextContent("✦ New: Pets");
    expect(optionNames(await open())[1]).toBe("✦ New: Pets");
  });

  it("only offers categories that can still take children when choosing a parent", async () => {
    render(CategorySelect, { canHoldChildren: true });
    const list = await open();
    expect(within(list).queryByRole("option", { name: /Transit/ })).not.toBeInTheDocument();
    expect(within(list).getByRole("option", { name: /Travel/ })).toBeInTheDocument();
  });

  it("can leave categories out", async () => {
    render(CategorySelect, { exclude: (c) => c.name === "Salary" });
    expect(within(await open()).queryByRole("option", { name: /Salary/ })).not.toBeInTheDocument();
  });

  it("in short form shows just the category's own name; otherwise its path", () => {
    const { unmount } = render(CategorySelect, { short: true, value: "Transit" });
    expect(screen.getByRole("combobox")).toHaveTextContent(/^Transit$/);
    unmount();
    render(CategorySelect, { value: "Transit" });
    expect(screen.getByRole("combobox")).toHaveTextContent("Travel > Transit");
  });

  it("shows the blank text while nothing is chosen", () => {
    render(CategorySelect, { short: true, value: "", blank: "Choose…" });
    expect(screen.getByRole("combobox")).toHaveTextContent("Choose…");
  });

  it("can be disabled", async () => {
    render(CategorySelect, { disabled: true });
    expect(screen.getByRole("combobox")).toBeDisabled();
  });

  it("comes up as a sheet on a phone, the search box focused", async () => {
    viewport.phone = true;
    render(CategorySelect, { label: "Category for X" });
    await userEvent.click(screen.getByRole("combobox", { name: "Category for X" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("listbox")).toBeInTheDocument();
    await vi.waitFor(() => expect(within(dialog).getByRole("combobox", { name: "Search categories" })).toHaveFocus());
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
