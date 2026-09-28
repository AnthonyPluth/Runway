<script lang="ts">
  import { api } from "$lib/api";
  import { app, route } from "$lib/app.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import ChartColumn from "@lucide/svelte/icons/chart-column";
  import ChartPie from "@lucide/svelte/icons/chart-pie";
  import House from "@lucide/svelte/icons/house";
  import Landmark from "@lucide/svelte/icons/landmark";
  import List from "@lucide/svelte/icons/list";
  import LogOut from "@lucide/svelte/icons/log-out";
  import Repeat from "@lucide/svelte/icons/repeat";
  import Settings from "@lucide/svelte/icons/settings";
  import TrendingUp from "@lucide/svelte/icons/trending-up";

  const main = [
    { page: "overview", label: "Overview", icon: House },
    { page: "transactions", label: "Transactions", icon: List },
    { page: "budget", label: "Budget", icon: ChartPie },
    { page: "recurring", label: "Recurring", icon: Repeat },
    { page: "reports", label: "Reports", icon: ChartColumn },
  ];
  const money = [
    { page: "investments", label: "Investments", icon: TrendingUp },
    { page: "networth", label: "Net worth", icon: Landmark },
  ];
  // Review is a tab of Transactions.
  const current = $derived(route.page === "review" ? "transactions" : route.page);

  const s = $derived(app.state);
  const user = $derived(s?.user && !s.user.local ? s.user : null);
  const userName = $derived(user ? user.name || user.email || "Signed in" : "");
  const initials = $derived(userName.split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join(""));

  // The sidebar just says how fresh the data is; sync runs on its own (daily, and when you open Runway).
  const sync = $derived.by(() => {
    if (!s) return { text: "", tone: "", title: "" };
    if (s.syncing) return { text: "Syncing…", tone: "busy", title: "" };
    if (s.last_log && !s.last_log.ok) return { text: "Last sync failed", tone: "bad", title: s.last_log.message ?? "" };
    if (s.last_sync_ok) {
      const t = new Date(s.last_sync_ok), today = new Date().toDateString() === t.toDateString();
      return { text: "Up to date · " + (today ? t.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })
        : t.toLocaleDateString("en-US", { month: "short", day: "numeric" })), tone: "", title: "Last synced " + t.toLocaleString() };
    }
    return s.connected ? { text: "Not synced yet", tone: "busy", title: "" } : { text: "Bank not connected", tone: "bad", title: "" };
  });

  // Signing out is a POST (so no other site can sign you out with a link); then on to the provider's sign-out page.
  async function signOut(e: Event) {
    e.preventDefault();
    try {
      const r = await api<{ redirect?: string }>("/auth/logout", { method: "POST" });
      location.href = r.redirect || "/auth/signed-out";
    } catch (err) { toast.error((err as Error).message); }
  }
</script>

{#snippet link(item: { page: string; label: string; icon: typeof House }, badge?: number)}
  <a href={`#${item.page}`} aria-current={current === item.page ? "page" : undefined}
    class={cn("flex h-8 shrink-0 items-center gap-2 rounded-md px-2 text-sm text-sidebar-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
      current === item.page && "bg-sidebar-accent font-medium text-sidebar-accent-foreground")}>
    <item.icon class="size-4" aria-hidden="true" />
    <span>{item.label}</span>
    {#if badge}<Badge class="ml-auto tabular-nums">{badge}</Badge>{/if}
  </a>
{/snippet}

<aside class="flex shrink-0 gap-1 overflow-x-auto border-border bg-sidebar p-2 max-md:border-b md:sticky md:top-0 md:h-dvh md:w-64 md:flex-col md:border-r md:p-3">
  <a href="#overview" class="mb-4 hidden items-center gap-2.5 px-2 font-semibold md:flex">
    <img src="/logo.svg" alt="" width="24" height="24" />Runway
    {#if s?.version}<span class="text-xs font-normal text-muted-foreground" title={`Runway ${s.version}`}>{s.version}</span>{/if}
  </a>
  <nav class="flex gap-1 md:flex-col" aria-label="Main">
    {#each main as item (item.page)}{@render link(item, item.page === "transactions" ? s?.review_count : undefined)}{/each}
    <div class="mx-1 my-2 hidden h-px bg-border md:block" role="presentation"></div>
    {#each money as item (item.page)}{@render link(item)}{/each}
  </nav>
  <div class="hidden flex-1 md:block"></div>
  {@render link({ page: "setup", label: "Settings", icon: Settings })}
  <div class="mt-2 hidden items-center gap-2.5 border-t border-border px-2 pt-3 md:flex">
    {#if user}
      <div class="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium">{initials}</div>
    {/if}
    <div class="flex min-w-0 flex-1 flex-col">
      {#if user}<span class="truncate text-sm font-medium" title={user.email ?? ""}>{userName}</span>{/if}
      <span class="flex items-center gap-1.5 text-xs text-muted-foreground" title={sync.title} role="status">
        <span class={cn("size-1.5 rounded-full bg-emerald-500", sync.tone === "busy" && "animate-pulse bg-muted-foreground", sync.tone === "bad" && "bg-destructive")}></span>
        {sync.text}
      </span>
    </div>
    {#if user}
      <a href="/auth/logout" onclick={signOut} title="Sign out" aria-label="Sign out" class="text-muted-foreground hover:text-foreground">
        <LogOut class="size-4" />
      </a>
    {/if}
  </div>
</aside>
