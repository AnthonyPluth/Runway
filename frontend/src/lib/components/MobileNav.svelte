<script lang="ts">
  import { untrack } from "svelte";
  import { app, route } from "$lib/app.svelte";
  import { MAIN_NAV, MONEY_NAV, currentPage, signOut, signedInUser, syncDot, syncStatus, type NavItem } from "$lib/nav.svelte";
  import * as Sheet from "$lib/components/ui/sheet";
  import SyncButton from "$lib/components/SyncButton.svelte";
  import { cn } from "$lib/utils";
  import Ellipsis from "@lucide/svelte/icons/ellipsis";
  import LogOut from "@lucide/svelte/icons/log-out";
  import Settings from "@lucide/svelte/icons/settings";

  // On a phone (lib/phone.svelte.ts): a floating tab bar along the bottom with the four pages you use most, and More for
  // the rest (which opens a sheet from the bottom). Hidden on a computer, where the sidebar shows instead. Recurring and
  // Reports are in the sidebar's main group but go in More here: more tabs wouldn't fit a phone's width.
  const IN_MORE = ["recurring", "reports"];
  const TABS = MAIN_NAV.filter((x) => !IN_MORE.includes(x.page));
  const MORE: NavItem[] = [...MAIN_NAV.filter((x) => IN_MORE.includes(x.page)), ...MONEY_NAV, { page: "setup", label: "Settings", icon: Settings }];

  const current = $derived(currentPage());
  const s = $derived(app.state);
  const user = $derived(signedInUser(s));
  const sync = $derived(syncStatus(s));
  const inMore = $derived(MORE.some((x) => x.page === current));
  let open = $state(false);
  let moreBtn = $state<HTMLButtonElement | null>(null);
  // Set when a route change closes the sheet: focus then follows the new page, not back onto More.
  let leaving = false;
  // Going to a page closes the sheet.
  $effect(() => { void route.page; untrack(() => { if (open) leaving = true; open = false; }); });
</script>

<Sheet.Root bind:open>
  <Sheet.Content class="gap-0 px-4 pt-3 pb-[calc(env(safe-area-inset-bottom)+1rem)] md:pb-4" aria-describedby={undefined}
    onCloseAutoFocus={(e) => { e.preventDefault(); if (!leaving) moreBtn?.focus(); leaving = false; }}>
    <div class="mb-2 flex min-h-11 items-center pr-12">
      <Sheet.Title class="text-sm">More pages</Sheet.Title>
    </div>
    <nav class="grid grid-cols-2 gap-2" aria-label="More pages">
      {#each MORE as item (item.page)}
        <a href={`#${item.page}`} aria-current={current === item.page ? "page" : undefined}
          class={cn("flex min-h-11 items-center gap-2.5 rounded-xl border bg-card px-3 py-3 text-sm", current === item.page && "border-primary/50 bg-muted font-medium")}>
          <item.icon class="size-5 text-muted-foreground" aria-hidden="true" />{item.label}
        </a>
      {/each}
    </nav>
    <div class="mt-4 flex items-start gap-2 text-xs text-muted-foreground">
      <div class="flex min-w-0 flex-1 flex-col" role="status" title={sync.title}>
        {#if sync.href}
          <a href={sync.href} class={cn("flex min-h-11 items-center gap-2 underline underline-offset-4", sync.tone === "warn" ? "text-warning" : "text-destructive")}>
            <span class={cn("size-1.5 shrink-0 rounded-full", syncDot(sync.tone))}></span>{sync.text}
          </a>
        {:else}
          <span class="flex items-center gap-2"><span class={cn("size-1.5 shrink-0 rounded-full", syncDot(sync.tone))}></span>{sync.text}</span>
        {/if}
        {#if sync.detail}<span class="truncate pl-3.5">{sync.detail}</span>{/if}
      </div>
      <SyncButton class="size-11 border border-border" />
      {#if s?.version}<span class="shrink-0 pt-2">Runway {s.version}</span>{/if}
    </div>
    {#if user}
      <a href="/auth/logout" onclick={signOut} class="mt-3 flex min-h-11 items-center gap-2 text-sm text-muted-foreground"><LogOut class="size-4" />Sign out {user.email ?? ""}</a>
    {/if}
  </Sheet.Content>
</Sheet.Root>

<!-- A floating pill: inset from the sides and lifted off the bottom, translucent over the page with a blur behind it. -->
<nav aria-label="Main" {...(open ? { inert: true } : {})} class="fixed inset-x-4 bottom-[calc(env(safe-area-inset-bottom)+0.75rem)] z-50 desktop:hidden">
  <div class="mx-auto grid h-[66px] max-w-lg grid-cols-5 items-stretch gap-0 rounded-full border bg-popover/80 p-0.5 min-[380px]:gap-0.5 min-[380px]:p-1 shadow-lg backdrop-blur-xl">
    {#each TABS as item (item.page)}
      {@const on = current === item.page && !open}
      <a href={`#${item.page}`} aria-current={on ? "page" : undefined} aria-label={item.short ? item.label : undefined}
        class={cn("relative flex min-w-0 flex-col items-center justify-center gap-0.5 rounded-full text-[10px] tracking-tight min-[380px]:text-[11px] min-[380px]:tracking-normal", on ? "bg-foreground/12 text-foreground" : "text-muted-foreground")}>
        <item.icon class={cn("size-5", on && "stroke-[2.25]")} aria-hidden="true" />
        <span class="max-w-full truncate">{item.short ?? item.label}</span>
        {#if item.page === "transactions" && s?.review_count}
          <span class="absolute top-1 left-1/2 ml-2 min-w-4 rounded-full bg-primary px-1 text-center text-[10px] leading-4 font-semibold text-primary-foreground tabular-nums">{s.review_count > 99 ? "99+" : s.review_count}</span>
        {/if}
      </a>
    {/each}
    <button type="button" bind:this={moreBtn} aria-expanded={open} aria-haspopup="dialog" onclick={() => (open = !open)}
      class={cn("flex min-w-0 cursor-pointer flex-col items-center justify-center gap-0.5 rounded-full text-[10px] tracking-tight min-[380px]:text-[11px] min-[380px]:tracking-normal", open || inMore ? "bg-foreground/12 text-foreground" : "text-muted-foreground")}>
      <span class="relative"><Ellipsis class="size-5" aria-hidden="true" />
        <!-- A sync problem shows here too, so it's seen without opening the sheet. -->
        {#if sync.href}<span class={cn("absolute -top-0.5 -right-1 size-2 rounded-full ring-2 ring-popover", syncDot(sync.tone))} data-testid="sync-dot"></span>{/if}
      </span>More{#if sync.href}<span class="sr-only">{`, ${sync.text}`}</span>{/if}
    </button>
  </div>
</nav>
