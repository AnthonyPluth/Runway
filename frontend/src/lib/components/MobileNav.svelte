<script lang="ts">
  import { app, route } from "$lib/app.svelte";
  import { MAIN_NAV, MONEY_NAV, currentPage, signOut, signedInUser, syncStatus, type NavItem } from "$lib/nav.svelte";
  import { cn } from "$lib/utils";
  import Ellipsis from "@lucide/svelte/icons/ellipsis";
  import LogOut from "@lucide/svelte/icons/log-out";
  import Settings from "@lucide/svelte/icons/settings";
  import X from "@lucide/svelte/icons/x";

  // On a phone: a tab bar along the bottom with the four pages you use most, and More for the rest (which opens a
  // sheet from the bottom). Hidden from md up, where the sidebar shows instead.
  const TABS = MAIN_NAV.filter((x) => x.page !== "recurring");
  const MORE: NavItem[] = [...MAIN_NAV.filter((x) => x.page === "recurring"), ...MONEY_NAV, { page: "setup", label: "Settings", icon: Settings }];

  const current = $derived(currentPage());
  const s = $derived(app.state);
  const user = $derived(signedInUser(s));
  const sync = $derived(syncStatus(s));
  const inMore = $derived(MORE.some((x) => x.page === current));
  let open = $state(false);
  // Going to a page closes the sheet.
  $effect(() => { void route.page; open = false; });
</script>

<svelte:window onkeydown={(e) => { if (open && e.key === "Escape") open = false; }} />

{#if open}
  <button type="button" class="fixed inset-0 z-40 cursor-default bg-black/60 md:hidden" aria-label="Close" onclick={() => (open = false)}></button>
  <div role="dialog" aria-modal="true" aria-label="More pages"
    class="fixed inset-x-0 bottom-0 z-50 rounded-t-2xl border-t bg-popover px-4 pt-3 pb-[calc(env(safe-area-inset-bottom)+5rem)] shadow-2xl md:hidden">
    <div class="mb-2 flex items-center justify-between">
      <span class="text-sm font-semibold">More</span>
      <button type="button" class="flex size-8 cursor-pointer items-center justify-center rounded-full hover:bg-muted" aria-label="Close" onclick={() => (open = false)}><X class="size-4" /></button>
    </div>
    <nav class="grid grid-cols-2 gap-2" aria-label="More pages">
      {#each MORE as item (item.page)}
        <a href={`#${item.page}`} aria-current={current === item.page ? "page" : undefined}
          class={cn("flex items-center gap-2.5 rounded-xl border bg-card px-3 py-3 text-sm", current === item.page && "border-primary/50 bg-muted font-medium")}>
          <item.icon class="size-5 text-muted-foreground" aria-hidden="true" />{item.label}
        </a>
      {/each}
    </nav>
    <div class="mt-4 flex items-center gap-2 text-xs text-muted-foreground">
      <span class={cn("size-1.5 rounded-full bg-emerald-500", sync.tone === "busy" && "animate-pulse bg-muted-foreground", sync.tone === "bad" && "bg-destructive")}></span>
      <span role="status" title={sync.title}>{sync.text}</span>
      {#if s?.version}<span class="ml-auto">Runway {s.version}</span>{/if}
    </div>
    {#if user}
      <a href="/auth/logout" onclick={signOut} class="mt-3 flex items-center gap-2 text-sm text-muted-foreground"><LogOut class="size-4" />Sign out {user.email ?? ""}</a>
    {/if}
  </div>
{/if}

<nav aria-label="Main" class="fixed inset-x-0 bottom-0 z-50 border-t bg-sidebar/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
  <div class="mx-auto grid max-w-lg grid-cols-5">
    {#each TABS as item (item.page)}
      {@const on = current === item.page && !open}
      <a href={`#${item.page}`} aria-current={on ? "page" : undefined}
        class={cn("relative flex flex-col items-center gap-0.5 pt-2 pb-1.5 text-[11px]", on ? "text-foreground" : "text-muted-foreground")}>
        <item.icon class={cn("size-5", on && "stroke-[2.25]")} aria-hidden="true" />
        {item.label}
        {#if item.page === "transactions" && s?.review_count}
          <span class="absolute top-1 left-1/2 ml-2 min-w-4 rounded-full bg-primary px-1 text-center text-[10px] leading-4 font-semibold text-primary-foreground tabular-nums">{s.review_count > 99 ? "99+" : s.review_count}</span>
        {/if}
      </a>
    {/each}
    <button type="button" aria-expanded={open} onclick={() => (open = !open)}
      class={cn("flex cursor-pointer flex-col items-center gap-0.5 pt-2 pb-1.5 text-[11px]", open || inMore ? "text-foreground" : "text-muted-foreground")}>
      <Ellipsis class="size-5" aria-hidden="true" />More
    </button>
  </div>
</nav>
