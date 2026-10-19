<script lang="ts" module>
  // Survives redraws (a sync, an edited amount), like the classic app.
  let horizon: number | null = null;
  let comingAll = $state(false);   // Coming up's "Show all"
  // Alerts put away on Needs attention, by their message: one stays hidden while its message reads the same, so a
  // warning worded differently (or one you ask to see again) comes back. Saved with the other settings.
  let dismissed = $state<string[]>([]);
  let showDismissed = $state(false);
  const DISMISSED_MAX = 50;   // the most the server keeps
  // The forecast last on screen, drawn at once when the page is drawn afresh (after a change) until the new one arrives.
  let last: { fc: Overview; days: number } | null = null;
  /** For tests: the page starts as if opened for the first time. */
  export function forgetForecast() { horizon = null; last = null; }
</script>

<script lang="ts">
  import { act } from "$lib/act";
  import { api } from "$lib/api";
  import { overviewFor, takeEarlyOverview } from "$lib/prefetch";
  import { app } from "$lib/app.svelte";
  import MissedAlert from "$lib/components/MissedAlert.svelte";
  import CardsTable from "$lib/components/overview/CardsTable.svelte";
  import EventsList from "$lib/components/overview/EventsList.svelte";
  import BalanceCard from "$lib/components/overview/BalanceCard.svelte";
  import InvestmentsCard from "$lib/components/overview/InvestmentsCard.svelte";
  import SummaryTiles from "$lib/components/overview/SummaryTiles.svelte";
  import { comingUp } from "$lib/components/overview/comingUp";
  import { setupReview } from "$lib/components/overview/forecastSheet.svelte";
  import SetupChecklist from "$lib/components/overview/SetupChecklist.svelte";
  import ThisMonth from "$lib/components/overview/ThisMonth.svelte";
  import { Button } from "$lib/components/ui/button";
  import { parseDate } from "$lib/format";
  import { syncDot, syncStatus } from "$lib/nav.svelte";
  import type { Overview } from "$lib/types";
  import { undoable } from "$lib/undo";
  import { cn } from "$lib/utils";
    import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import Group from "$lib/components/ui/group/Group.svelte";

  let { sub: _sub = "" }: { sub?: string } = $props();
  const connected = $derived(app.state?.connected);
  const setup = $derived(app.state?.setup);
  // Until you finish or dismiss it; with all four steps done it ends in the review of the forecast, which the forecast
  // settings can bring back.
  const setupLeft = $derived((!!setup && !setup.dismissed) || setupReview.open);
  $effect(() => () => { setupReview.open = false; });   // a reopened review doesn't outlive leaving Overview
  const toForecast = () => document.getElementById("forecast")?.scrollIntoView({ block: "start" });
  const initial = horizon ?? app.state?.horizon_days ?? 90;

  // The selected length is always the one on screen: a request only replaces `shown` (data, chart label, sentence and
  // the length controls together) when it succeeds. Another length (or a change) keeps the forecast on screen until
  // the new one arrives, and only the latest request draws, if you click through lengths quickly.
  let shown = $state.raw(last);
  let requested = $state<number | null>(last && last.days !== initial ? initial : null);   // a different length is on its way
  const days = $derived(shown?.days ?? requested ?? initial);
  let failed = $state<{ days: number } | null>(null);   // the last request that failed while a forecast is on screen
  let error = $state<Error | null>(null);
  let seq = 0;
  let intent = initial;   // the length the newest request asked for
  let picked = false;   // …and whether it was chosen (not just a refresh), so only a chosen length that arrives is remembered
  const span = (d: number) => (d === 180 ? "6 months" : `${d} days`);
  // The app's first draw may use the forecast main.ts asked for while the state was loading (lib/prefetch.ts).
  let early = takeEarlyOverview();
  function load(d: number) {
    const n = ++seq;
    intent = d;
    requested = shown && shown.days !== d ? d : null;
    const reply = overviewFor(d, early);
    early = null;   // only the first request; every later one asks the server
    reply.then(
      (fc) => {
        if (n !== seq) return;
        shown = last = { fc, days: d }; requested = null; failed = null; error = null;
        if (picked) horizon = d;   // the successful length is the one remembered, never a failed request
        dismissed = fc.dismissed_warnings ?? []; showDismissed = false;
      },
      (e: Error) => {
        if (n !== seq) return;
        if (!shown) { error = e; return; }
        failed = { days: d };   // stays up (not a toast) until a replacement arrives or another length is chosen
        intent = shown.days; picked = false; requested = null;
      });
  }
  load(initial);
  function setDays(v: string) { failed = null; picked = true; load(Number(v)); }
  const refresh = () => load(intent);

  // An alert is put away by its message, so one worded differently later (the same link, a new problem) shows again.
  const remember = (list: string[]) => api("/api/settings", { method: "POST", body: { overview_warnings_dismissed: list } });
  async function dismiss(message: string) {
    const next = [...dismissed, message].slice(-DISMISSED_MAX);   // the oldest go first, so the server's limit never turns Dismiss down
    if (!(await act(async () => { await remember(next); dismissed = next; }))) return;   // the alert stays up if it didn't save
    undoable("Alert dismissed", () => restore(message));
  }
  async function restore(message: string) {
    const next = dismissed.filter((m) => m !== message);
    await remember(next);   // a failure throws, so Undo's toast says what went wrong
    dismissed = next;
  }

  // Needs attention shows its first item and "+N more", which opens the rest (all of them while put-away ones are shown).
  let moreOpen = $state(false);

