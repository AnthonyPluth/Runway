// What you picked on the Investments page (period, allocation tab, sort). It lives here, outside
// the page, so it survives a redraw (a sync, an edit) and coming back to the page, as in the classic app.
import type { AllocKey } from "./types";

export type SortKey = "name" | "quantity" | "price" | "value" | "day_change" | "gain" | "allocation" | "cost_basis";

export const inv = $state({
  period: "1Y",
  allocTab: "asset_class" as AllocKey,
  sort: { key: "value" as SortKey, dir: -1 },
});
