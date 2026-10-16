// The Overview's Investments card: live prices re-price its total and today's change, as they do on Investments.
import type { InvestmentsSummary } from "$lib/api-types";
import type { Quote } from "$lib/components/investments/types";

/** The summary as of `quotes` (latest quote per ticker): positions with a quote are re-valued at its price, with
 *  today's change from its previous close; the total and the change are added up again. The movers and the trend are
 *  the load's. `base` is never changed, so the quotes can be applied to it again as more arrive. */
export function applyLive(base: InvestmentsSummary, quotes: Record<string, Quote>, market: string): InvestmentsSummary {
  const priced = base.positions.map((p) => {
    const q = quotes[p.ticker];
    if (!q || !p.quantity) return p;
    return { ...p, value: p.quantity * q.price, day_change: q.prev_close ? p.quantity * (q.price - q.prev_close) : p.day_change };
  });
  // What isn't a position (cash) stays as it was.
  const rest = base.total - base.positions.reduce((a, p) => a + p.value, 0);
  const total = rest + priced.reduce((a, p) => a + p.value, 0);
  const moved = priced.filter((p): p is typeof p & { day_change: number } => p.day_change != null);
  const day = moved.reduce((a, p) => a + p.day_change, 0);
  const prev = moved.reduce((a, p) => a + p.value - p.day_change, 0);
  return {
    ...base, total, positions: priced, market: market === "open" ? "open" : "closed",
    day_change: moved.length ? day : null, day_change_pct: moved.length && prev > 0 ? day / prev : null,
    trend: base.trend.length ? [...base.trend.slice(0, -1), total] : base.trend,
  };
}
