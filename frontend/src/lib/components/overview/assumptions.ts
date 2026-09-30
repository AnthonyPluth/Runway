// What the forecast counts, in a sentence under Overview's verdict: "Includes 7 paychecks and bills and 2 card payments
// (1 estimated). Everyday spending isn’t included", and what the link after it says ("Turn on" or "Change").
import { fmt, fmt0, plural } from "$lib/format";
import type { Overview } from "$lib/types";

/** "about $42 a day" (cents under $10, where they matter). */
export const perDay = (v: number) => `about ${v >= 10 ? fmt0(v) : fmt(v)} a day`;

const list = (xs: string[]) => (xs.length < 3 ? xs.join(" and ") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

export function assumptions(fc: Pick<Overview, "events" | "accounts">): { text: string; action: string } {
  const rec = fc.events.filter((e) => e.kind === "recurring");
  const income = rec.filter((e) => e.amount > 0).length;
  const cards = fc.events.filter((e) => e.kind === "card");
  const estimated = cards.filter((e) => e.estimated).length;
  const on = fc.accounts.filter((a) => a.daily_spend_on);

  const parts: string[] = [];
  if (rec.length)
    parts.push(income === rec.length ? plural(income, "paycheck") : !income ? plural(rec.length, "bill")
      : `${rec.length} paychecks and bills`);
  if (cards.length)
    parts.push(plural(cards.length, "card payment") + (estimated === cards.length ? " (estimated)" : estimated ? ` (${estimated} estimated)` : ""));
  if (on.length && on.length === fc.accounts.length)
    parts.push(`everyday spending of ${perDay(on.reduce((s, a) => s + (a.daily_spend ?? 0), 0))}`);

  const text = parts.length ? `Includes ${list(parts)}` : "Nothing scheduled in this range yet";
  if (!on.length) return { text: `${text}. Everyday spending isn’t included`, action: "Turn on" };
  if (on.length < fc.accounts.length) return { text: `${text}. Everyday spending is taken out of ${list(on.map((a) => a.name))} only`, action: "Change" };
  return { text, action: "Change" };
}
