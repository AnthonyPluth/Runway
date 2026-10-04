// What the category picker lists for what's typed: the groups (Spending, Money in, Not spending), the last few
// categories picked ("Recent", while nothing's typed), and any options of the caller's own. Kept apart from the
// component so it can be tested on its own.
import { catLabel } from "$lib/categories.svelte";
import type { Category } from "$lib/types";

export interface PickOption { value: string; label: string; icon?: string }
export interface PickSection { label: string; items: PickOption[] }

const RECENT_KEY = "runway.recent-categories";
const RECENT_MAX = 5;

/** The categories picked last, newest first (none when the browser keeps nothing). */
export function recentCategories(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string").slice(0, RECENT_MAX) : [];
  } catch { return []; }
}

/** Remember a category as just picked. */
export function rememberRecent(name: string): void {
  if (!name) return;
  try { localStorage.setItem(RECENT_KEY, JSON.stringify([name, ...recentCategories().filter((n) => n !== name)].slice(0, RECENT_MAX))); }
  catch { /* not remembered; picking still works */ }
}

const optionOf = (c: Category): PickOption => ({ value: c.name, label: catLabel(c), icon: c.icon || undefined });

/** Lower case, without accents, for matching what's typed. */
const fold = (s: string) => s.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();

/** The sections to list. With something typed: only the options whose name (or path) has it, in their groups. */
export function pickSections(opts: { groups: { label: string; items: Category[] }[]; query: string; recent: string[];
  blank: string | false; extra?: PickOption[] }): PickSection[] {
  const q = fold(opts.query.trim());
  const groups = opts.groups.map((g) => ({ label: g.label, items: g.items.map(optionOf) }));
  if (q) {
    const has = (o: PickOption) => fold(o.label).includes(q);
    return [{ label: "", items: (opts.extra ?? []).filter(has) }, ...groups.map((g) => ({ ...g, items: g.items.filter(has) }))]
      .filter((s) => s.items.length);
  }
  const offered = new Map(groups.flatMap((g) => g.items).map((o) => [o.value, o]));
  const recent = opts.recent.map((n) => offered.get(n)).filter((o): o is PickOption => !!o);
  return [
    { label: "", items: [...(opts.blank === false ? [] : [{ value: "", label: opts.blank }]), ...(opts.extra ?? [])] },
    { label: "Recent", items: recent },
    ...groups,
  ].filter((s) => s.items.length);
}
