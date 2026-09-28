// What the whole app shares: Runway's state (/api/state), the current page, and syncing when you open Runway.
import { api, newPage } from "./api";
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

/** Load the page's data again (after a change, or when a sync brings new data). */
export function reload(): void {
  newPage();
  app.version++;
}

// ------------------------------------------------------------------------------------------ routing
// Hash routes, as in the classic app: #overview, #setup/connections, ...
export const route = $state({ page: "overview", sub: "" as string });

function readHash(): void {
  let [page, sub = ""] = (location.hash || "#overview").slice(1).split("?")[0].split("/");
  if (page === "settings") page = "setup";
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
// Opening Runway (or coming back to its tab) syncs in the background when the data is more than an hour old;
// the server decides, so this is cheap to call. When the sync finishes, the page loads again with the new data.
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
export async function boot(): Promise<void> {
  try { await refreshState(); app.bootError = ""; }
  catch (err) { console.error(err); app.bootError = (err as Error).message; return; }
  if (booted) return;
  booted = true;
  syncOnVisit();
}
window.addEventListener("online", () => { if (!booted) boot(); });
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && booted) syncOnVisit(); });
setInterval(() => (booted ? refreshState().catch((err) => console.error(err)) : boot()), 60_000);
