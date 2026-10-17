// What you picked on the Investments page (period, allocation tab, sort). It lives here, outside
// the page, so it survives a redraw (a sync, an edit) and coming back to the page, as in the classic app.
import type { AllocKey } from "./types";

export type SortKey = "name" | "quantity" | "price" | "value" | "day_change" | "gain" | "allocation" | "cost_basis";

/** What the phone's holdings list shows in its pill: today's change %, total gain %, or share of the portfolio. */
export type HoldView = "day" | "total" | "weight";

export const inv = $state({
  period: "1Y",
  allocTab: "asset_class" as AllocKey,
  sort: { key: "value" as SortKey, dir: -1 },
  holdView: "day" as HoldView,
  /** Where the page was scrolled to when you opened a holding's page, so coming back puts it there (0: nowhere to go back to). */
  scrollY: 0,
  /** The phone list: by value, or by the shown figure after a second tap on the switch's active choice. */
  listSort: { by: "value" as "value" | "figure", dir: -1 },
});
