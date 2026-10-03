// What the reports share about color and about opening Transactions from a chart.
import { catLook } from "$lib/categories.svelte";
import type { ShowFilters } from "$lib/filters.svelte";
import { isoDay, parseDate } from "$lib/format";

/** A top-level category's color in a chart: its own, the one Budget shows, so it's the same in every period and on
 * every tab. "Everything else" is always the reserved gray, and Uncategorized (no color of its own) a neutral one. */
export function catFill(name: string, other = false): string {
  if (other) return "var(--cat-other)";
  if (name === "Uncategorized") return "var(--muted-foreground)";
  return catLook(name).color;
}

/** A color as its sRGB channels (0–255): "#3987e5", "#38e", "rgb(57 135 229)", or a var() resolved on the page. */
export function channels(color: string): [number, number, number] | null {
  let c = color.trim();
  const v = /^var\((--[\w-]+)\)$/.exec(c);
  if (v) {
    if (typeof document === "undefined") return null;
    c = getComputedStyle(document.documentElement).getPropertyValue(v[1]).trim();
  }
  const hex = /^#([\da-f]{3}|[\da-f]{6})$/i.exec(c);
  if (hex) {
    const h = hex[1].length === 3 ? [...hex[1]].map((x) => x + x).join("") : hex[1];
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
  }
  const rgb = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i.exec(c);
  return rgb ? [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])] : null;
}

const luminance = ([r, g, b]: [number, number, number]) => {
  const lin = (x: number) => { const s = x / 255; return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
};
/** The contrast ratio of two colors' channels (1–21). */
export const contrast = (a: [number, number, number], b: [number, number, number]) => {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};

export const INK_DARK = "#0e1116", INK_LIGHT = "#ffffff";
/** The text color for a label on a fill: dark ink or white, whichever stands out more (one of them always reaches
 * 4.5:1). Dark ink when the fill can't be read. */
export function textOn(fill: string): string {
  const c = channels(fill);
  if (!c) return INK_DARK;
  return contrast(c, [255, 255, 255]) > contrast(c, [14, 17, 22]) ? INK_LIGHT : INK_DARK;
}

/** The day before a YYYY-MM-DD date: a report's end is the day after its last, Transactions' `to` is the last. */
export const dayBefore = (day: string) => { const d = parseDate(day); return isoDay(new Date(d.getFullYear(), d.getMonth(), d.getDate() - 1)); };

/** A report's category as Transactions' category filter: Uncategorized is the "no category" one. */
export const catFilter = (name: string) => (name === "Uncategorized" ? "__none__" : name);

/** Transactions for a report's figure: the accounts the reports count (`scope` "budget"), and the rest as given. */
export const drill = (f: ShowFilters): ShowFilters => ({ scope: "budget", ...f });
