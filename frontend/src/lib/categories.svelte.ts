// Runway's categories, shared by every page that shows or picks one. Load them with loadCategories() before use;
// they come back in tree order (each category followed by its subcategories).
import { api } from "./api";
import type { Category } from "./types";

export const CAT_MAX_DEPTH = 2;   // levels including the top one (matches the server): Parent > Sub

export const categories = $state({ list: [] as Category[] });

// Fill in path/depth/top from the parent links if the server didn't send them (e.g. an older server still running).
function withPaths(list: Category[]): Category[] {
  const parentOf = Object.fromEntries(list.map((c) => [c.name, c.parent || null]));
  for (const c of list) {
    if (Array.isArray(c.path) && c.path.length) continue;
    const path = [c.name];
    let p = parentOf[c.name];
    while (p && !path.includes(p) && path.length < 10) { path.unshift(p); p = parentOf[p]; }
    c.path = path; c.depth = path.length - 1; c.top = path[0];
  }
  return list;
}

export async function loadCategories(): Promise<Category[]> {
  categories.list = withPaths(await api<Category[]>("/api/categories", { keep: true }));
  return categories.list;
}

export const catLabel = (c: Category) => (c.path && c.path.length > 1 ? c.path.join(" > ") : c.name);

/** A category's emoji and color. Unknown names (a category removed meanwhile) get a tag and gray. */
export function catLook(name: string | null | undefined): { icon: string; color: string } {
  const c = name ? categories.list.find((x) => x.name === name) : undefined;
  return { icon: c?.icon || "🏷️", color: c?.color || "var(--cat-other)" };
}
/** The colors a category can wear (the server's categories.PALETTE). */
export const CAT_PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767", "#1c9aa8", "#8a8a86"];
/** Emoji offered when picking one (any emoji can be typed too). */
export const CAT_EMOJI = ["🛒", "🍽️", "☕", "🍔", "🍷", "🛍️", "👕", "✈️", "🚆", "🚕", "⛽", "🅿️", "🚗", "💡", "📱", "🔁", "💻",
  "🩺", "💊", "🏋️", "🔨", "🏠", "🏡", "🏦", "🧾", "🎬", "🎮", "🎨", "🎁", "💸", "📦", "💰", "↩️", "💳", "🔄", "🚫", "🐾",
  "🧸", "📚", "🛡️", "💅", "💼", "📈", "🎓", "⚽", "🎵", "🌱", "🧹", "🍼", "🏷️"];
export const catParentOf = (name: string | null | undefined) => categories.list.find((c) => c.name === name)?.parent || null;

/** The categories as the pickers group them: Spending, Money in, Not spending. */
export function categoryGroups(opts: { canHoldChildren?: boolean; exclude?: (c: Category) => boolean } = {}) {
  const groups: [string, (c: Category) => boolean][] = [
    ["Spending", (c) => !c.is_transfer && !c.is_income], ["Money in", (c) => !!c.is_income], ["Not spending", (c) => !!c.is_transfer]];
  return groups.map(([label, test]) => ({
    label,
    items: categories.list.filter(test)
      .filter((c) => !opts.canHoldChildren || (c.depth || 0) < CAT_MAX_DEPTH - 1)
      .filter((c) => !opts.exclude || !opts.exclude(c)),
  })).filter((g) => g.items.length);
}

// Categorical chart colors in fixed order (validated for the dark surface); "everything else" is always gray.
export const CAT_COLORS = 8;
export const catColor = (i: number, other = false) => (other || i >= CAT_COLORS ? "var(--cat-other)" : `var(--cat-${i + 1})`);
