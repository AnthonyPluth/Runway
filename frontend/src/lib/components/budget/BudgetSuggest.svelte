<script lang="ts">
  import { act, errMsg } from "$lib/act";
  import type { BudgetSuggestion, BudgetSuggestions } from "$lib/api-types";
  import { apiCall } from "$lib/contract";
  import AiButton from "$lib/components/AiButton.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Sheet from "$lib/components/ui/sheet";
  import { fmt0, monthShort, plural } from "$lib/format";
  import { toast } from "svelte-sonner";

  // Suggested budgets (GET /api/budget/suggestions; the method is in runway/domain/budget_suggest.py): nothing is saved
  // until you apply one, or all of them, through POST /api/budget like a budget you type. `month` is the month on screen
  // (a month still to come counts its own recurring payments); `onchanged` reloads the page after a budget is set.
  let { month, onchanged }: { month: string; onchanged?: () => void } = $props();

  let open = $state(false);
  let s = $state<BudgetSuggestions | null>(null);
  let error = $state("");
  let busy = $state(false);

  const differs = (x: BudgetSuggestion) => x.budget == null || Math.abs(x.budget - x.suggested) >= 0.005;
  const pending = $derived((s?.suggestions ?? []).filter(differs));

  // What a suggestion is made from, in a few words.
  function basis(x: BudgetSuggestion): string {
    const parts = [];
    if (s?.months) parts.push(`typical month ${fmt0(x.typical)}`);
    if (x.recurring > 0.005) parts.push(`bills ${fmt0(x.recurring)}`);
    return parts.join(" · ");
  }

  async function load() {
    error = "";
    try { s = await apiCall<"GET /api/budget/suggestions">(`/api/budget/suggestions?month=${month}`); }
    catch (err) { s = null; error = errMsg(err); }
  }
  // Each time the sheet opens, for the month on screen then (what it showed last time isn't kept: it may be out of date).
  $effect(() => { if (open) { s = null; load(); } });

  const save = (x: BudgetSuggestion) =>
    apiCall<"POST /api/budget">("/api/budget", { method: "POST", body: { category: x.category, amount: x.suggested } });

  async function apply(x: BudgetSuggestion) {
    const ok = await act(async () => {
      const r = await save(x);
      const raised = (r.raised ?? []).map((p) => `${p.category} raised to ${fmt0(p.amount)}`);
      toast.success([`${x.category} budget set to ${fmt0(x.suggested)}`, ...raised].join(" · "));
    }, { busy: (on) => (busy = on) });
    await load();
    if (ok) onchanged?.();
  }

  // One at a time, in the list's order; a failure stops there and says which, keeping the ones set before it.
  async function applyAll() {
    const todo = [...pending];
    let done = 0;
    await act(async () => {
      for (const x of todo) {
        try { await save(x); } catch (err) { throw new Error(`${x.category}: ${errMsg(err)}`, { cause: err }); }
        done++;
      }
      toast.success(`${plural(done, "budget")} set`);
    }, { busy: (on) => (busy = on),
         onError: (m) => toast.error(done ? `${plural(done, "budget")} set, then stopped at ${m}` : m) });
    await load();
    if (done) onchanged?.();
  }
</script>

<!-- On a phone just the icon, so it fits beside the month picker. -->
<AiButton label="Suggest budgets" onclick={() => (open = true)} />
<Sheet.Root bind:open>
  <Sheet.Content>
    <Sheet.Header>
      <Sheet.Title>Suggested budgets</Sheet.Title>
      <Sheet.Description>
        {#if s?.months}
          Your typical month over the last {plural(s.months, "full month")} ({monthShort(s.first!)}–{monthShort(s.last!)}), or the
          recurring bills due in {monthShort(s.recurring_month, true)} if they’re more, rounded up to $5.
        {:else if s}
          No full month of spending yet, so just the recurring bills due in {monthShort(s.recurring_month, true)}, rounded up to $5.
        {:else}
          From your recent spending and the bills coming up.
        {/if}
        Nothing changes until you apply one.
      </Sheet.Description>
    </Sheet.Header>
    <div class="px-4">
      {#if error}
        <p class="text-sm">Couldn’t work out suggestions: {error}</p>
        <Button class="mt-3" variant="outline" onclick={load}>Try again</Button>
      {:else if !s}
        <div class="h-24 animate-pulse rounded-lg bg-muted motion-reduce:animate-none"></div>
      {:else if !s.suggestions.length}
        <p class="text-sm text-muted-foreground">Nothing to suggest yet: there’s no spending or bills to go on.</p>
      {:else}
        <ul class="divide-y divide-border" aria-label="Suggestions">
          {#each s.suggestions as x (x.category)}
            <li class="flex min-h-14 items-center gap-3 py-2">
              <div class="min-w-0 flex-1">
                <div class="truncate text-[15px] font-medium">{x.category}</div>
                <div class="truncate text-[13px] text-muted-foreground">{basis(x)}</div>
              </div>
              <div class="shrink-0 text-right tabular-nums">
                <div class="text-[15px] font-semibold">{fmt0(x.suggested)}</div>
                <div class="text-[13px] text-muted-foreground">{x.budget == null ? "no budget" : `now ${fmt0(x.budget)}`}</div>
              </div>
              {#if differs(x)}
                <Button size="sm" variant="outline" class="h-10 w-16 shrink-0 sm:h-8" disabled={busy} onclick={() => apply(x)}
                  aria-label={`Apply ${fmt0(x.suggested)} to ${x.category}`}>Apply</Button>
              {:else}
                <span class="w-16 shrink-0 text-center text-[13px] text-muted-foreground">Set</span>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    </div>
    {#if s && pending.length}
      <Sheet.Footer>
        <Button disabled={busy} onclick={applyAll}>Apply {pending.length === 1 ? "it" : `all ${pending.length}`}</Button>
      </Sheet.Footer>
    {/if}
  </Sheet.Content>
</Sheet.Root>
