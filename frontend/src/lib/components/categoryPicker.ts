// What the category picker lists for what's typed: the groups (Spending, Money in, Not spending) and any options of
// the caller's own. Kept apart from the component so it can be tested on its own.
import type { Category } from "$lib/types";

/** `name`, `parent` and `depth` are set for a category: the list indents a child under its parent by `depth`. */
export interface PickOption { value: string; label: string; icon?: string; name?: string; parent?: string | null; depth?: number }
export interface PickSection { label: string; items: PickOption[] }

/** The category it sits directly under (null for a top-level one): what the chip and the picker show beside its name. */
export const catParentName = (c: Category | undefined): string | null =>
  c ? c.parent || (c.path && c.path.length > 1 ? c.path[c.path.length - 2] : null) || null : null;

const optionOf = (c: Category): PickOption => ({
  value: c.name, label: c.name, icon: c.icon || undefined, name: c.name, parent: catParentName(c), depth: c.depth || 0 });

/** Lower case, without accents, for matching what's typed. */
const fold = (s: string) => s.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();

/** The sections to list. Browsing, a child sits indented under its parent. With something typed: only the options
 * whose name (or any parent's) has it, in their groups, flat, a child written "Child · Parent". */
export function pickSections(opts: { groups: { label: string; items: Category[] }[]; query: string;
  blank: string | false; extra?: PickOption[] }): PickSection[] {
  const q = fold(opts.query.trim());
  if (q) {
    const has = (text: string) => fold(text).includes(q);
    const found = (c: Category) => has(c.path?.length ? c.path.join(" ") : c.name);
    const flat = (c: Category): PickOption => ({ ...optionOf(c), depth: 0, label: catParentName(c) ? `${c.name} · ${catParentName(c)}` : c.name });
    return [{ label: "", items: (opts.extra ?? []).filter((o) => has(o.label)) },
      ...opts.groups.map((g) => ({ label: g.label, items: g.items.filter(found).map(flat) }))].filter((s) => s.items.length);
  }
  return [
    { label: "", items: [...(opts.blank === false ? [] : [{ value: "", label: opts.blank }]), ...(opts.extra ?? [])] },
    ...opts.groups.map((g) => ({ label: g.label, items: g.items.map(optionOf) })),
  ].filter((s) => s.items.length);
}
