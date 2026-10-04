// What's coming up, for Overview's list and Transactions' Upcoming: the forecast's events and churning cards' annual
// fees (charges on cards, which reach cash through the card's statement payment), by date and then amount, as the
// forecast orders its events. With `charges`, the recurring charges on cards too (Transactions, which lists every
// account's: a budget's link shows what's still coming in it there); Overview's list stays what reaches cash.
import type { ForecastEvent, Overview } from "$lib/types";

export const comingUp = (fc: Pick<Overview, "events" | "fees" | "charges">, { charges = false } = {}): ForecastEvent[] =>
  [...fc.events, ...(fc.fees ?? []), ...(charges ? fc.charges ?? [] : [])].sort((a, b) => a.date.localeCompare(b.date) || a.amount - b.amount);
