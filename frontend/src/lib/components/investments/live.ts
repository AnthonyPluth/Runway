// Live prices, streamed from the server while the Investments page is open and visible: an update whenever a
// holding's price moves. With the market closed the server sends the latest prices and the browser checks back
// every few minutes. Browsers (or proxies) that can't stream fall back to asking every 30 seconds.
import { api } from "$lib/api";
import type { Holding, Investments, LiveQuotes, Quote } from "./types";

// Streams that failed before sending anything; after 3 we poll instead (for as long as Runway is open).
let failures = 0;

/** Starts live prices; call the function it returns to stop them (when you leave the page). */
export function livePrices(onQuotes: (live: LiveQuotes) => void): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let source: EventSource | null = null;
  let stopped = false;

  const tick = async () => {
    timer = null;
    if (stopped) return;
    let next = 30_000;
    if (!document.hidden) {
      try {
        const live = await api<LiveQuotes>("/api/investments/live");
        if (stopped) return;
        onQuotes(live);
        if (live.market !== "open") next = 300_000;
      } catch { /* keep the last numbers; try again next time */ }
    }
    if (!stopped) timer = setTimeout(tick, next);
  };
  const halt = () => {
    if (timer) clearTimeout(timer);
    timer = null;
    source?.close(); source = null;
  };
  const start = () => {
    if (stopped || document.hidden || source || timer) return;
    if (!window.EventSource || failures >= 3) { void tick(); return; }
    const src = (source = new EventSource("/api/investments/stream"));
    let got = false;
    src.addEventListener("quotes", (e) => { got = true; failures = 0; if (!stopped) onQuotes(JSON.parse((e as MessageEvent).data)); });
    // The server ends each stream on purpose (market closed, or after a while) and the browser reconnects on its own.
    // A refused stream (signed out, a proxy in the way) or one that keeps failing before sending anything: poll instead.
    src.onerror = () => {
      if (src.readyState === EventSource.CLOSED) failures = 3;
      else if (got) return;
      else failures++;
      if (failures >= 3) { halt(); start(); }
    };
  };
  // Hidden tabs don't hold a stream open; it picks up again (with a fresh snapshot) when you come back.
  const onVisible = () => {
    if (document.hidden) { source?.close(); source = null; } else start();
  };
  document.addEventListener("visibilitychange", onVisible);
  start();
  return () => { stopped = true; halt(); document.removeEventListener("visibilitychange", onVisible); };
}

/** Re-price holdings with live quotes and recompute the page totals. */
export function applyLiveQuotes(d: Investments, quotes: Record<string, Quote>, market: string): void {
  const now = Date.now() / 1000;
  for (const h of d.holdings) {
    const q = h.ticker ? quotes[h.ticker] : undefined;
    h.live = false;
    if (!q || h.is_cash || !h.quantity) continue;
    // "Live" = trading now and quoted in the last 20 minutes. Mutual funds only get one price a day, after the close.
    h.live = market === "open" && (q.type || "").toUpperCase() !== "MUTUALFUND" && !!q.time && now - q.time < 1200;
    h.live_time = q.time ?? undefined;
    h.price = q.price;
    h.value = h.quantity * q.price;
    if (q.prev_close) {
      h.day_change = h.quantity * (q.price - q.prev_close);
      h.day_change_pct = q.price / q.prev_close - 1;
    }
    if (h.gain != null && h.cost_basis) {
      h.gain = h.value - h.cost_basis;
      h.gain_pct = h.gain / h.cost_basis;
    }
  }
  d.total = d.holdings.reduce((a, h) => a + h.value, 0);
  for (const h of d.holdings) h.allocation = d.total ? h.value / d.total : 0;
  const moved = d.holdings.filter((h): h is Holding & { day_change: number } => h.day_change != null);
  d.day_change = moved.length ? moved.reduce((a, h) => a + h.day_change, 0) : null;
  const prev = moved.reduce((a, h) => a + h.value - h.day_change, 0);
  d.day_change_pct = d.day_change != null && prev > 0 ? d.day_change / prev : null;
  const known = d.holdings.filter((h) => h.gain != null);
  if (known.length) {
    d.unrealized_gain = known.reduce((a, h) => a + (h.gain ?? 0), 0);
    d.cost_basis = known.reduce((a, h) => a + h.cost_basis, 0);
  }
}
