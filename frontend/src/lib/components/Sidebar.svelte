<script lang="ts">
  import { app, route } from "$lib/app.svelte";
  import { MAIN_NAV, MONEY_NAV, currentPage, signOut, signedInUser, syncDot, syncStatus, type NavGroup, type NavItem } from "$lib/nav.svelte";
  import { tablet } from "$lib/rail.svelte";
  import SyncButton from "$lib/components/SyncButton.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import * as Sheet from "$lib/components/ui/sheet";
  import { cn } from "$lib/utils";
  import LogOut from "@lucide/svelte/icons/log-out";
  import Menu from "@lucide/svelte/icons/menu";
  import Settings from "@lucide/svelte/icons/settings";

  const current = $derived(currentPage());
  const s = $derived(app.state);
  const user = $derived(signedInUser(s));
  const userName = $derived(user ? user.name || user.email || "Signed in" : "");
  const initials = $derived(userName.split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join(""));
  const sync = $derived(syncStatus(s));

  // On a tablet (lib/rail.svelte.ts) the sidebar is a 72px rail of icons and Menu opens a sheet with the full labels, the account and
  // sync details and sign-out. The sheet closes on going to a page, and when the screen stops being a tablet's.
  let menu = $state(false);
  $effect(() => { void route.page; void route.sub; menu = false; });
  $effect(() => { if (!tablet.rail) menu = false; });

  // In the rail a link's label is its tooltip: out of view until hover or keyboard focus, but still its accessible name.
  const tip = "rail:pointer-events-none rail:absolute rail:left-full rail:z-30 rail:ml-3 rail:rounded-md rail:border rail:border-border rail:bg-popover rail:px-2 rail:py-1 rail:text-xs rail:font-medium rail:whitespace-nowrap rail:text-popover-foreground rail:opacity-0 rail:shadow-md rail:transition-opacity rail:group-hover/nav:opacity-100 rail:group-focus-visible/nav:opacity-100";
</script>

