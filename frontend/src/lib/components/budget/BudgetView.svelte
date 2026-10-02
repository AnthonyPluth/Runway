<script lang="ts" module>
  // The month you're looking at survives redraws (a sync, moving to Transactions and back), like the classic app.
  let budgetMonth: string | null = null;
</script>

<script lang="ts">
  import { api } from "$lib/api";
  import { loadCategories } from "$lib/categories.svelte";
  import BudgetRow from "$lib/components/budget/BudgetRow.svelte";
  import type { BudgetCategory, BudgetMonth, Family } from "$lib/components/budget/types";
  import MonthPicker from "$lib/components/MonthPicker.svelte";
  import StatStrip from "$lib/components/StatStrip.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import Group from "$lib/components/ui/group/Group.svelte";
  import { fmt0, plural, thisMonth } from "$lib/format";
  import { cn } from "$lib/utils";
  import { commas } from "$lib/commas";
  import { toast } from "svelte-sonner";

  let month = $state(budgetMonth ?? thisMonth());
  let b = $state<BudgetMonth | null>(null);
  let error = $state("");

  // Load the month again after every change; what's on screen stays until the new numbers arrive
  // (and only the latest request draws, if you click through months quickly).
  let seq = 0;
  async function refresh() {
    const n = ++seq;
    try {
      await loadCategories();
      const r = await api<BudgetMonth>(`/api/budget?month=${month}`);
      if (n === seq) { b = r; error = ""; }
    } catch (err) { if (n === seq) error = (err as Error).message; }
  }
  refresh();

  function pick(m: string) {
    month = budgetMonth = m;
    refresh();
  }

  async function saveBudget(category: string, amount: string) {
    try { await api("/api/budget", { method: "POST", body: { category, amount } }); toast.success(amount ? "Budget saved" : "Budget removed"); refresh(); }
    catch (err) { toast.error((err as Error).message); }
  }
  let newCat = $state("");
  function addBudget(e: Event & { currentTarget: HTMLInputElement }) {
    if (newCat) saveBudget(newCat, e.currentTarget.value); else toast.error("Choose a category first");
  }

  const view = $derived.by(() => {
    if (!b) return null;
    // Group into families: a top-level category plus everything under it. A category's "spent" already includes its subcategories.
    const families: Family[] = [];
    for (const c of b.categories) {
      if (!c.depth) families.push({ top: c, kids: [] });
      else families.find((f) => f.top.name === c.top)?.kids.push(c);
    }
    const budgeted = new Set(b.categories.filter((c) => c.budget != null).map((c) => c.name));
    // Count a budget only when nothing above it has one, so nested budgets aren't counted twice.
    const countsToward = (c: BudgetCategory) => c.budget != null && !c.path.slice(0, -1).some((a) => budgeted.has(a));
    const isBudgeted = (f: Family) => f.top.budget != null || f.kids.some((k) => k.budget != null);
    const inBudget = families.filter(isBudgeted);
    const notBudget = families.filter((f) => !isBudgeted(f) && f.top.spent > 0.005);
    const unusedTops = families.filter((f) => !isBudgeted(f) && !(f.top.spent > 0.005));
    // Totals without double counting: a parent's budget covers its subcategories. What's left is each budget's own
    // remainder added up: one that's over doesn't eat into what another still has (its overage is counted apart).
    // A budget that rolls over has what earlier months left on top of its own amount (`available`).
    let totBudget = 0, totCarried = 0, totSpent = 0, totLeft = 0, totOver = 0, overCount = 0;
    for (const f of inBudget) for (const c of [f.top, ...f.kids]) if (countsToward(c)) {
      const avail = c.available ?? c.budget!;
      totBudget += c.budget!; totCarried += c.carried ?? 0; totSpent += c.spent;
      totLeft += Math.max(0, avail - c.spent);
      if (c.spent - avail > 0.005) { totOver += c.spent - avail; overCount++; }
    }
    const allSpent = families.reduce((s, f) => s + Math.max(0, f.top.spent), 0);
    return { inBudget, notBudget, unusedTops, countsToward, totBudget, totCarried, totSpent, totLeft, totOver, overCount, otherSpent: allSpent - totSpent,
      // pace: the share of the month gone, counting today as half gone (at the end of today the marker would sit a
      // day ahead of the date all day long)
      pace: b.month !== thisMonth() ? (b.day >= b.days_in_month ? 1 : 0) : Math.max(0, b.day - 0.5) / b.days_in_month };
  });
</script>

