<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import * as Sheet from "$lib/components/ui/sheet";
  import { fmt } from "$lib/format";
  import type { DateRow, ValuationModel } from "$lib/valuationDates";
  import { UNAVAILABLE } from "$lib/valuationDates";

  // The "Valuation dates" action next to a headline total and the sheet it opens: which contributors the total is made of,
  // each with its source and valuation date, and "Values from different dates" when they differ. It only reads what the
  // page already loaded (opening it sends nothing), and the list it explains stays where it is: `onshow` closes the sheet and
  // takes you to that list, which is where anything is left out or counted again.
  let { title, total, model, listName, onshow, class: className = "" }: {
    title: string; total: string; model: ValuationModel; listName: string; onshow: () => void; class?: string;
  } = $props();
  let open = $state(false);

  function show() { open = false; setTimeout(onshow, 0); }
</script>

{#snippet rows(list: DateRow[], muted: boolean)}
  <ul class="divide-y divide-border">
    {#each list as r (r.key)}
      <li class="py-2" data-testid="valuation-row">
        <div class="flex items-start justify-between gap-3">
          <span class={muted ? "min-w-0 text-muted-foreground" : "min-w-0"}>{r.name}<span class="block text-xs text-muted-foreground">{r.group} · {r.source}</span></span>
          {#if r.amount != null}<span class="tabular-nums">{fmt(r.amount)}</span>{/if}
        </div>
        <p class={r.day ? "mt-0.5 text-xs" : "mt-0.5 text-xs font-medium text-warning"}>{r.dateLabel}: {r.dateText}</p>
        {#if r.note}<p class="mt-0.5 text-xs text-muted-foreground">{r.note}</p>{/if}
      </li>
    {/each}
  </ul>
{/snippet}

<Button variant="outline" size="sm" class={className} aria-haspopup="dialog" onclick={() => (open = true)}>Valuation dates</Button>

<Sheet.Root bind:open>
  <Sheet.Content>
    <Sheet.Header>
      <Sheet.Title>Valuation dates</Sheet.Title>
      <Sheet.Description>What the {title} of {total} is made of, and when each part was valued.</Sheet.Description>
    </Sheet.Header>
    <div class="grid gap-5 px-4 pb-6 text-sm" data-testid="valuation-sheet">
      <section aria-labelledby="vd-summary" class="rounded-lg border border-border p-3">
        <h3 id="vd-summary" class="font-semibold" data-testid="valuation-headline">{model.summary.headline}</h3>
        {#if model.summary.detail}<p class="mt-0.5 text-muted-foreground">{model.summary.detail}</p>{/if}
        {#if model.summary.mixed || model.summary.groups.some((g) => g.day == null)}
          <ul class="mt-2 grid gap-1" data-testid="valuation-groups">
            {#each model.summary.groups as g (g.label)}
              <li><span class={g.day ? "font-medium" : "font-medium text-warning"}>{g.label}</span><span class="text-muted-foreground">{` · ${g.names.join(", ")}`}</span></li>
            {/each}
          </ul>
        {/if}
      </section>

      <section aria-labelledby="vd-counted">
        <h3 id="vd-counted" class="mb-1 font-semibold">Counted in the total</h3>
        {#if model.counted.length}{@render rows(model.counted, false)}{:else}<p class="text-muted-foreground">Nothing yet.</p>{/if}
      </section>

      {#if model.left.length}
        <section aria-labelledby="vd-left" data-testid="valuation-left">
          <h3 id="vd-left" class="mb-1 font-semibold">Not counted</h3>
          <p class="mb-1 text-xs text-muted-foreground">Shown for reference. These are not in the total or in the dates above.</p>
          {@render rows(model.left, true)}
        </section>
      {/if}

      <section aria-labelledby="vd-notes" class="grid gap-2">
        <h3 id="vd-notes" class="font-semibold">Other dates</h3>
        {#each model.notes as n (n.title)}
          <p><span class="font-medium">{n.title}.</span> <span class="text-muted-foreground">{n.text}</span></p>
        {/each}
      </section>

      <p class="text-xs text-muted-foreground">{UNAVAILABLE} means the source didn't say, not that the value is current.</p>
      <Button variant="outline" onclick={show}>Show {listName}</Button>
    </div>
  </Sheet.Content>
</Sheet.Root>
