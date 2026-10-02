// How the Churning page words and filters what the server worked out (runway/churning.py, runway/bank_bonuses.py).
import { fmt0, fmtDate, parseDate, plural } from "$lib/format";
import type {
  BankBonus, Benefit, Blocker, CardPlan, ChurnCard, ChurnRate, Churning, CreditScore, Currency, CurrencyGroup, Eligibility, Five24,
  UpcomingItem, Wish,
} from "./types";

export const BOTH = "";   // the person switcher's "everyone" value

/** Only this person's items ("" = everyone's). */
export const mine = <T extends { owner: string | null }>(items: T[], person: string) =>
  person === BOTH ? items : items.filter((x) => x.owner === person);

export const daysUntil = (day: string, today: string) =>
  Math.round((parseDate(day).getTime() - parseDate(today).getTime()) / 864e5);

/** "Oct 12" within this year, "Jan 10, 2027" in any other. */
const fullDate = (d: string) => fmtDate(d, parseDate(d).getFullYear() === new Date().getFullYear() ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" });
export { fullDate };

/** 60,000 → "60k", 1,250 → "1,250" (points and miles). */
export function points(n: number | null | undefined): string {
  if (n == null) return "—";
  if (Math.abs(n) >= 10000) return `${Math.round(n / 1000).toLocaleString("en-US")}k`;
  return Math.round(n).toLocaleString("en-US");
}

/** A balance to edit, with its commas ("125,000"); "" for none. */
export const balanceText = (n: number | null | undefined) => (n == null ? "" : n.toLocaleString("en-US", { maximumFractionDigits: 2 }));
/** What was typed in a balance field, without the commas (or spaces) it may have been written with. */
export const balanceValue = (text: string) => text.replace(/[,\s]/g, "");

/** A bonus in its own currency: "$200" for cash, "60k Ultimate Rewards" for points. */
export function bonusLabel(amount: number | null, currencyKey: string, currencyName: string): string {
  if (!amount) return "";
  return currencyKey === "cash" ? fmt0(amount) : `${points(amount)} ${currencyName.replace(/^(Chase|Amex|Citi|Capital One) /, "")}`;
}

/** Spending toward a bonus: the share done (0 to 1) and what's left. */
export function spendProgress(spent: number | null, need: number | null): { share: number; left: number } {
  const s = spent ?? 0, n = need ?? 0;
  if (n <= 0) return { share: s > 0 ? 1 : 0, left: 0 };
  return { share: Math.max(0, Math.min(1, s / n)), left: Math.max(0, n - s) };
}

/** When a bonus can be earned again, in a few words. */
export function eligibilityText(e: Eligibility, today: string): string {
  switch (e.status) {
    case "now": return e.override ? "Now (your date)" : "Now";
    case "later": return fullDate(e.on!) + (e.override ? " (your date)" : "");
    case "never": return "Never (once per lifetime)";
    case "in_progress": return "Earning it now";
    case "held": return e.on && daysUntil(e.on, today) > 0 ? `After you close it, from ${fullDate(e.on)}` : "After you close or downgrade it";
    case "au": return "—";
    default: return "Rule unknown";
  }
}

/** "3/24" and what happens next: "4/24 on Jan 10, 2027" (at 5 or more: the day you're under). */
export function five24Line(f: Five24 | undefined): { count: string; next: string } {
  if (!f) return { count: "0/24", next: "No cards yet" };
  const count = `${f.count}/24`;
  if (f.under_on) return { count, next: `Under 5/24 on ${fullDate(f.under_on)}` };
  if (f.next_fall_off) return { count, next: `${f.count - 1}/24 on ${fullDate(f.next_fall_off)}` };
  return { count, next: "Nothing counts right now" };
}

// Categories an issuer's travel portal books (travel, or one named for flights, hotels and the like, or under one): only
// these offer a portal-only rate.
const TRAVEL = /travel|flight|airfare|airline|hotel|lodging|car rental|rental car|cruise|vacation/i;
export const isTravel = (name: string | null | undefined, parent?: string | null): boolean =>
  !!name && (TRAVEL.test(name) || (!!parent && TRAVEL.test(parent)));

export const STATUS_LABEL: Record<string, string> = { open: "Open", closed: "Closed", product_changed: "Product changed" };
export const BANK_STATUS_LABEL: Record<string, string> = { open: "Open", pending: "Requirements met", received: "Bonus received", closed: "Closed" };
export const BANK_TYPE_LABEL: Record<string, string> = { checking: "Checking", savings: "Savings", business: "Business" };
export const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** Open cards first, then by when they were opened, newest first. */
export const cardOrder = (a: ChurnCard, b: ChurnCard) =>
  Number(a.status !== "open") - Number(b.status !== "open") || b.opened_on.localeCompare(a.opened_on) || a.id - b.id;

/** Bonuses still in play first (being earned, then waiting to post), then the rest, newest first. */
export function bankOrder(a: BankBonus, b: BankBonus): number {
  const rank = { active: 0, met: 1, missed: 2, received: 3, closed: 4 };
  return rank[a.state] - rank[b.state] || b.opened_on.localeCompare(a.opened_on) || a.id - b.id;
}

/** Annual fees due in the next `days` days: the total and how many. */
export function feesDue(cards: ChurnCard[], today: string, days = 90): { total: number; count: number } {
  const due = cards.filter((c) => c.fee_due && daysUntil(c.fee_due, today) <= days);
  return { total: due.reduce((s, c) => s + (c.annual_fee || 0), 0), count: due.length };
}

/** Bank bonus requirements that aren't met yet, in words: "$200 more direct deposits · 3 more debit purchases". */
export function bankLeft(b: BankBonus): string[] {
  const p = b.progress, out: string[] = [];
  if (b.dd_total && p.dd_total < b.dd_total) out.push(`${fmt0(b.dd_total - p.dd_total)} more direct deposits`);
  if (b.dd_count && p.dd_count != null && p.dd_count < b.dd_count) out.push(`${b.dd_count - p.dd_count} more deposit${b.dd_count - p.dd_count === 1 ? "" : "s"}`);
  if (b.debit_count && p.debits < b.debit_count) out.push(`${b.debit_count - p.debits} more debit purchase${b.debit_count - p.debits === 1 ? "" : "s"}`);
  if (b.min_balance && p.balance_ok === false) out.push(`${fmt0(b.min_balance - (p.balance ?? 0))} more to reach the minimum balance`);
  return out;
}

/** The Upcoming list's label for each kind of item. */
export const KIND_LABEL: Record<UpcomingItem["kind"], string> = {
  task: "To-do", fee: "Annual fee", plan: "Your plan", bonus: "Bonus spending", benefit: "Card credit", five24: "5/24",
  eligible: "Bonus again", apply: "Apply", offer_ends: "Offer ends", bank_due: "Bank bonus", bank_hold: "Balance hold",
  bank_post: "Bonus posting", bank_close: "Safe to close", bank_fee: "Monthly fee", bank_eligible: "Bonus again",
};

export const PLAN_LABEL: Record<CardPlan, string> = {
  undecided: "Undecided", keep: "Keep it", close: "Close it", product_change: "Product change (downgrade or switch)",
};
/** Plans that mean doing something to the card, so they have a day and a reminder. */
export const PLAN_ACTS: CardPlan[] = ["close", "product_change"];

/** A card's plan in a few words for its row: "Keeping it", "Product change to Freedom by Oct 20", "Done Oct 3". */
export function planLine(c: ChurnCard): string {
  if (c.plan_done_on) return `Done ${fullDate(c.plan_done_on)}`;
  const by = c.plan_due ? ` by ${fmtDate(c.plan_due)}` : "";
  switch (c.plan) {
    case "keep": return "Keeping it";
    case "product_change": return `Product change${c.plan_target ? ` to ${c.plan_target}` : ""}${by}`;
    case "close": return `Close it${by}`;
    default: return "";
  }
}

/** "Benefits $650/yr · net fee −$100" on a card's row; empty when it has no benefits. */
export function benefitSummary(c: Pick<ChurnCard, "benefits" | "benefits_value" | "net_fee">): string {
  if (!c.benefits.length) return "";
  const net = c.net_fee < 0 ? `−${fmt0(-c.net_fee)}` : fmt0(c.net_fee);
  return `Benefits ${fmt0(c.benefits_value)}/yr · net fee ${net}`;
}

/** Earning rates as text, with the portal-only ones marked: "5x Travel (via Capital One Travel), 2x Dining". */
export function ratesText(rates: ChurnRate[], portalName?: string | null): string {
  return rates.map((r) => `${r.multiplier}x ${r.category}${r.portal_only ? ` (${portalName ? `via ${portalName}` : "portal"})` : ""}`).join(", ");
}

// A number box gives a number, or null (or "") once emptied; a form's rows hold whichever it last had.
export interface RateRow { category: string; multiplier: string | number | null; portal_only: boolean }

const blank = (x: unknown) => x == null || x === "";

/** The `rates` a form sends: the base rate first (as the "*" marker), then each row. A row without a category or a
 * multiplier goes as it is, and the server's message names the problem. */
export function ratesPayload(base: string | number | null, rows: RateRow[], marker = "*"): { category: string; multiplier: number | null; portal_only: boolean }[] {
  const out = rows.map((r) => ({ category: r.category, multiplier: blank(r.multiplier) ? null : Number(r.multiplier), portal_only: r.portal_only }));
  return blank(base) ? out : [{ category: marker, multiplier: Number(base), portal_only: false }, ...out];
}

export { ownerChoices } from "$lib/owners";

/** Currencies in the groups the server sent (bank points, airlines, hotels, cash); any it left out go last, under
 * "Other". */
export function currencyGroups(d: Pick<Churning, "currencies" | "currency_groups">): { kind: CurrencyGroup["kind"]; label: string; currencies: Currency[] }[] {
  const byKey = new Map(d.currencies.map((c) => [c.key, c]));
  const seen = new Set<string>();
  const out = d.currency_groups.map((g) => ({
    kind: g.kind, label: g.label,
    currencies: g.keys.flatMap((k) => { const c = byKey.get(k); if (!c || seen.has(k)) return []; seen.add(k); return [c]; }),
  })).filter((g) => g.currencies.length);
  const rest = d.currencies.filter((c) => !seen.has(c.key));
  return rest.length ? [...out, { kind: "other" as const, label: "Other", currencies: rest }] : out;
}

/** Where a point value came from: "your value", or "estimate (as of Jun 2026)". */
export function valueSource(c: Currency, valuesAsOf: string): string {
  if (c.custom) return "your currency";
  if (c.overridden) return "your value";
  const day = c.as_of || valuesAsOf;   // the server dates its estimates by month ("2026-09")
  return day ? `estimate (as of ${fmtDate(day.length === 7 ? `${day}-01` : day, { month: "short", year: "numeric" })})` : "estimate";
}

/** Access and status (a lounge network, free bags, hotel status): on all year, nothing to use up. */
export const isPerk = (b: Pick<Benefit, "kind">) => b.kind === "access" || b.kind === "status";

/** Who gets into a lounge: "Cardholder only", "Cardholder + 2 guests"; empty when it isn't set. */
export function guestsText(guests: number | null | undefined): string {
  if (guests == null) return "";
  return guests === 0 ? "Cardholder only" : `Cardholder + ${guests} guest${guests === 1 ? "" : "s"}`;
}

/** A benefit's current period in words: "$150 of $300 used · resets Dec 31", "Not used this period"; a perk's guests
 * and visits: "Cardholder + 2 guests · used twice this period". */
export function benefitState(b: Benefit): string {
  const times = (n: number) => (n === 1 ? "once" : n === 2 ? "twice" : `${n} times`);
  if (isPerk(b)) {
    const parts = [b.kind === "access" ? guestsText(b.guests) : "", b.used_count ? `used ${times(b.used_count)} this period` : ""].filter(Boolean);
    const line = parts.join(" · ") || "Included";
    return line[0].toUpperCase() + line.slice(1);
  }
  const resets = b.period_end ? ` · ${b.period === "one_time" ? "expires" : "resets"} ${fmtDate(b.period_end)}` : "";
  if (b.kind === "credit" && b.amount != null) {
    return (b.remaining ?? 0) <= 0.005 ? `All ${fmt0(b.amount)} used${resets}` : `${fmt0(b.used ?? 0)} of ${fmt0(b.amount)} used${resets}`;
  }
  return b.used_count ? `Used ${times(b.used_count)} this period${resets}` : `Not used this period${resets}`;
}

/** This period's uses in a line: "Used $100 on Sep 1, $50 on Sep 20", "Used Sep 1"; empty when none. */
export const usesText = (b: Pick<Benefit, "uses">) =>
  b.uses.length ? `Used ${b.uses.map((u) => (u.amount_used != null ? `${fmt0(u.amount_used)} on ` : "") + fmtDate(u.used_on)).join(", ")}` : "";

/** Whether a benefit has something left to mark used this period: a credit with money left, one not used yet, or a
 * perk (a lounge visit can be logged any number of times). */
export const canUse = (b: Benefit) => (isPerk(b) ? true : b.kind === "credit" && b.amount != null ? (b.remaining ?? 0) > 0.005 : b.used_count === 0);

/** A card's benefits in reading order: credits, then other one-offs, then perks (lounges by network), each by name. */
export function benefitOrder(a: Benefit, b: Benefit): number {
  const rank = { credit: 0, other: 1, access: 2, status: 3 };
  return rank[a.kind] - rank[b.kind] || a.name.localeCompare(b.name);
}

export const BLOCKER_LABEL: Record<Blocker["kind"], string> = {
  five24: "5/24", bonus_rule: "Bonus rule", held: "Still open", wait: "Waiting", score: "Credit score", offer: "Offer",
};

/** "705 of 740 wanted": a person's latest score against what a planned item wants; null when it wants none. */
export function scoreProgress(w: Pick<Wish, "owner" | "min_score">, scores: Record<string, CreditScore>): string | null {
  if (!w.min_score) return null;
  const s = scores[w.owner];
  return s ? `${s.score} of ${w.min_score} wanted` : `${w.min_score} wanted · no score entered`;
}

/** A planned item's name: the card, or the bank and its account. */
export const wishName = (w: Pick<Wish, "kind" | "product" | "bank">) =>
  w.kind === "card" ? (w.product ?? "") : [w.bank, w.product].filter(Boolean).join(" ");

/** Planned items still in play (wanted or ready) apart from the ones applied for or dropped. */
/** Planned items still to do (each person's together, in their order: priorities are per person, so everyone's
 * interleaved by number would put one person's #1 between another's #1 and #2) and the ones applied for or dropped. */
export function splitWishes(wishes: Wish[]) {
  const open = wishes.filter((w) => w.status === "wanted" || w.status === "ready");
  const owners = [...new Set(open.map((w) => w.owner))];
  return {
    open: owners.flatMap((o) => open.filter((w) => w.owner === o)),
    closed: wishes.filter((w) => w.status === "applied" || w.status === "dropped"),
  };
}

/** Move a planned item up or down among its person's open items; returns the priorities to save (only the ones that
 * change), numbering them 1, 2, 3… in the new order. */
export function reorder(open: Wish[], id: number, dir: -1 | 1): { id: number; priority: number }[] {
  const w = open.find((x) => x.id === id);
  if (!w) return [];
  const list = open.filter((x) => x.owner === w.owner);
  const from = list.findIndex((x) => x.id === id), to = from + dir;
  if (to < 0 || to >= list.length) return [];
  const next = [...list];
  [next[from], next[to]] = [next[to], next[from]];
  return next.flatMap((x, i) => (x.priority === i + 1 ? [] : [{ id: x.id, priority: i + 1 }]));
}

export interface BenefitRow { card: ChurnCard; b: Benefit }
interface BenefitBoard {
  expiring: BenefitRow[];     // money left and the period ends soon, soonest first
  available: BenefitRow[];    // not used up yet, not expiring soon
  used: BenefitRow[];         // all of a credit used, or a one-off (a free night) used, this period
  perks: BenefitRow[];        // access and status, on all year: lounges, free bags, hotel status
  left: number;               // dollars of credits still unused this period
  usedAmount: number;         // dollars of credits used this period
  value: number;              // a year, the benefits that count
}

/** The benefits of these cards (open ones, the benefits still active) sorted into expiring soon, still to use and used this
 * period, and perks that are simply on, with the dollars behind each figure. */
export function benefitBoard(cards: ChurnCard[]): BenefitBoard {
  const rows = cards.filter((c) => c.status === "open").flatMap((card) => card.benefits.filter((b) => b.active).map((b) => ({ card, b })));
  const byName = (x: BenefitRow, y: BenefitRow) => x.card.product.localeCompare(y.card.product) || x.b.name.localeCompare(y.b.name);
  const board: BenefitBoard = { expiring: [], available: [], used: [], perks: [], left: 0, usedAmount: 0, value: 0 };
  for (const r of rows) {
    if (r.b.kind === "credit" && r.b.amount != null) { board.left += r.b.remaining ?? 0; board.usedAmount += r.b.used ?? 0; }
    if (r.b.counts) board.value += r.b.value_per_year;
    (r.b.expiring ? board.expiring : isPerk(r.b) ? board.perks : canUse(r.b) ? board.available : board.used).push(r);
  }
  board.expiring.sort((x, y) => (x.b.days_left ?? 0) - (y.b.days_left ?? 0) || byName(x, y));
  board.available.sort(byName);
  board.used.sort(byName);
  board.perks.sort((x, y) => x.card.product.localeCompare(y.card.product) || benefitOrder(x.b, y.b));   // a card's lounges, then its status
  return board;
}

// What each collapsed part of the card form says it holds (see Section.svelte).
const num = (x: unknown) => (x == null || x === "" ? 0 : Number(x));

export function ratesSummary(rows: unknown[], currencyName: string, currencyKey: string, earnNote: string, base: string | number | null): string {
  const parts = [];
  if (rows.length) parts.push(`${rows.length} ${rows.length === 1 ? "rate" : "rates"}`);
  if (currencyKey !== "cash") parts.push(currencyName);
  if (earnNote.trim()) parts.push(earnNote.trim());
  return parts.length ? parts.join(" · ") : `${num(base) || 1}x on everything`;
}

/** "60k Ultimate Rewards after $4,000 in 3 months"; "None" without a bonus. */
export function bonusSummary(bonus: string | number | null, spend: string | number | null, months: string | number | null, currencyKey: string, currencyName: string): string {
  if (!num(bonus) && !num(spend)) return "None";
  const got = num(bonus) ? bonusLabel(num(bonus), currencyKey, currencyName) : "Bonus";
  return num(spend) ? `${got} after ${fmt0(num(spend))}${num(months) ? ` in ${num(months)} months` : ""}` : got;
}

export const benefitsSummary = (n: number) => (n ? `${n} ${n === 1 ? "benefit" : "benefits"}` : "None");

const join = (parts: unknown[]) => parts.filter(Boolean).join(" · ") || "Nothing added";
type Fields = Record<string, string | boolean>;

/** The bank bonus form's Requirements: "$500 in direct deposits · 2 debit purchases · 90 days". */
export const bankRequirementsSummary = (v: Fields) => join([
  num(v.dd_total) && `${fmt0(num(v.dd_total))} in direct deposits`, num(v.dd_count) && plural(num(v.dd_count), "deposit"),
  num(v.debit_count) && plural(num(v.debit_count), "debit purchase"), num(v.min_balance) && `${fmt0(num(v.min_balance))} balance`,
  num(v.deadline_days) && `${num(v.deadline_days)} days`, String(v.other_reqs).trim() && "Other requirement",
]);
/** Fees and closing: "$15/month fee · keep open 180 days". */
export const bankFeesSummary = (v: Fields) => join([
  num(v.monthly_fee) && `${fmt0(num(v.monthly_fee))}/month fee`, num(v.early_close_fee) && `${fmt0(num(v.early_close_fee))} early-closing fee`,
  num(v.keep_open_days) && `keep open ${num(v.keep_open_days)} days`,
]);
/** Bonus received, and again: "Posted Oct 5 · again after 12 months". */
export const bankReceivedSummary = (v: Fields) => join([
  v.received_on && `Posted ${fullDate(String(v.received_on))}`, v.closed_on && `Closed ${fullDate(String(v.closed_on))}`,
  v.once_per_lifetime ? "once per lifetime" : num(v.repeat_months) && `again after ${plural(num(v.repeat_months), "month")}`,
  v.eligible_on && `eligible ${fullDate(String(v.eligible_on))}`,
]);
/** The planning form's "What you expect": "$95 fee · 60k after $4,000 in 3 months" (a card) or "$300 · Has requirements" (a bank bonus). */
export function wishExpectSummary(v: Fields, currencyName: string): string {
  const card = v.kind === "card";
  const got = num(v.bonus) ? (card ? bonusLabel(num(v.bonus), String(v.currency), currencyName) : fmt0(num(v.bonus))) : "";
  return join(card
    ? [v.annual_fee !== "" && v.annual_fee != null && `${fmt0(num(v.annual_fee))} fee`, got && (num(v.bonus_spend) ? `${got} after ${fmt0(num(v.bonus_spend))}${num(v.bonus_months) ? ` in ${num(v.bonus_months)} months` : ""}` : got)]
    : [got, String(v.requirements).trim() && "Has requirements", v.once_per_lifetime ? "once per lifetime" : num(v.repeat_months) && `again after ${plural(num(v.repeat_months), "month")}`]);
}
/** Timing: "Offer ends Dec 1 · wait until Jan 5 · score 740". */
export const wishTimingSummary = (v: Fields) => join([
  v.offer_expires_on && `Offer ends ${fullDate(String(v.offer_expires_on))}`, v.wait_until && `wait until ${fullDate(String(v.wait_until))}`,
  num(v.min_score) && `score ${num(v.min_score)}`, v.status === "dropped" && "dropped",
]);
