// Where you can go (the sidebar on a computer, the tab bar on a phone), and what both say about you and syncing.
import { api } from "./api";
import { route } from "./app.svelte";
import { isoDay, parseDate, relDay } from "./format";
import type { AppState } from "./types";
import { toast } from "svelte-sonner";
import ChartColumn from "@lucide/svelte/icons/chart-column";
import ChartPie from "@lucide/svelte/icons/chart-pie";
import CreditCard from "@lucide/svelte/icons/credit-card";
import House from "@lucide/svelte/icons/house";
import Landmark from "@lucide/svelte/icons/landmark";
import Repeat from "@lucide/svelte/icons/repeat";
import List from "@lucide/svelte/icons/list";

export interface NavItem { page: string; label: string; icon: typeof House }

export const MAIN_NAV: NavItem[] = [
  { page: "overview", label: "Overview", icon: House },
  { page: "transactions", label: "Transactions", icon: List },
  { page: "budget", label: "Budget", icon: ChartPie },
  { page: "recurring", label: "Recurring", icon: Repeat },
  { page: "reports", label: "Reports", icon: ChartColumn },
];
export const MONEY_NAV: NavItem[] = [
  { page: "networth", label: "Net worth", icon: Landmark },
  { page: "churning", label: "Churning", icon: CreditCard },
];

/** The nav item to light up: Review is a tab of Transactions (Investments resolves to its hub in the router). */
export const currentPage = () => (route.page === "review" ? "transactions" : route.page);

export const signedInUser = (s: AppState | null) => (s?.user && !s.user.local ? s.user : null);

/** After this long without a good sync, the data is called out as old (a day, and some slack for a late sync). */
export const STALE_HOURS = 26;

type SyncTone = "" | "busy" | "warn" | "bad";
/** The sync line: its text and tone, a visible second line (`detail`), the full story on hover (`title`), and where to
 * go about a problem (`href`, only when there is one). */
interface SyncStatus { text: string; tone: SyncTone; title: string; detail: string; href: string }

const FIX = "#setup/connections";
const time = (t: Date) => t.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
const day = (t: Date) => t.toLocaleDateString("en-US", { month: "short", day: "numeric" });
/** Whole calendar days (in the browser's time zone) from `t` to `now`. */
const daysBetween = (t: Date, now: Date) =>
  Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
    - new Date(t.getFullYear(), t.getMonth(), t.getDate()).getTime()) / 864e5);

/** The dot beside the sync line, by tone. */
export const syncDot = (tone: SyncTone) =>
  ({ "": "bg-emerald-500", busy: "animate-pulse bg-muted-foreground", warn: "bg-amber-500", bad: "bg-destructive" })[tone];

/** How fresh the data is; sync runs on its own (daily, and when you open Runway). A sync that worked can still leave a
 * bank needing you (an expired login), and a good sync days ago isn't "up to date": both show amber. */
export function syncStatus(s: AppState | null, now: Date = new Date()): SyncStatus {
  const none = { title: "", detail: "", href: "" };
  if (!s) return { text: "", tone: "", ...none };
  if (s.syncing) return { text: "Syncing…", tone: "busy", ...none };
  if (s.last_log && !s.last_log.ok) {
    const why = s.last_log.message ?? "";
    return { text: "Last sync failed", tone: "bad", title: why, detail: why, href: FIX };
  }
  if (s.last_sync_ok) {
    const t = new Date(s.last_sync_ok), warnings = s.sync_warnings ?? [];
    const when = `Last synced ${day(t)}, ${time(t)}`;
    const title = [when, ...warnings].join("\n"), detail = warnings.join("; ");
    if ((now.getTime() - t.getTime()) / 36e5 > STALE_HOURS) {
      const ago = daysBetween(t, now);
      return { text: ago <= 1 ? "Updated yesterday" : `Updated ${ago} days ago`, tone: "warn", title, detail, href: FIX };
    }
    if (warnings.length)
      return { text: `Synced · ${warnings.length} ${warnings.length === 1 ? "bank needs" : "banks need"} attention`, tone: "warn", title, detail, href: FIX };
    return { text: "Up to date · " + (daysBetween(t, now) === 0 ? time(t) : day(t)), tone: "", title: when, detail: "", href: "" };
  }
  return s.connected ? { text: "Not synced yet", tone: "busy", ...none } : { text: "Bank not connected", tone: "bad", ...none, href: FIX };
}

/** The Overview's "Balance as of": the newest day among the balances (with the sync's time when it synced that day),
 * `stale` once it's from before yesterday. Null when no account says. */
export function balanceAsOf(dates: (string | null | undefined)[], today: string, lastSync?: string | null): { text: string; stale: boolean } | null {
  const newest = dates.filter((d): d is string => !!d).sort().pop();
  if (!newest) return null;
  const d = newest > today ? today : newest, synced = lastSync ? new Date(lastSync) : null, t = parseDate(today);
  const at = synced && isoDay(synced) === d ? `, ${time(synced)}` : "";
  return { text: `Balance as of ${relDay(d, today)}${at}`, stale: d < isoDay(new Date(t.getFullYear(), t.getMonth(), t.getDate() - 1)) };
}

/** Signing out is a POST (so no other site can sign you out with a link); then on to the provider's sign-out page. */
export async function signOut(e: Event): Promise<void> {
  e.preventDefault();
  try {
    const r = await api<{ redirect?: string }>("/auth/logout", { method: "POST" });
    location.href = r.redirect || "/auth/signed-out";
  } catch (err) { toast.error((err as Error).message); }
}
