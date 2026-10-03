<script lang="ts">
  import { api } from "$lib/api";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { fmt } from "$lib/format";
  import { cn } from "$lib/utils";
  import { catParentOf } from "$lib/categories.svelte";
  import { commas } from "$lib/commas";
  import Chip from "./Chip.svelte";
  import { fullDate, isTravel } from "./churning";
  import type { BestCard } from "./types";

  // Which card to use for a purchase: pick a category (and an amount, if you like) and the open cards are ranked by
  // what they return. A card still short of its sign-up bonus says so: spending there may be worth more.
  let { person, version, showOwner, today }: { person: string; version: number; showOwner: boolean; today?: string } = $props();
  // Some rates only count when you book through the issuer's travel portal: for travel, tick the box if you will, and
  // those count.
  let category = $state(""), amount = $state(""), portal = $state(false);
  let ranked = $state<BestCard[] | null>(null);
  // Travel by its name, or any category a card has a portal-only rate for (a travel category called something else).
  const travel = $derived(isTravel(category, catParentOf(category)) ||
    (!!category && !!ranked?.some((c) => c.needs_portal || c.portal_option)));
  let failed = $state(false), loading = $state(true);
  let tries = $state(0);   // Try again counts up, which asks again at once

  // Typing an amount (or picking another category) asks once you pause for DEBOUNCE_MS, and a slow answer to an old
  // question never replaces the answer to the newer one. The first ask, a reload of the page's data and Try again go at once.
  const DEBOUNCE_MS = 300;
  let seq = 0, started = false, seenVersion = -1, seenTries = 0;
  async function ask(path: string) {
    const n = ++seq;
    try {
      const r = await api<{ cards: BestCard[] }>(path);
      if (n !== seq) return;
      ranked = r.cards; failed = false;
    } catch {
      if (n !== seq) return;
      failed = true;
    }
    loading = false;
  }
  $effect(() => {
    const params = new URLSearchParams({ category, owner: person, amount: amount && Number(amount) > 0 ? amount : "" });
    if (portal && travel) params.set("portal", "1");
    const path = `/api/churning/best?${params}`;
    const now = !started || version !== seenVersion || tries !== seenTries;
    started = true; seenVersion = version; seenTries = tries;
    loading = true;
    if (now) { ask(path); return; }
    const timer = setTimeout(() => ask(path), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  });
  const push = $derived(ranked?.filter((c) => c.bonus) ?? []);
</script>

<Card.Root class="mb-6">
  <Card.Header><Card.Title>Best card for…</Card.Title></Card.Header>
  <Card.Content>
    <div class="flex flex-wrap items-end gap-3">
      <label class="flex min-w-56 flex-col gap-1 text-sm">A purchase in
        <CategorySelect bind:value={category} blank="Anything (base rates)" label="Category of the purchase" exclude={(x) => !!x.is_transfer || !!x.is_income} />
      </label>
      <label class="flex flex-col gap-1 text-sm">Amount <span class="sr-only">(optional)</span>
        <span class="relative">
          <span class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true">$</span>
          <Input type="number" min="0" step="1" class="w-32 pl-6" bind:value={amount} {@attach commas} placeholder="optional" />
        </span>
      </label>
      {#if travel}
        <label class="inline-flex items-center gap-2 pb-2 text-sm"><input type="checkbox" class="size-4" bind:checked={portal} />I'll book through the issuer's travel portal</label>
      {/if}
    </div>
    {#if failed}
      <p class="mt-4 flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground" role="status">
        Couldn’t rank your cards just now.
        <Button variant="link" size="sm" class="h-auto px-0" onclick={() => tries++}>Try again</Button>
      </p>
    {:else if !ranked}
      <div class="mt-4 space-y-2" aria-busy="true" aria-label="Ranking your cards">
        {#each [0, 1, 2] as i (i)}<div class="h-10 animate-pulse rounded-lg bg-muted motion-reduce:animate-none"></div>{/each}
      </div>
    {:else if !ranked.length}<p class="mt-4 text-sm text-muted-foreground">No open cards{person ? ` for ${person}` : ""} yet.</p>
    {:else}
      {#if push.length}
        <p class="mt-4 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
          Put spending on <b>{push[0].product}</b>{showOwner ? ` (${push[0].owner})` : ""} to hit its bonus: {fmt(push[0].bonus!.remaining)} to go by {fullDate(push[0].bonus!.deadline, today)}.
        </p>
      {/if}
      <ol class={cn("mt-3 divide-y transition-opacity", loading && "opacity-60")} aria-busy={loading}>
        {#each ranked as c, i (c.id)}
          <li class="flex items-center gap-3 py-2">
            <span class={cn("w-5 text-right text-sm tabular-nums", i === 0 ? "font-semibold" : "text-muted-foreground")}>{i + 1}</span>
            <div class="min-w-0 flex-1">
              <div class={cn("flex flex-wrap items-center gap-1.5 text-sm", i === 0 && "font-semibold")}>{c.product}{#if showOwner}<span class="font-normal text-muted-foreground"> · {c.owner}</span>{/if}
                {#if c.needs_portal}<Chip>Via {c.portal_name ?? "the issuer's portal"}</Chip>{/if}</div>
              <div class="text-xs text-muted-foreground">{c.multiplier}x {c.currency} at {c.cents}¢{c.bonus ? ` · bonus: ${fmt(c.bonus.remaining)} to go` : ""}</div>
              {#if c.note}<div class={cn("text-xs", c.portal_option ? "text-good" : "text-muted-foreground")}>{c.note}</div>{/if}
            </div>
            <div class="text-right">
              <div class="text-sm font-medium tabular-nums">{c.return_pct.toFixed(1)}%</div>
              {#if c.value != null}<div class="text-xs text-muted-foreground tabular-nums">{fmt(c.value)}</div>{/if}
            </div>
          </li>
        {/each}
      </ol>
    {/if}
  </Card.Content>
</Card.Root>
