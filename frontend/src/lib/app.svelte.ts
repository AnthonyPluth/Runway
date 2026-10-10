// What the whole app shares: Runway's state (/api/state), the current page, and syncing when you open Runway.
import { api, newPage } from "./api";
import { loadCategories } from "./categories.svelte";
import { startMonitoring } from "./monitoring";
import { toast } from "svelte-sonner";
import type { AppState } from "./types";
import { errMsg } from "./act";
import { afterUnlock, isLocked } from "./lock.svelte";

export const app = $state({
  state: null as AppState | null,
  bootError: "" as string,
  /** Bumped when the page should load again (a sync finished, an amount changed). */
  version: 0,
  /** Signed out while the page was open: App shows a Sign in banner, and Runway stops checking in until you do. */
  sessionExpired: false,
});

/** background: Runway checking in on its own (see api's `background`), rather than for something you did. */
export async function refreshState(background = false): Promise<void> {
  app.state = await api<AppState>("/api/state", background ? { keep: true, background } : { keep: true });
}

/** Load the page's data again (after a change, or when a sync brings new data). The page is drawn afresh, so this
 *  also puts you back where you were on it (see keepScroll). */
export function reload(): void {
  const y = window.scrollY;
  newPage();
  app.version++;
  keepScroll(y);
}

// Drawing a page afresh starts it at the top, and its data arrives a moment later, so the page grows back to its
// length over a few frames. Keep scrolling back to where you were (or as near as the page allows) while it settles,
// until you scroll or type yourself.
let stopKeeping: (() => void) | null = null;
export function keepScroll(y: number): void {
  stopKeeping?.();
  if (y <= 0) return;
  let done = false;
  const until = performance.now() + 2000;
  const stop = () => {
    done = true; stopKeeping = null;
    for (const ev of ["wheel", "touchstart", "keydown", "mousedown"]) window.removeEventListener(ev, stop);
  };
  for (const ev of ["wheel", "touchstart", "keydown", "mousedown"]) window.addEventListener(ev, stop, { passive: true });
  stopKeeping = stop;
  const frame = () => {
    if (done) return;
    // as close as the page allows (it may have come back a little shorter, e.g. without the bar for selected rows)
    const to = Math.min(y, document.documentElement.scrollHeight - window.innerHeight);
    if (Math.abs(window.scrollY - to) > 1) window.scrollTo(0, to);
    if (performance.now() < until) requestAnimationFrame(frame); else stop();
  };
  requestAnimationFrame(frame);
}

// ------------------------------------------------------------------------------------------ routing
// Hash routes, as in the classic app: #overview, #setup/connections, ...
// `query`: what follows "?" (Transactions keeps its filters there).
export const route = $state({ page: "overview", sub: "" as string, query: "" as string });

// Old routes that moved (#investments is Net worth's Investments tab; #budget/recurring was Bills & income, now the
// Recurring page): they resolve to the new route here, so everything downstream (the page, the nav highlight) sees only
// the current routes.
const MOVED: Record<string, [page: string, sub: string]> = { investments: ["networth", "investments"] };

function readHash(): void {
  const [path, query = ""] = (location.hash || "#overview").slice(1).split(/\?(.*)/s);
  const [name, rawSub = ""] = path.split("/");
  const [page, sub] = name === "budget" && rawSub === "recurring" ? ["recurring", ""]
    : MOVED[name] ?? [name === "settings" ? "setup" : name, rawSub];
  if ((page || "overview") !== route.page) newPage();   // a tab inside the same page keeps its data and place
  route.page = page || "overview";
  route.sub = sub;
  route.query = query;
}

/** Put this query in the address (`#page/sub?query`) without a new history entry or a hashchange: a page's own
 *  filters, as they change. */
export function setQuery(query: string): void {
  if (query === route.query) return;
  const path = (location.hash || "#overview").split("?")[0];
  history.replaceState(history.state, "", `${location.pathname}${location.search}${path}${query ? `?${query}` : ""}`);
  route.query = query;
}
readHash();
window.addEventListener("hashchange", readHash);

// ------------------------------------------------------------------------------------------ editors
// True while you're typing or have an editor open, when redrawing the page would throw away what you've entered.
export function editing(): boolean {
  const f = document.activeElement;
  return !!(f && ["INPUT", "TEXTAREA", "SELECT"].includes(f.tagName)) || !!document.querySelector("[data-editor]");
}
// A session that expires while you're editing, or while Runway is only checking in, mustn't send you off to sign in
// (see lib/api.ts): App shows a Sign in banner instead.
window.addEventListener("runway:signed-out", (e) => {
  if (!e.detail.background && !editing()) return;
  e.preventDefault();
  app.sessionExpired = true;
});