{#snippet link(item: NavItem, badge?: number)}
  <a href={`#${item.page}`} aria-current={current === item.page ? "page" : undefined}
    class={cn("group/nav relative flex h-8 shrink-0 items-center gap-2 rounded-md px-2 text-sm text-sidebar-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
      "rail:size-11 rail:justify-center rail:px-0",
      current === item.page && "bg-sidebar-accent font-medium text-sidebar-accent-foreground rail:before:absolute rail:before:-left-2 rail:before:h-5 rail:before:w-1 rail:before:rounded-r-full rail:before:bg-primary")}>
    <item.icon class="size-4 rail:size-5" aria-hidden="true" />
    <span class={tip}>{item.label}</span>
    {#if badge}<Badge class="ml-auto tabular-nums rail:absolute rail:top-0 rail:right-0 rail:ml-0 rail:h-4 rail:min-w-4 rail:px-1 rail:text-[10px] rail:leading-4">{badge}</Badge>{/if}
  </a>
{/snippet}

<!-- A group's small muted label. The rail has no room for it (its divider still marks the split), but the Menu sheet does. -->
{#snippet groupLabel(g: NavGroup, inRail: boolean)}
  <p class={cn("px-2 pb-0.5 text-[11px] uppercase tracking-wide text-muted-foreground", !inRail && "rail:hidden")}>{g.label}</p>
{/snippet}

<!-- A page in the Menu sheet, with its full label. -->
{#snippet menuLink(item: NavItem, badge?: number)}
  <a href={`#${item.page}`} aria-current={current === item.page ? "page" : undefined} onclick={() => (menu = false)}
    class={cn("flex h-10 items-center gap-2.5 rounded-md px-2 text-sm hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none", current === item.page && "bg-accent font-medium")}>
    <item.icon class="size-4" aria-hidden="true" />
    <span>{item.label}</span>
    {#if badge}<Badge class="ml-auto tabular-nums">{badge}</Badge>{/if}
  </a>
{/snippet}

<!-- Who's signed in, the sync status and its button, and sign-out: the sidebar's foot, and the Menu sheet's on a tablet. -->
{#snippet account()}
  {#if user}
    <div class="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium">{initials}</div>
  {/if}
  <div class="flex min-w-0 flex-1 flex-col">
    {#if user}<span class="truncate text-sm font-medium" title={user.email ?? ""}>{userName}</span>{/if}
    <!-- A problem links to Settings › Connections, with the bank's message on a line of its own (not only on hover). -->
    <div class="flex min-w-0 flex-col text-xs text-muted-foreground" title={sync.title} role="status">
      {#if sync.href}
        <a href={sync.href} onclick={() => (menu = false)} class={cn("flex items-center gap-1.5 underline-offset-4 hover:underline", sync.tone === "warn" ? "text-warning" : "text-destructive")}>
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
{/snippet}

<!-- On a phone (see lib/phone.svelte.ts), MobileNav (a tab bar at the bottom) takes the sidebar's place. From 768 to 1023 px this is
     a 72px rail; from 1024 px up the full sidebar. -->
<aside class="sticky top-0 hidden h-dvh w-64 shrink-0 flex-col gap-1 border-r border-border bg-sidebar p-3 desktop:flex rail:w-[72px] rail:items-center rail:px-2">
  <a href="#overview" class="group/nav relative mb-4 flex items-center gap-2.5 rounded-md px-2 font-semibold focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none rail:mb-3 rail:size-11 rail:justify-center rail:px-0">
    <img src="/logo.svg" alt="" width="24" height="24" /><span class={tip}>Runway</span>
    {#if s?.version}<span class="text-xs font-normal text-muted-foreground rail:hidden" title={`Runway ${s.version}`}>{s.version}</span>{/if}
  </a>
  <nav class="flex flex-col gap-1 rail:items-center" aria-label="Main">
    {@render groupLabel(MAIN_NAV, false)}
    {#each MAIN_NAV.items as item (item.page)}{@render link(item, item.page === "transactions" ? s?.review_count : undefined)}{/each}
    <div class="mx-1 my-2 h-px bg-border rail:mx-0 rail:w-8" role="presentation"></div>
    {@render groupLabel(MONEY_NAV, false)}
    {#each MONEY_NAV.items as item (item.page)}{@render link(item)}{/each}
  </nav>
  <div class="flex-1"></div>
  {@render link({ page: "setup", label: "Settings", icon: Settings })}
  <div class="mt-2 flex items-center gap-2.5 border-t border-border px-2 pt-3 rail:hidden">
    {@render account()}
  </div>
  <Sheet.Root bind:open={menu}>
    <Sheet.Trigger aria-label={sync.href ? `Menu, ${sync.text}` : "Menu"}
      class="group/nav relative mt-1 hidden size-11 cursor-pointer items-center justify-center rounded-md text-sidebar-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none rail:flex">
      <Menu class="size-5" aria-hidden="true" />
      <!-- A sync problem shows on the rail too, so it's seen without opening the menu. -->
      {#if sync.href}<span class={cn("absolute top-1.5 right-1.5 size-2 rounded-full ring-2 ring-sidebar", syncDot(sync.tone))} data-testid="sync-dot"></span>{/if}
      <span class={tip} aria-hidden="true">Menu</span>
    </Sheet.Trigger>
    <Sheet.Content side="left" class="md:w-80">
      <Sheet.Header>
        <Sheet.Title class="flex items-center gap-2.5"><img src="/logo.svg" alt="" width="24" height="24" />Runway
          {#if s?.version}<span class="text-xs font-normal text-muted-foreground">{s.version}</span>{/if}</Sheet.Title>
        <Sheet.Description class="sr-only">All pages, your account and the sync status.</Sheet.Description>
      </Sheet.Header>
      <nav class="flex flex-col gap-1 px-4" aria-label="All pages">
        {@render groupLabel(MAIN_NAV, true)}
        {#each MAIN_NAV.items as item (item.page)}
          {@render menuLink(item, item.page === "transactions" ? s?.review_count : undefined)}
        {/each}
        <div class="my-2 h-px bg-border" role="presentation"></div>
        {@render groupLabel(MONEY_NAV, true)}
        {#each MONEY_NAV.items as item (item.page)}{@render menuLink(item)}{/each}
        <div class="my-2 h-px bg-border" role="presentation"></div>
        {@render menuLink({ page: "setup", label: "Settings", icon: Settings })}
      </nav>
      <div class="mt-auto flex items-center gap-2.5 border-t border-border p-4">
        {@render account()}
      </div>
    </Sheet.Content>
  </Sheet.Root>
</aside>
