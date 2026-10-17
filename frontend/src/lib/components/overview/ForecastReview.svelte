<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { fmt, fmtDate, plural } from "$lib/format";
  import type { Overview } from "$lib/types";
  import { openForecastSettings } from "./forecastSheet.svelte";

  // The last step of getting started: what the forecast is using, read straight from the Overview's own figures, with
  // a way to change each. It says what is there and what isn't, and nothing about whether the forecast is right or safe.
  // Something left out is fine: Finish setup doesn't need any of it.
  let { fc, error = null, onretry }: { fc: Overview | null; error?: Error | null; onretry?: () => void } = $props();

  const BILLS_SHOWN = 3;
  const recurring = $derived((fc?.events ?? []).filter((e) => e.kind === "recurring"));
  const income = $derived(recurring.find((e) => e.amount > 0));
  const bills = $derived(recurring.filter((e) => e.amount < 0));
  // The forecast's own card warnings (a missing or old statement, no account paying the card, ...): each links to
  // Settings → Accounts, where they're put right.
  const cards = $derived((fc?.warning_links ?? []).filter((w) => w.href.startsWith("#setup/accounts")).map((w) => w.text));
  const budget = $derived(fc?.budget);
  const editCls = "h-10 shrink-0 sm:h-8";
</script>

{#snippet row(title: string, id: string, label: string, href?: string, onclick?: () => void)}
  <li class="flex items-start gap-3.5 border-t py-3.5 first:border-t-0">
    <div class="min-w-0 flex-1">
      <div class="text-xs text-muted-foreground">{title}</div>
      <div class="mt-0.5 text-sm">
        {#if id === "account"}
          {#if fc?.accounts.length}{fc.accounts.map((a) => a.name).join(" + ")}{:else}<span class="text-muted-foreground">No forecast account chosen</span>{/if}
        {:else if id === "income"}
          {#if income}{income.name}, {fmtDate(income.date)} · {fmt(income.amount)}{:else}<span class="text-muted-foreground">No income scheduled</span>{/if}
        {:else if id === "bills"}
          {#if bills.length}
            {plural(bills.length, "bill")} coming up
            <ul class="mt-0.5 text-xs text-muted-foreground">
              {#each bills.slice(0, BILLS_SHOWN) as b (b.key ?? b.date + b.name)}<li>{b.name}, {fmtDate(b.date)} · {fmt(-b.amount)}</li>{/each}
              {#if bills.length > BILLS_SHOWN}<li>and {bills.length - BILLS_SHOWN} more</li>{/if}
            </ul>
          {:else}<span class="text-muted-foreground">No bills scheduled</span>{/if}
        {:else if id === "cards"}
          {#if cards.length}
            {plural(cards.length, "card payment")} to look at
            <ul class="mt-0.5 text-xs text-muted-foreground">{#each cards as c (c)}<li>{c}</li>{/each}</ul>
          {:else}<span class="text-muted-foreground">No card payments need attention</span>{/if}
        {:else if id === "budgets"}
          {#if budget?.used.length}
            {plural(budget.used.length, "budget")} in the forecast, {fmt(budget.monthly)} a month
            {#if budget.skipped.length}<div class="mt-0.5 text-xs text-muted-foreground">{budget.skipped.length} left out</div>{/if}
          {:else}<span class="text-muted-foreground">No budgets in the forecast</span>{/if}
        {/if}
      </div>
    </div>
    <Button {href} {onclick} size="sm" variant="outline" class={editCls} aria-label={label}>Edit</Button>
  </li>
{/snippet}

<section class="mt-4 border-t pt-4" aria-labelledby="review-title" aria-busy={!fc && !error}>
  <h3 id="review-title" class="text-sm font-semibold">Review your forecast</h3>
  <p class="mt-1 text-xs text-muted-foreground">This is what the forecast uses right now. Leave out anything you don’t want in it: you can change any of it later.</p>
  {#if error && !fc}
    <div role="alert" class="mt-3 text-sm">Couldn’t load your forecast: {error.message}
      <Button class="ml-2" size="sm" variant="outline" onclick={onretry}>Try again</Button></div>
  {:else if !fc}
    <p class="mt-3 text-sm text-muted-foreground">Loading your forecast…</p>
  {:else}
    <ul class="mt-2 flex flex-col">
      {@render row("Forecast account", "account", "Edit forecast account", undefined, openForecastSettings)}
      {@render row("Next income", "income", "Edit income", "#recurring")}
      {@render row("Upcoming bills", "bills", "Edit bills", "#recurring")}
      {@render row("Card payments", "cards", "Edit card payments", "#setup/accounts")}
      {@render row("Everyday budgets", "budgets", "Edit budgets", "#budget")}
    </ul>
  {/if}
</section>
