// Overview's data, asked for while Runway's state is still on its way. Overview needs the horizon from /api/state to
// say how many days it wants, but asking for /api/overview without `days` makes the server use the same horizon it
// would have told the state (api_overview), so the two requests don't have to wait for each other. The reply's own
// length says what horizon it used, and Overview only uses it when that is the horizon it was going to ask for;
// anything else is fetched again as before.
import { api, currentPage } from "./api";
import { route } from "./app.svelte";
import type { Overview } from "./types";

/** How long an early answer may wait for the page that wants it: past this (a slow or retried boot), ask again. */
export const MAX_AGE_MS = 30_000;

let early: { at: number; page: number; reply: Promise<Overview> } | null = null;

/** Start Overview's request now, if Overview is the page being opened. Call once, before the state is asked for. */
export function prefetchOverview(): void {
  if (early || route.page !== "overview") return;
  const reply = api<Overview>("/api/overview");
  early = { at: Date.now(), page: currentPage(), reply };
  // Nobody may ever ask for it (another page opens first): that is not an error to report. Overview, if it does use
  // the reply, gets the rejection itself, from `reply`.
  reply.catch(() => { /* reported by whoever uses the reply, if anyone does */ });
}

/** The early answer, once and only if it is still fresh and its page is still the one being read (moving to another
 *  page cancels a read, and a cancelled read never answers); null when there isn't one (ask the usual way). */
export function takeEarlyOverview(): Promise<Overview> | null {
  const e = early;
  early = null;
  return e && e.page === currentPage() && Date.now() - e.at <= MAX_AGE_MS ? e.reply : null;
}

/** An Overview of `days` days: the early answer if it covers exactly that horizon, else a request for it. */
export function overviewFor(days: number, early: Promise<Overview> | null = null): Promise<Overview> {
  const ask = () => api<Overview>(`/api/overview?days=${days}`);
  if (!early) return ask();
  return early.then((fc) => (fc.dates.length - 1 === days ? fc : ask()), ask);   // a failed early request is asked again
}
