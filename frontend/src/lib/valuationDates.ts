// What the "Valuation dates" sheets on Net worth and Investments say. Built only from what the pages already have (the
// server's totals and each contributor's own date and source): nothing here adds up, reprices or decides what counts.
// A missing date is "Date unavailable", never today's.
import { KIND_LABEL } from "$lib/accounts";
import type { InvAccount, Investments, LiveQuotes } from "$lib/components/investments/types";
import { valueSource } from "$lib/components/networth/homeValues";
import type { NetWorth } from "$lib/components/networth/types";
import type { PlaidStatus, SimplefinSeen } from "$lib/components/settings/types";
import { fmtDate, fmtDateTime, isoDay, serverTime } from "$lib/format";

export const UNAVAILABLE = "Date unavailable";
export const DIFFERENT = "Values from different dates";

/** One value that is part of (or, in `left`, not part of) the headline. */
export interface DateRow {
  key: string;
  name: string;
  /** Where the row sits: the Net worth group, or the Investments institution. */
  group: string;
  amount: number | null;
  /** Where the value comes from: "Bank balance", "Realie estimate", "SimpleFIN sync". */
  source: string;
  /** What the date is the date of: "Balance synced", "Value as of". */
  dateLabel: string;
  /** The day it is valued (YYYY-MM-DD), or null when nothing says. */
  day: string | null;
  dateText: string;
  note?: string;
}
/** The rows that share a date, newest first; the one with no date is last. */
export interface DateGroup { label: string; day: string | null; names: string[] }
export interface Summary { headline: string; detail: string; mixed: boolean; groups: DateGroup[] }
export interface Note { title: string; text: string }
export interface ValuationModel {
  counted: DateRow[];
  /** Left out of the total (and so of the summary): shown apart, never added in. */
  left: DateRow[];
  summary: Summary;
  notes: Note[];
}

const longDate = (s: string) => fmtDate(s, { month: "short", day: "numeric", year: "numeric" });
const dayOf = (s: string | null | undefined) => (s ? s.slice(0, 10) : null);
const dayText = (day: string | null) => (day ? longDate(day) : UNAVAILABLE);

/** Groups rows (and any extra entries, like live quotes) by date and says whether they differ. */
export function summarize(entries: { name: string; day: string | null; label?: string }[], what: string): Summary {
  const by = new Map<string, DateGroup>();
  for (const e of entries) {
    const key = e.day ?? "";
    const g = by.get(key) ?? { label: e.label ?? dayText(e.day), day: e.day, names: [] };
    g.names.push(e.name);
    by.set(key, g);
  }
  const groups = [...by.values()].sort((a, b) => (a.day == null ? 1 : b.day == null ? -1 : a.day < b.day ? 1 : a.day > b.day ? -1 : 0));
  const known = groups.filter((g) => g.day != null);
  const unknown = groups.some((g) => g.day == null);
  if (known.length > 1) return { headline: DIFFERENT, detail: `${what} are not all valued on the same day.`, mixed: true, groups };
  if (known.length === 1 && !unknown) return { headline: `All values are from ${known[0].label}`, detail: "", mixed: false, groups };
  if (known.length === 1) return { headline: "Some values have no date", detail: `${UNAVAILABLE} for some, so the rest can't be compared with them.`, mixed: false, groups };
  return { headline: UNAVAILABLE, detail: groups.length ? "Nothing here says when its value is from." : "", mixed: false, groups };
}

/** Net worth: every account, asset and equity line in the breakdown, with its own date; accounts left out are apart. */
export function netWorthDates(d: NetWorth): ValuationModel {
  const counted: DateRow[] = [];
  for (const g of d.groups) {
    for (const i of g.items) {
      const day = dayOf(i.as_of);
      const row = { key: `${i.type}:${i.id}`, name: i.name, group: g.label, amount: i.value, day, dateText: dayText(day) };
      if (i.type === "account") {
        counted.push({ ...row, source: i.org ? `${i.org} · bank balance` : "Bank balance", dateLabel: "Balance synced",
          note: i.synced != null ? "Counted as paid down by its terms since then" : undefined });
      } else if (i.type === "equity") {
        counted.push({ ...row, source: i.source === "carta" ? "Share price from Carta" : "Share price you entered", dateLabel: "Share price as of" });
      } else {
        counted.push({ ...row, source: valueSource(i.source), dateLabel: "Value as of",
          note: i.loan ? `${i.loan.name} is counted once, under Liabilities` : undefined });
      }
    }
  }
  const left: DateRow[] = d.excluded.map((a) => {
    const day = dayOf(a.as_of);
    return { key: `left:${a.id}`, name: a.name, group: (KIND_LABEL as Record<string, string>)[a.kind] ?? "Account", amount: a.balance,
      source: a.org ? `${a.org} · bank balance` : "Bank balance", dateLabel: "Balance synced", day, dateText: dayText(day) };
  });
  const last = d.history.at(-1)?.date;
  return {
    counted, left,
    summary: summarize(counted.map((r) => ({ name: r.name, day: r.day })), "Accounts and assets"),
    notes: [
      { title: "Investment accounts", text: "These use their synced balance. Live quotes on Investments don't change this total." },
      { title: "The history chart", text: last
        ? `The chart and the change figures are daily snapshots of net worth, saved on days you open Runway. The latest is ${longDate(last)}. They are past totals, not today's valuations.`
        : "History builds up each day you open Runway." },
    ],
  };
}

