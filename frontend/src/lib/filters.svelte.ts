// The Transactions list's filters. They live here, not in the page, so they survive moving between pages and so
// other pages can open Transactions already filtered: set them with showTransactions(). The page mirrors them into its
// address (#transactions?q=…&from=…), so a reload, Back and a bookmark keep them; Review keeps its own, off the address.
import { isoDay, monthLabel, parseDate, fmtDate } from "./format";

/** `from`/`to`: YYYY-MM-DD, both included. `min`/`max`: the amount either way, as typed. `scope` "budget": only the
 *  accounts the Budget page counts (qualifies a range opened from Budget). */
export interface TxFilters { q: string; account: string; category: string; from: string; to: string; min: string; max: string; kind: Kind; scope: string }
export type Kind = "" | "in" | "out" | "transfer";
export const KINDS: { value: Kind; label: string }[] = [
  { value: "", label: "All" }, { value: "out", label: "Money out" }, { value: "in", label: "Money in" }, { value: "transfer", label: "Transfers" },
];
const KEYS = ["q", "account", "category", "from", "to", "min", "max", "kind", "scope"] as const;

const blank = (): TxFilters => ({ q: "", account: "", category: "", from: "", to: "", min: "", max: "", kind: "", scope: "" });
export const txFilters = $state({ transactions: blank(), review: blank() });

// Whether All lists what's categorized Ignore (hidden unless you ask). Not a filter: Clear filters leaves it alone.
export const txShow = $state({ ignored: false });

/** A month (YYYY-MM) as the range of its days. */
export function monthRange(month: string): { from: string; to: string } {
  const [y, m] = month.split("-").map(Number);
  return { from: isoDay(new Date(y, m - 1, 1)), to: isoDay(new Date(y, m, 0)) };
}

/** What showTransactions takes: any filters, and a month (YYYY-MM) as shorthand for its first and last days. */
export type ShowFilters = Partial<TxFilters> & { month?: string };

/** Open Transactions filtered (e.g. a budget line's spending that month, or a report's range). Unset filters are
 *  cleared. */
export function showTransactions({ month, ...f }: ShowFilters): void {
  Object.assign(txFilters.transactions, blank(), month ? monthRange(month) : {}, f);
  const qs = toQuery(txFilters.transactions);
  location.hash = `#transactions${qs ? `?${qs}` : ""}`;
}

/** Whether any filter is narrowing the list (the scope only qualifies a range). */
export const isFiltered = (f: TxFilters): boolean => !!(f.q || f.account || f.category || f.from || f.to || f.min || f.max || f.kind);

/** Back to every transaction: every filter cleared. */
export function clearAll(f: TxFilters): void {
  Object.assign(f, blank());
}

/** The filters that are set, as an address's query ("q=rent&from=2026-03-01"). */
export function toQuery(f: TxFilters): string {
  const qs = new URLSearchParams();
  for (const k of KEYS) if (f[k]) qs.set(k, f[k]);
  return qs.toString();
}

const DAY = /^\d{4}-\d{2}-\d{2}$/;
const okDay = (s: string) => DAY.test(s) && isoDay(parseDate(s)) === s;
const okAmount = (s: string) => s !== "" && isFinite(Number(s)) && Number(s) >= 0;

/** The filters in an address's query; anything malformed (a typo'd date, a word for an amount) is left out. */
export function fromQuery(query: string): TxFilters {
  const qs = new URLSearchParams(query), f = blank();
  const get = (k: string) => (qs.get(k) ?? "").trim();
  f.q = qs.get("q") ?? "";
  f.account = get("account");
  f.category = get("category");
  if (okDay(get("from"))) f.from = get("from");
  if (okDay(get("to"))) f.to = get("to");
  if (okAmount(get("min"))) f.min = get("min");
  if (okAmount(get("max"))) f.max = get("max");
  if (KINDS.some((k) => k.value && k.value === get("kind"))) f.kind = get("kind") as Kind;
  if (get("scope") === "budget") f.scope = "budget";
  if (!qs.has("month") || f.from || f.to) return f;
  // An older link's month (?month=2026-03).
  if (/^\d{4}-\d{2}$/.test(get("month"))) Object.assign(f, monthRange(get("month")));
  return f;
}

/** Whether two sets of filters ask for the same list. */
export const sameFilters = (a: TxFilters, b: TxFilters): boolean => KEYS.every((k) => a[k] === b[k]);

// ------------------------------------------------------------------------------------------ date ranges

export interface Preset { id: string; label: string; from: string; to: string }

/** The date ranges offered with one tap. "All time" is no range at all. */
export function presets(today: Date = new Date()): Preset[] {
  const y = today.getFullYear(), m = today.getMonth();
  return [
    { id: "this-month", label: "This month", from: isoDay(new Date(y, m, 1)), to: isoDay(new Date(y, m + 1, 0)) },
    { id: "last-month", label: "Last month", from: isoDay(new Date(y, m - 1, 1)), to: isoDay(new Date(y, m, 0)) },
    { id: "last-3", label: "Last 3 months", from: isoDay(new Date(y, m - 2, 1)), to: isoDay(new Date(y, m + 1, 0)) },
    { id: "this-year", label: "This year", from: isoDay(new Date(y, 0, 1)), to: isoDay(new Date(y, 11, 31)) },
    { id: "all", label: "All time", from: "", to: "" },
  ];
}

/** What the date button says: "All dates", a preset's name, a whole month ("March 2026"), or the range. */
export function rangeLabel(from: string, to: string, today: Date = new Date()): string {
  if (!from && !to) return "All dates";
  const preset = presets(today).find((p) => p.from === from && p.to === to && p.id !== "all");
  if (preset) return preset.label;
  if (from && to && from.endsWith("-01") && monthRange(from.slice(0, 7)).to === to) return monthLabel(from.slice(0, 7));
  const thisYear = String(today.getFullYear());
  const day = (s: string, year: boolean) => fmtDate(s, year ? { month: "short", day: "numeric", year: "numeric" } : { month: "short", day: "numeric" });
  const yearOf = (s: string) => !s.startsWith(thisYear) || (!!from && !!to && from.slice(0, 4) !== to.slice(0, 4));
  if (from && to) return from === to ? day(from, yearOf(from)) : `${day(from, yearOf(from))} – ${day(to, yearOf(to))}`;
  return from ? `From ${day(from, yearOf(from))}` : `Until ${day(to, yearOf(to))}`;
}

/** The amount filter, said short: "$50–$100", "$50 or more", "Up to $100". */
export function amountLabel(min: string, max: string): string {
  const n = (s: string) => `$${Number(s).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
  if (min && max) return min === max ? n(min) : `${n(min)}–${n(max)}`;
  return min ? `${n(min)} or more` : max ? `Up to ${n(max)}` : "";
}
