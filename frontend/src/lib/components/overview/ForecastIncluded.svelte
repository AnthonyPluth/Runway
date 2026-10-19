<script lang="ts">
  import { fmt0 } from "$lib/format";
  import type { Overview } from "$lib/types";
  import { groupSkipped } from "./budgetReasons";

  // "What's included": what the forecast counts besides scheduled items, as plain content a touch or keyboard user can
  // open (the summary is a native disclosure). Existing inputs only: the response's own budgets and skipped reasons.
  let { budget }: { budget: Overview["budget"] } = $props();
  const used = $derived(budget?.used ?? []);
  const groups = $derived(groupSkipped(budget?.skipped ?? []));
  const link = "font-medium text-foreground underline underline-offset-4";
</script>

<details class="mt-2 text-xs" data-testid="forecast-included">
  <summary class="cursor-pointer text-[13px] font-medium text-foreground">What’s included</summary>
  <div class="mt-2 flex flex-col gap-3 text-muted-foreground" role="group" aria-label="What the forecast includes">
    <section>
      <h4 class="font-semibold text-foreground">Recurring bills and income</h4>
      <p>Every scheduled item is counted on its day.</p>
    </section>
    <section>
      <h4 class="font-semibold text-foreground">Everyday budgets</h4>
      {#if used.length}
        <ul>
          {#each used as u (`${u.category}\u0000${u.account_id}`)}
            <li class="flex justify-between gap-3"><span class="min-w-0">{u.category} · {u.account}</span><span class="tabular-nums">{fmt0(u.amount)} a month</span></li>
          {/each}
        </ul>
        <p class="mt-1 font-medium text-foreground">{fmt0(budget?.monthly ?? 0)} a month in all</p>
      {:else if budget}
        <p>None of your budgets are spent in the forecast.</p>
      {:else}
        <p>Off: no budgets are counted. <a class={link} href="#budget">Turn on budgets</a> to include everyday spending.</p>
      {/if}
    </section>
    {#if groups.length}
      <section>
        <h4 class="font-semibold text-foreground">Left out</h4>
        <ul class="flex flex-col gap-2">
          {#each groups as g (g.title)}
            <li data-kind={g.kind}>
              <span class="font-medium text-foreground">{g.title}</span>: {g.categories.join(", ")}.
              {#if g.hint}{g.hint}{/if}
              {#if g.fix}<a class={link} href={g.fix.href}>{g.fix.label}</a>{/if}
            </li>
          {/each}
        </ul>
      </section>
    {:else if used.length}
      <p>Nothing is left out.</p>
    {/if}
  </div>
</details>
