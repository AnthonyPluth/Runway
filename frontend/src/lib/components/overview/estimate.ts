import { fmt, fmtDate } from "$lib/format";
import type { StatementEstimate } from "$lib/types";

// What an estimated card statement is made of, one line per part, for Coming up's asterisk (its tooltip, and the lines a
// tap shows under the row): the heading, then each part, then the sum, which is the event's amount
// (forecast.estimate_parts rounds the parts so they add up to it to the cent).

/** A part: "Charged so far · $1,204.10", or with what it's made of: "… · $980.00 (Groceries $600.00, …)". `sum` marks
 *  the last line(s): what it comes to, and what's paid of it when that's less. */
export interface EstimateLine { label: string; value: string; detail?: string; sum?: boolean }

const SHOWN = 3;   // named items listed in a part's detail before "…", when there are more than one more

/** Money with a real minus, for a credit. */
const money = (v: number) => (Math.round(v * 100) < 0 ? `−${fmt(-v)}` : fmt(v));

function named(items: { label: string; amount: number }[]): string {
  const cut = items.length > SHOWN + 1;   // "…" in place of a single item would hide it for nothing
  const list = items.slice(0, cut ? SHOWN : undefined).map((i) => `${i.label} ${fmt(i.amount)}`).join(", ");
  return cut ? `${list}, …` : list;
}

/** A part made of named items: one item is the line itself ("Insurance · $600.00"); several are listed in its detail. */
function items(many: string, list: { name: string; amount: number }[] | undefined, total: number | undefined): EstimateLine[] {
  if (!list?.length || total == null) return [];
  if (list.length === 1) return [{ label: list[0].name, value: fmt(total) }];
  return [{ label: many, value: fmt(total), detail: named(list.map((i) => ({ label: i.name, amount: i.amount }))) }];
}

export const estimateHeading = (est: StatementEstimate) => est.assumed_cycle
  ? `Estimate for a statement closing ${fmtDate(est.close)} (none from the bank yet)`
  : `Estimate for the ${fmtDate(est.close)} statement`;

export function estimateLines(est: StatementEstimate): EstimateLine[] {
  const out: EstimateLine[] = [];
  if (est.charged_so_far != null) out.push({ label: "Charged so far", value: fmt(est.charged_so_far) });
  if (est.owed_now != null) out.push({ label: est.owed_now < 0 ? "Credit on the card now" : "Owed on the card now", value: money(est.owed_now) });
  if (est.budgets?.length && est.budgets_total != null) {
    const label = `Budgets on this card to ${fmtDate(est.close)}`;
    out.push(est.budgets.length === 1
      ? { label, value: fmt(est.budgets_total), detail: est.budgets[0].category }
      : { label, value: fmt(est.budgets_total), detail: named(est.budgets.map((b) => ({ label: b.category, amount: b.amount }))) });
  }
  out.push(...items("Recurring charges", est.recurring, est.recurring_total));
  out.push(...items("Annual fees", est.fees, est.fees_total));
  if (est.carried != null) {
    out.push({ label: est.carried < 0 ? "Credit from the last statement" : "Carried from the last statement", value: money(est.carried) });
  }
  if (est.interest != null) out.push({ label: "Interest", value: fmt(est.interest), detail: est.apr != null ? `${est.apr}% APR` : undefined });
  out.push({ label: "", value: `= ${fmt(est.statement)}`, sum: true });
  if (Math.round(est.total * 100) !== Math.round(est.statement * 100)) {
    out.push({ label: est.pay_mode === "fixed" ? "Paying your fixed amount" : "Paying the minimum", value: fmt(est.total),
      detail: "the rest carries over", sum: true });
  }
  return out;
}

/** A line as one line of text ("label · value (detail)"), for a `title`. */
export const estimateText = (l: EstimateLine) => (l.label ? `${l.label} · ` : "") + l.value + (l.detail ? ` (${l.detail})` : "");

/** The whole breakdown as a multi-line tooltip: the heading, then a line per part. */
export const estimateTitle = (est: StatementEstimate) => [estimateHeading(est), ...estimateLines(est).map(estimateText)].join("\n");
