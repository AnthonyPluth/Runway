// What's coming up, for Overview's list and Transactions' Upcoming: the forecast's events and churning cards' annual
// fees (charges on cards, which reach cash through the card's statement payment), by date and then amount, as the
// forecast orders its events.
import type { ForecastEvent, Overview } from "$lib/types";

export const comingUp = (fc: Pick<Overview, "events" | "fees">): ForecastEvent[] =>
  [...fc.events, ...(fc.fees ?? [])].sort((a, b) => a.date.localeCompare(b.date) || a.amount - b.amount);
