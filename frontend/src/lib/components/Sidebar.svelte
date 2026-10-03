<script lang="ts">
  import { app } from "$lib/app.svelte";
  import { MAIN_NAV, MONEY_NAV, currentPage, signOut, signedInUser, syncDot, syncStatus, type NavItem } from "$lib/nav.svelte";
  import SyncButton from "$lib/components/SyncButton.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { cn } from "$lib/utils";
  import LogOut from "@lucide/svelte/icons/log-out";
  import Settings from "@lucide/svelte/icons/settings";

  const current = $derived(currentPage());
  const s = $derived(app.state);
  const user = $derived(signedInUser(s));
  const userName = $derived(user ? user.name || user.email || "Signed in" : "");
  const initials = $derived(userName.split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join(""));
  const sync = $derived(syncStatus(s));
</script>

{#snippet link(item: NavItem, badge?: number)}
  <a href={`#${item.page}`} aria-current={current === item.page ? "page" : undefined}
    class={cn("flex h-8 shrink-0 items-center gap-2 rounded-md px-2 text-sm text-sidebar-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
      current === item.page && "bg-sidebar-accent font-medium text-sidebar-accent-foreground")}>
    <item.icon class="size-4" aria-hidden="true" />
    <span>{item.label}</span>
    {#if badge}<Badge class="ml-auto tabular-nums">{badge}</Badge>{/if}
  </a>
{/snippet}

<!-- On a phone (see lib/phone.svelte.ts), MobileNav (a tab bar at the bottom) takes the sidebar's place. -->
<aside class="sticky top-0 hidden h-dvh w-64 shrink-0 flex-col gap-1 border-r border-border bg-sidebar p-3 desktop:flex">
  <a href="#overview" class="mb-4 flex items-center gap-2.5 px-2 font-semibold">
    <img src="/logo.svg" alt="" width="24" height="24" />Runway
    {#if s?.version}<span class="text-xs font-normal text-muted-foreground" title={`Runway ${s.version}`}>{s.version}</span>{/if}
  </a>
  <nav class="flex flex-col gap-1" aria-label="Main">
    {#each MAIN_NAV as item (item.page)}{@render link(item, item.page === "transactions" ? s?.review_count : undefined)}{/each}
    <div class="mx-1 my-2 h-px bg-border" role="presentation"></div>
    {#each MONEY_NAV as item (item.page)}{@render link(item)}{/each}
  </nav>
  <div class="flex-1"></div>
  {@render link({ page: "setup", label: "Settings", icon: Settings })}
  <div class="mt-2 flex items-center gap-2.5 border-t border-border px-2 pt-3">
    {#if user}
      <div class="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium">{initials}</div>
    {/if}
    <div class="flex min-w-0 flex-1 flex-col">
      {#if user}<span class="truncate text-sm font-medium" title={user.email ?? ""}>{userName}</span>{/if}
      <!-- A problem links to Settings › Connections, with the bank's message on a line of its own (not only on hover). -->
      <div class="flex min-w-0 flex-col text-xs text-muted-foreground" title={sync.title} role="status">
        {#if sync.href}
          <a href={sync.href} class={cn("flex items-center gap-1.5 underline-offset-4 hover:underline", sync.tone === "warn" ? "text-warning" : "text-destructive")}>
            <span class={cn("size-1.5 shrink-0 rounded-full", syncDot(sync.tone))}></span>{sync.text}
          </a>
        {:else}
          <span class="flex items-center gap-1.5"><span class={cn("size-1.5 shrink-0 rounded-full", syncDot(sync.tone))}></span>{sync.text}</span>
        {/if}
        {#if sync.detail}<span class="truncate pl-3">{sync.detail}</span>{/if}
      </div>
    </div>
    <SyncButton />
    {#if user}
      <a href="/auth/logout" onclick={signOut} title="Sign out" aria-label="Sign out" class="text-muted-foreground hover:text-foreground">
        <LogOut class="size-4" />
      </a>
    {/if}
  </div>
</aside>