{#snippet family(f: Family, budgets: boolean, bm: BudgetMonth, pace: number, counts: (c: BudgetCategory) => boolean)}
  <div class="group/family px-4 py-1">
    {#each [f.top, ...f.kids.filter((k) => budgets || k.spent > 0.005)] as c (c.name)}
      <BudgetRow {c} month={bm.month} sub={c !== f.top} {budgets} counts={counts(c)} {pace} payAccounts={bm.pay_accounts}
        onsave={saveBudget} onchanged={refresh} />
    {/each}
  </div>
{/snippet}

<div class="mb-6 flex flex-wrap items-center justify-end gap-4">
  <MonthPicker month={b?.month ?? month} onchange={pick} />
</div>

{#if error && !b}
  <Card.Root>
    <Card.Content>
      <p class="text-sm">Something went wrong: {error}</p>
      <Button class="mt-3" variant="outline" onclick={refresh}>Try again</Button>
    </Card.Content>
  </Card.Root>
{:else if !b || !view}
  <div class="h-40 animate-pulse rounded-xl bg-muted"></div>
{:else}
  {@const v = view}
  {@const over = v.totLeft < 0.005 && v.totOver > 0.005}
  <!-- The hero is what's spent in the budgeted categories; the strip has what it's measured against and the rest. -->
  <section class="mb-8">
    <div class="text-[15px] text-muted-foreground">Spent in budgeted categories</div>
    <div class={cn("text-[44px] leading-none font-extrabold tracking-[-0.04em] tabular-nums md:text-[56px]", over && "text-destructive")}>{fmt0(v.totSpent)}</div>
    <p class={cn("mt-2 text-[15px] tabular-nums", over ? "font-semibold text-destructive" : "text-muted-foreground")}>
      {v.totBudget <= 0 ? "Set a budget below" : [v.totLeft > 0.005 || !v.totOver ? `${fmt0(v.totLeft)} left` : "",
        v.totOver > 0.005 ? `▲ ${fmt0(v.totOver)} over in ${plural(v.overCount, "budget")}` : ""].filter(Boolean).join(" · ")}
    </p>
    <StatStrip class="mt-5" items={[
      { label: "Budgeted", value: fmt0(v.totBudget), sub: v.totCarried > 0.005 ? `each month · plus ${fmt0(v.totCarried)} rolled over` : "each month" },
      { label: "Other spending", value: fmt0(v.otherSpent + b.uncategorized),
        sub: b.uncategorized > 0 ? `incl. ${fmt0(b.uncategorized)} uncategorized` : "in categories without a budget" },
      ...(b.income ? [{ label: "Money in", value: fmt0(b.income) }] : []),
    ]} />
  </section>

  <Group title="Budgets" inset="3.4rem" class="mb-8">
      {#if v.inBudget.length}
        {#each v.inBudget as f (f.top.name)}{@render family(f, true, b, v.pace, v.countsToward)}{/each}
      {:else}
        <p class="cell text-sm text-muted-foreground">No budgets yet. Set one below.</p>
      {/if}
  </Group>

  <Group title="Not budgeted" inset="3.4rem" class="mb-4">
      {#each v.notBudget as f (f.top.name)}{@render family(f, false, b, v.pace, v.countsToward)}{/each}
      {#if v.unusedTops.length}
        <div class="flex min-h-12 items-center gap-2.5 px-2 py-2">
          <select bind:value={newCat} aria-label="Category to budget"
            class="h-10 min-w-0 cursor-pointer sm:h-9 rounded-lg border border-transparent bg-transparent text-primary py-1 pr-8 pl-2.5 text-sm outline-none hover:border-input focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 [&_option]:bg-popover">
            <option value="">Another category…</option>
            {#each v.unusedTops.flatMap((f) => [f.top, ...f.kids]) as c (c.name)}
              <option value={c.name}>{c.icon ? `${c.icon}  ` : ""}{c.parent ? `${c.parent} > ${c.name}` : c.name}</option>
            {/each}
          </select>
          <span class="group/money relative ml-auto inline-flex items-center">
            <span aria-hidden="true" class="pointer-events-none absolute left-2 hidden text-sm text-muted-foreground group-focus-within/money:inline">$</span>
            <input type="number" min="0" step="10" placeholder="Budget" aria-label="Budget for the chosen category" {@attach commas} onchange={addBudget}
              class="h-10 w-20 rounded-md border border-transparent bg-transparent py-1 pr-1 pl-2 sm:h-8 focus:pl-5 text-sm tabular-nums outline-none placeholder:text-primary hover:border-input focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 sm:w-24" />
          </span>
        </div>
      {/if}
  </Group>
{/if}