// ------------------------------------------------------------------------------------------ boot and sync
// Opening Runway (or coming back to its tab) asks the server to catch up: a missed daily bank sync, or investments
// more than an hour old. The server decides, so this is cheap to call. When the sync finishes, the page loads again with the new data.
let syncWatch: ReturnType<typeof setInterval> | null = null;
export async function syncOnVisit(): Promise<void> {
  try {
    const r = await api<{ started: boolean }>("/api/sync/auto", { method: "POST", background: true });
    if (r.started) watchSync();
  } catch (err) { if (!isLocked()) console.error(err); }
}

/** Watch a sync that's running on the server until it ends, then say how it went and load the page again. */
function watchSync(sayFailure = false): void {
  if (syncWatch) return;
  if (app.state) app.state.syncing = true;
  const before = app.state?.last_sync_ok;
  const beforeLog = app.state?.last_log?.at;
  syncWatch = setInterval(async () => {
    try { await refreshState(true); }
    catch (err) {   // keep checking through a blip, but not once signed out (or locked: the unlock loads it all again)
      if (isLocked()) { clearInterval(syncWatch!); syncWatch = null; return; }
      console.error(err);
      if (app.sessionExpired) { clearInterval(syncWatch!); syncWatch = null; }
      return;
    }
    if (app.state?.syncing) return;
    clearInterval(syncWatch!); syncWatch = null;
    const synced = app.state?.last_sync_ok !== before && app.state?.last_log?.ok;
    // A sync you asked for that failed says so (the one on opening Runway only shows it in the sidebar).
    const log = app.state?.last_log;
    if (sayFailure && !synced && log && !log.ok && log.at !== beforeLog) toast.error(log.message || "The sync failed.");
    if (!editing()) { if (synced) toast.success(`Synced · ${app.state?.last_log?.message ?? ""}`); reload(); }
    else if (synced) toast(`Synced · ${app.state?.last_log?.message ?? ""}. Change page to see the new data.`);
  }, 3000);
}

/** The Sync button: sync the banks now (SimpleFIN and Plaid), say how it went,
 *  and load the page again. A sync that's already running (the daily one, or another tab's) is watched instead. */
export async function syncNow(): Promise<void> {
  if (app.state?.syncing) return;
  if (app.state) app.state.syncing = true;
  try {
    const r = await api<{ new: number; bank_messages?: string[] }>("/api/sync", { method: "POST" });
    await refreshState().catch(console.error);
    const done = `Synced · ${r.new} new ${r.new === 1 ? "transaction" : "transactions"}`;
    // While you're typing in a form the page isn't reloaded under you, so it says the data shown is older.
    if (!editing()) { toast.success(done); reload(); } else toast.success(`${done}. Change page to see the new data.`);
    if (r.bank_messages?.length) toast.warning(r.bank_messages.join("; "));
  } catch (err) {
    if ((err as { status?: number }).status === 409) { toast("A sync is already running. Runway will show the result when it's done."); watchSync(true); return; }
    if (app.state) app.state.syncing = false;
    toast.error(errMsg(err));
    await refreshState().catch(console.error);   // the failure is in the log now, and the sidebar says so
    // A proxy can give up on a long sync (a 504) while it carries on: still running, so watch it to the end.
    if (app.state?.syncing) watchSync(true);
  }
}

// If Runway can't be reached when the app opens (offline, or the server is restarting), say so and keep trying
// every minute (and when you're back online) instead of leaving a blank page.
let booted = false;
const onBoot: (() => void)[] = [];
/** Run `fn` once Runway has answered for the first time (straight away if it already has). */
export function whenBooted(fn: () => void): void { if (booted) fn(); else onBoot.push(fn); }
export async function boot(): Promise<void> {
  try { await refreshState(); app.bootError = ""; }
  catch (err) { console.error(err); app.bootError = errMsg(err); return; }
  if (booted) return;
  booted = true;
  startMonitoring(app.state?.sentry).catch((err) => console.error(err));   // error reports, if Runway sends them
  loadCategories().catch((err) => console.error(err));   // every page shows categories' emoji and colors
  onBoot.splice(0).forEach((fn) => fn());
  syncOnVisit();
}
// While the app lock is locked (lib/lock.svelte.ts) Runway is asked for nothing: it would refuse anyway (423), and the
// first boot waits for the unlock (main.ts).
window.addEventListener("online", () => { if (!booted && !isLocked()) boot(); });
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && booted && !app.sessionExpired && !isLocked()) syncOnVisit();
});
// Unlocked again: the page is drawn afresh (App.svelte), and the state catches up on what happened while it was locked.
afterUnlock(() => {
  if (!booted) { boot(); return; }   // it locked before Runway first answered (the device had forgotten the lock: a 423)
  refreshState(true).then(() => { if (app.state?.syncing) watchSync(); }).catch((err) => console.error(err));
});
/** Every minute: pick up what changed meanwhile (or try to boot again, if Runway hasn't answered yet). */
export function checkIn(): void {
  if (app.sessionExpired || isLocked()) return;   // nothing more to ask until you've signed in again, or unlocked
  if (booted) refreshState(true).catch((err) => { if (!isLocked()) console.error(err); }); else boot();
}
setInterval(checkIn, 60_000);
