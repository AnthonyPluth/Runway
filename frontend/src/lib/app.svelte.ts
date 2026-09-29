// What the whole app shares: Runway's state (/api/state), the current page, and syncing when you open Runway.
import { api, newPage } from "./api";
import { loadCategories } from "./categories.svelte";
import { startMonitoring } from "./monitoring";
import { toast } from "svelte-sonner";
import type { AppState } from "./types";

export const app = $state({
  state: null as AppState | null,
  bootError: "" as string,
  /** Bumped when the page should load again (a sync finished, an amount changed). */
  version: 0,
});

export async function refreshState(): Promise<void> {
  app.state = await api<AppState>("/api/state", { keep: true });
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
export const route = $state({ page: "overview", sub: "" as string });

function readHash(): void {
  const [name, sub = ""] = (location.hash || "#overview").slice(1).split("?")[0].split("/");
  const page = name === "settings" ? "setup" : name;
  newPage();
  route.page = page || "overview";
  route.sub = sub;
}
readHash();
window.addEventListener("hashchange", readHash);

// ------------------------------------------------------------------------------------------ editors
// True while you're typing or have an editor open, when redrawing the page would throw away what you've entered.
export function editing(): boolean {
  const f = document.activeElement;
  return !!(f && ["INPUT", "TEXTAREA", "SELECT"].includes(f.tagName)) || !!document.querySelector("[data-editor]");
}

// ------------------------------------------------------------------------------------------ boot and sync
// Opening Runway (or coming back to its tab) asks the server to catch up: a missed daily bank sync, or investments
// more than an hour old. The server decides, so this is cheap to call. When the sync finishes, the page loads again with the new data.
let syncWatch: ReturnType<typeof setInterval> | null = null;
export async function syncOnVisit(): Promise<void> {
  try {
    const r = await api<{ started: boolean }>("/api/sync/auto", { method: "POST" });
    if (!r.started || syncWatch) return;
    if (app.state) app.state.syncing = true;
    const before = app.state?.last_sync_ok;
    syncWatch = setInterval(async () => {
      await refreshState();
      if (app.state?.syncing) return;
      clearInterval(syncWatch!); syncWatch = null;
      const synced = app.state?.last_sync_ok !== before && app.state?.last_log?.ok;
      if (!editing()) { if (synced) toast.success(`Synced · ${app.state?.last_log?.message ?? ""}`); reload(); }
      else if (synced) toast(`Synced · ${app.state?.last_log?.message ?? ""}. Change page to see the new data.`);
    }, 3000);
  } catch (err) { console.error(err); }
}

// If Runway can't be reached when the app opens (offline, or the server is restarting), say so and keep trying
// every minute (and when you're back online) instead of leaving a blank page.
let booted = false;
const onBoot: (() => void)[] = [];
/** Run `fn` once Runway has answered for the first time (straight away if it already has). */
export function whenBooted(fn: () => void): void { if (booted) fn(); else onBoot.push(fn); }
export async function boot(): Promise<void> {
  try { await refreshState(); app.bootError = ""; }
  catch (err) { console.error(err); app.bootError = (err as Error).message; return; }
  if (booted) return;
  booted = true;
  startMonitoring(app.state?.sentry).catch((err) => console.error(err));   // error reports, if Runway sends them
  loadCategories().catch((err) => console.error(err));   // every page shows categories' emoji and colors
  onBoot.splice(0).forEach((fn) => fn());
  syncOnVisit();
}
window.addEventListener("online", () => { if (!booted) boot(); });
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && booted) syncOnVisit(); });
setInterval(() => (booted ? refreshState().catch((err) => console.error(err)) : boot()), 60_000);
