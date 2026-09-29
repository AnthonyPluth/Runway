// Where you can go (the sidebar on a computer, the tab bar on a phone), and what both say about you and syncing.
import { api } from "./api";
import { route } from "./app.svelte";
import type { AppState } from "./types";
import { toast } from "svelte-sonner";
import ChartColumn from "@lucide/svelte/icons/chart-column";
import ChartPie from "@lucide/svelte/icons/chart-pie";
import CreditCard from "@lucide/svelte/icons/credit-card";
import House from "@lucide/svelte/icons/house";
import Landmark from "@lucide/svelte/icons/landmark";
import List from "@lucide/svelte/icons/list";
import Repeat from "@lucide/svelte/icons/repeat";
import TrendingUp from "@lucide/svelte/icons/trending-up";

export interface NavItem { page: string; label: string; icon: typeof House }

export const MAIN_NAV: NavItem[] = [
  { page: "overview", label: "Overview", icon: House },
  { page: "transactions", label: "Transactions", icon: List },
  { page: "budget", label: "Budget", icon: ChartPie },
  { page: "recurring", label: "Recurring", icon: Repeat },
  { page: "reports", label: "Reports", icon: ChartColumn },
];
export const MONEY_NAV: NavItem[] = [
  { page: "investments", label: "Investments", icon: TrendingUp },
  { page: "networth", label: "Net worth", icon: Landmark },
  { page: "churning", label: "Churning", icon: CreditCard },
];

/** The page to show as current: Review is a tab of Transactions. */
export const currentPage = () => (route.page === "review" ? "transactions" : route.page);

export const signedInUser = (s: AppState | null) => (s?.user && !s.user.local ? s.user : null);

/** How fresh the data is; sync runs on its own (daily, and when you open Runway). */
export function syncStatus(s: AppState | null): { text: string; tone: "" | "busy" | "bad"; title: string } {
  if (!s) return { text: "", tone: "", title: "" };
  if (s.syncing) return { text: "Syncing…", tone: "busy", title: "" };
  if (s.last_log && !s.last_log.ok) return { text: "Last sync failed", tone: "bad", title: s.last_log.message ?? "" };
  if (s.last_sync_ok) {
    const t = new Date(s.last_sync_ok), today = new Date().toDateString() === t.toDateString();
    return { text: "Up to date · " + (today ? t.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })
      : t.toLocaleDateString("en-US", { month: "short", day: "numeric" })), tone: "", title: "Last synced " + t.toLocaleString() };
  }
  return s.connected ? { text: "Not synced yet", tone: "busy", title: "" } : { text: "Bank not connected", tone: "bad", title: "" };
}

/** Signing out is a POST (so no other site can sign you out with a link); then on to the provider's sign-out page. */
export async function signOut(e: Event): Promise<void> {
  e.preventDefault();
  try {
    const r = await api<{ redirect?: string }>("/auth/logout", { method: "POST" });
    location.href = r.redirect || "/auth/signed-out";
  } catch (err) { toast.error((err as Error).message); }
}