</script>

<!-- An alert links to where it's put right: a compact banner of one or two lines. -->
{#snippet attention(text: string, href: string)}
    <a class="banner" {href}>
      <TriangleAlert class="size-4 shrink-0 text-warning" aria-hidden="true" />
      <span class="line-clamp-2 min-w-0 flex-1 text-[13px] leading-snug">{text}</span>
      <ChevronRight class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
    </a>
{/snippet}

<!-- One that can be put away, by its message. `held` is one you've put away: shown again so it can be brought back. -->
{#snippet warn(text: string, href: string, held: boolean)}
    <div class="banner" class:opacity-60={held}>
      <a href={`/${href}`} class="flex min-w-0 flex-1 items-center gap-2">
        <TriangleAlert class="size-4 shrink-0 text-warning" aria-hidden="true" />
        <span class="line-clamp-2 min-w-0 flex-1 text-[13px] leading-snug">{text}</span>
        <ChevronRight class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      </a>
      <Button variant="ghost" size="sm" class="h-7 shrink-0 px-2 text-xs text-muted-foreground" aria-label={`${held ? "Show again" : "Dismiss"} ${text}`}
        onclick={() => (held ? act(() => restore(text)) : dismiss(text))}>{held ? "Show again" : "Dismiss"}</Button>
    </div>
{/snippet}

{#if !connected}
  <SetupChecklist welcome />
{:else}
  {#if error && !shown}
    <div class="rounded-2xl bg-card p-5">
      <p class="text-sm">Something went wrong: {error.message}</p>
      <Button class="mt-3" variant="outline" onclick={() => load(intent)}>Try again</Button>
    </div>
  {:else if !shown}
    <div class="space-y-4" aria-busy="true"><div class="h-24 w-72 animate-pulse motion-reduce:animate-none rounded-2xl bg-card"></div><div class="h-64 animate-pulse motion-reduce:animate-none rounded-2xl bg-card"></div></div>
  {:else}
    {@const fc = shown.fc}
    {@const gone = new Set(dismissed)}
    {@const open = fc.warning_links.filter((w) => !gone.has(w.text))}
    {@const held = fc.warning_links.filter((w) => gone.has(w.text))}
    {@const alerts = open.length + (fc.missed?.length ?? 0) + (fc.accounts.length ? 0 : 1)}
    {@const rows = showDismissed ? [...open, ...held] : open}
    <!-- Every item in the order they're listed: the forecast's warnings, missed payments, then the no-account alert. -->
    {@const items = [...rows.map((w) => ({ id: w.text, w, m: null })), ...(fc.missed ?? []).map((m) => ({ id: m.key, w: null, m })),
      ...(fc.accounts.length ? [] : [{ id: "no-account", w: null, m: null }])]}
    {@const expanded = moreOpen || showDismissed}
    {@const sync = syncStatus(app.state)}

    <header class="mb-4 flex items-start justify-between gap-3">
      <div class="min-w-0">
        <div class="text-[13px] text-muted-foreground">
          {parseDate(fc.today).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })}
        </div>
        <h1 class="text-[30px] leading-[1.1] font-extrabold tracking-[-0.03em]">Overview</h1>
      </div>
      {#if sync.text}
        {@const pill = "mt-1 inline-flex shrink-0 items-center gap-1.5 rounded-full border border-edge bg-card px-2.5 py-1 text-[12px] text-muted-foreground"}
        {#snippet inner()}<span class={cn("size-1.5 rounded-full", syncDot(sync.tone))} aria-hidden="true"></span>{sync.text}{/snippet}
        {#if sync.href}<a class={cn(pill, "hover:text-foreground")} href={sync.href} title={sync.title || undefined}>{@render inner()}</a>
        {:else}<span class={pill} title={sync.title || undefined}>{@render inner()}</span>{/if}
      {/if}
    </header>

    {#if alerts || held.length}
      <section class="mb-4 min-w-0">
        <div class="mb-1.5 flex items-baseline justify-between px-1">
          <h2 class="eyebrow">Needs attention</h2>
          {#if held.length && (alerts || showDismissed)}
            <button type="button" class="text-[13px] text-primary" onclick={() => (showDismissed = !showDismissed)}>
              {showDismissed ? "Hide dismissed" : "Show dismissed"}
            </button>
          {/if}
        </div>
        <div class="flex flex-col gap-1.5">
          {#each expanded ? items : items.slice(0, 1) as it (it.id)}
            {#if it.w}{@render warn(it.w.text, it.w.href, gone.has(it.w.text))}
            {:else if it.m}<MissedAlert m={it.m} today={fc.today} />
            {:else}{@render attention("No account to forecast yet. Choose your forecast account.", "/#overview?forecast")}{/if}
          {/each}
          {#if items.length > 1 && !expanded}
            <button type="button" class="self-start px-1 text-[13px] font-medium text-primary" aria-expanded="false" onclick={() => (moreOpen = true)}>+{items.length - 1} more</button>
          {/if}
          {#if held.length && !alerts && !showDismissed}
            <!-- Every alert here is put away: the way back, so the section isn't left empty. -->
            <button type="button" class="banner" onclick={() => (showDismissed = true)}>Show dismissed</button>
          {/if}
        </div>
      </section>
    {/if}
    {#if setupLeft}<SetupChecklist fc={fc} onretry={refresh} onfinish={toForecast} />{/if}

    <div id="forecast" class="mb-4 scroll-mt-4">
      {#if requested !== null}
        <p class="mb-2 px-1 text-[13px] text-muted-foreground" role="status">Updating to {span(requested)}… Showing the {span(shown.days)} forecast until it arrives.</p>
      {/if}
      {#if failed}
        <div class="mb-2 flex items-center justify-between gap-3 rounded-xl border border-destructive/40 bg-destructive/10 px-3 py-2 text-[13px]" role="alert">
          <span class="min-w-0">{failed.days === shown.days ? `Couldn’t refresh the ${span(failed.days)} forecast. Showing the previous data.` : `Couldn’t load ${span(failed.days)}. Showing the previous ${span(shown.days)} forecast.`}</span>
          <Button variant="outline" size="sm" class="h-7 shrink-0" onclick={() => { const d = failed!.days; picked = d !== shown!.days; load(d); }}>Retry</Button>
        </div>
      {/if}
      <BalanceCard {fc} {days} shownDays={shown.days} {setDays} onchange={refresh} />
    </div>
    <div class="mb-6"><SummaryTiles /></div>

    <div class="grid items-start gap-6 lg:grid-cols-2">
      <div class="flex min-w-0 flex-col gap-6">
        <Group title="Coming up" inset="3.75rem"><EventsList events={comingUp(fc)} limit={6} bind:all={comingAll}
          onchanged={refresh} /></Group>
        <Group title="Credit cards"><CardsTable cards={fc.cards} onchanged={refresh} /></Group>
      </div>
      <ThisMonth>
        {#snippet after()}<InvestmentsCard />{/snippet}
      </ThisMonth>
    </div>
  {/if}
{/if}
