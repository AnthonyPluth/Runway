import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Category } from "./types";

vi.mock("./api", () => ({ api: vi.fn() }));
import { api } from "./api";
import {
  CAT_COLORS,
  catColor,
  catLabel,
  catLook,
  catParentOf,
  catPath,
  categories,
  categoryGroups,
  lastEmoji,
  loadCategories,
} from "./categories.svelte";

const cat = (name: string, extra: Partial<Category> = {}): Category => ({
  name,
  path: [name],
  depth: 0,
  top: name,
  ...extra,
});
const tree: Category[] = [
  cat("Food", { icon: "🍽️", color: "#111111" }),
  cat("Groceries", {
    parent: "Food",
    path: ["Food", "Groceries"],
    depth: 1,
    top: "Food",
  }),
  cat("Salary", { is_income: true }),
  cat("Transfer", { is_transfer: 1 }),
];
beforeEach(() => {
  categories.list = tree;
});

describe("loadCategories", () => {
  it("stores what the server sends", async () => {
    vi.mocked(api).mockResolvedValue(tree);
    expect(await loadCategories()).toBe(categories.list);
    expect(api).toHaveBeenCalledWith("/api/categories", { keep: true });
  });

  it("fills in path, depth and top from the parent links when an older server omits them", async () => {
    vi.mocked(api).mockResolvedValue([
      { name: "Travel" },
      { name: "Flights", parent: "Travel" },
      { name: "Budget air", parent: "Flights" },
    ]);
    const list = await loadCategories();
    expect(list.map((c) => [c.path, c.depth, c.top])).toEqual([
      [["Travel"], 0, "Travel"],
      [["Travel", "Flights"], 1, "Travel"],
      [["Travel", "Flights", "Budget air"], 2, "Travel"],
    ]);
  });

  it("doesn't loop forever on parents that point at each other", async () => {
    vi.mocked(api).mockResolvedValue([
      { name: "A", parent: "B" },
      { name: "B", parent: "A" },
    ]);
    const [a] = await loadCategories();
    expect(a.path).toEqual(["B", "A"]);
  });
});

describe("category helpers", () => {
  it("labels a subcategory with its path and a top-level one with its name", () => {
    expect(catLabel(tree[1])).toBe("Food > Groceries");
    expect(catLabel(tree[0])).toBe("Food");
  });

  it("looks up a category's emoji and color, defaulting to a tag on gray for unknown names", () => {
    expect(catLook("Food")).toEqual({ icon: "🍽️", color: "#111111" });
    expect(catLook("Gone")).toEqual({ icon: "🏷️", color: "var(--cat-other)" });
    expect(catLook(null)).toEqual({ icon: "🏷️", color: "var(--cat-other)" });
  });

  it("finds a category's parent", () => {
    expect(catParentOf("Groceries")).toBe("Food");
    expect(catParentOf("Food")).toBeNull();
    expect(catParentOf(undefined)).toBeNull();
  });

  it("walks up from a category to the top, stopping on a loop", () => {
    expect(catPath("Groceries")).toEqual(["Groceries", "Food"]);
    expect(catPath(null)).toEqual([]);
    categories.list = [cat("A", { parent: "B" }), cat("B", { parent: "A" })];
    expect(catPath("A")).toEqual(["A", "B"]);
  });

  it("uses fixed chart colors, then gray for the rest and for 'everything else'", () => {
    expect(catColor(0)).toBe("var(--cat-1)");
    expect(catColor(CAT_COLORS - 1)).toBe(`var(--cat-${CAT_COLORS})`);
    expect(catColor(CAT_COLORS)).toBe("var(--cat-other)");
    expect(catColor(0, true)).toBe("var(--cat-other)");
  });
});

describe("categoryGroups", () => {
  it("splits categories into Spending, Money in and Not spending, dropping empty groups", () => {
    expect(
      categoryGroups().map((g) => [g.label, g.items.map((c) => c.name)]),
    ).toEqual([
      ["Spending", ["Food", "Groceries"]],
      ["Money in", ["Salary"]],
      ["Not spending", ["Transfer"]],
    ]);
    categories.list = [tree[0]];
    expect(categoryGroups().map((g) => g.label)).toEqual(["Spending"]);
  });

  it("offers only categories that can still hold a child when picking a parent", () => {
    expect(
      categoryGroups({ canHoldChildren: true })[0].items.map((c) => c.name),
    ).toEqual(["Food"]);
  });

  it("applies an exclusion", () => {
    expect(
      categoryGroups({ exclude: (c) => c.name === "Food" })[0].items.map(
        (c) => c.name,
      ),
    ).toEqual(["Groceries"]);
  });
});

describe("lastEmoji", () => {
  it("finds one whole emoji in what was typed or pasted", () => {
    for (const e of [
      "🌮",
      "🍽️",
      "👍🏽",
      "🇯🇵",
      "1️⃣",
      "#️⃣",
      "👩‍💻",
      "👨‍👩‍👧‍👦",
      "❤️",
      "▶️",
      "ℹ️",
      "‼️",
      "〰️",
      "↔️",
    ])
      expect(lastEmoji(e)).toBe(e);
    expect(lastEmoji("taco 🌮")).toBe("🌮");
    expect(lastEmoji("🌮🍕")).toBe("🍕");
  });
  it("finds nothing in letters, digits or punctuation", () => {
    for (const t of ["", "taco", "1", "#", "!?", "  ", "é", "!\ufe0f"])
      expect(lastEmoji(t)).toBeNull();
  });
});