// What the Accounts card shows for an account (components/investments/AccountsCard.svelte): its balance, or with none
// the values of its lots.
const accountValue = (a: InvAccount, d: Investments) =>
  a.balance ?? d.holdings.reduce((s, h) => s + h.lots.filter((l) => l.account_id === a.id).reduce((t, l) => t + l.value, 0), 0);

const seenFor = (seen: SimplefinSeen[], a: InvAccount) => seen.find((x) => x.id === a.id.slice(3));

/** Investments: each account in the accounts list, dated by the sync that brought it, plus live quotes and the charts. */
export function investmentDates(d: Investments, status: PlaidStatus, live: LiveQuotes | null): ValuationModel {
  const syncOf = (a: InvAccount): { at: string | null; source: string } => {
    if (a.source === "simplefin") return { at: status.simplefin_last_sync, source: "SimpleFIN sync" };
    const item = status.items.find((i) => i.item_id === a.item_id);
    return { at: item?.sides?.investments?.last_sync ?? item?.last_sync ?? null, source: "Plaid holdings sync" };
  };
  const row = (a: InvAccount, left: boolean): DateRow => {
    const { at, source } = syncOf(a);
    const when = at ? serverTime(at) : null;
    const valid = when != null && !Number.isNaN(when.getTime());
    const hand = a.source === "simplefin" && a.tracked
      ? `Funds entered by hand, last saved: ${a.tracked_updated ? longDate(a.tracked_updated) : UNAVAILABLE}`
      : a.source === "simplefin" && seenFor(status.simplefin_seen ?? [], a)?.positions === 0 ? "Balance only, no holdings sent" : undefined;
    const notes = [hand, a.also_simplefin ? "Also sent by SimpleFIN; counted once" : undefined, left ? "Left out in Settings → Accounts" : undefined].filter(Boolean);
    return { key: `${left ? "left:" : ""}${a.id}`, name: a.name || a.official_name || "Account", group: a.institution_name || "Account", amount: accountValue(a, d),
      source, dateLabel: "Last synced", day: valid ? isoDay(when) : null, dateText: valid ? fmtDateTime(when) : UNAVAILABLE, note: notes.join(" · ") || undefined };
  };
  const counted = d.accounts.filter((a) => !a.hidden).map((a) => row(a, false));
  const left = d.accounts.filter((a) => a.hidden && a.hidden_in_accounts).map((a) => row(a, true));
  const entries: { name: string; day: string | null; label?: string }[] = counted.map((r) => ({ name: r.name, day: r.day }));
  const clock = live ? new Date(live.as_of).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" }) : "";
  if (live) {
    entries.push({ name: "Stocks and ETFs", day: `live ${live.as_of}`, label: `${live.market === "open" ? "Live quotes" : "Latest quotes, market closed"} · ${clock}` });
  }
  const at = [status.last_inv_sync, status.simplefin_last_sync].filter((x): x is string => !!x).map((x) => serverTime(x)).sort((a, b) => a.getTime() - b.getTime()).pop();
  const lastDate = d.history.dates.at(-1);
  const notes: Note[] = [
    { title: "Holdings sync", text: at ? `Holdings, balances and cost basis are from the last sync, ${fmtDateTime(at)} (the latest of Plaid and SimpleFIN).` : "Nothing has synced yet." },
    { title: "Live quotes", text: !live ? "No live quotes are loaded, so prices are those from the last sync."
      : live.market === "open" ? `Live quotes from ${clock} re-price stocks and ETFs in the total. Funds that price once a day, cash and balances keep their last-sync values.`
      : `The market is closed. The total uses the latest quotes, as of ${clock}.` },
    { title: "The charts", text: `${lastDate ? `The charts run to ${longDate(lastDate)} and are` : "The charts are"} as of the last sync, not live quotes.${d.history.estimated_before ? ` Values before ${longDate(d.history.estimated_before)} are estimated.` : ""}` },
    { title: "Account figures", text: "Each account shows the balance on the Accounts list. The headline is the sum of the holdings, so the two can differ slightly (cash, for one)." },
  ];
  return { counted, left, summary: summarize(entries, "Accounts and quotes"), notes };
}
