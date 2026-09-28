<script lang="ts" module>
  // The month you're looking at survives redraws (a sync, moving to Transactions and back), like the classic app.
  let budgetMonth: string | null = null;
</script>

<script lang="ts">
  import { api } from "$lib/api";
  import { loadCategories } from "$lib/categories.svelte";
  import BudgetRow from "$lib/components/budget/BudgetRow.svelte";
  import type { BudgetCategory, BudgetMonth, Family } from "$lib/components/budget/types";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { fmt, fmt0, monthLabel, plural, thisMonth } from "$lib/format";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import ChevronLeft from "@lucide/svelte/icons/chevron-left";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";

  let { page: _page = "", sub: _sub = "" }: { page?: string; sub?: string } = $props();

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

  function shift(n: number) {
    const [y, m] = (b?.month ?? month).split("-").map(Number);
    const d = new Date(y, m - 1 + n, 1);
    month = budgetMonth = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
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
      pace: b.day / b.days_in_month };   // pace: the share of the month gone
  });
</script>

{#snippet family(f: Family, budgets: boolean, bm: BudgetMonth, pace: number, counts: (c: BudgetCategory) => boolean)}
  <div class="group/family border-b py-1 last:border-b-0">
    {#each [f.top, ...f.kids.filter((k) => budgets || k.spent > 0.005)] as c (c.name)}
      <BudgetRow {c} month={bm.month} sub={c !== f.top} {budgets} counts={counts(c)} {pace} payAccounts={bm.pay_accounts}
        onsave={saveBudget} onchanged={refresh} />
    {/each}
  </div>
{/snippet}

<div class="mb-6 flex flex-wrap items-center justify-between gap-4">
  <h1 class="text-3xl font-semibold tracking-tight">Budget</h1>
  <div class="inline-flex h-9 items-center rounded-lg bg-muted p-[3px]">
    <Button variant="ghost" size="icon" class="size-8" aria-label="Previous month" onclick={() => shift(-1)}><ChevronLeft /></Button>
    <span class="min-w-36 rounded-md bg-background px-3 py-1 text-center text-sm font-medium shadow-sm dark:bg-input/30" aria-live="polite">{monthLabel(b?.month ?? month)}</span>
    <Button variant="ghost" size="icon" class="size-8" aria-label="Next month" onclick={() => shift(1)}><ChevronRight /></Button>
  </div>
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
  <div class="mb-6 grid gap-4 md:grid-cols-3">
    {#each [
      { label: "Budgeted", value: fmt0(v.totBudget), sub: v.totCarried > 0.005 ? `monthly · plus ${fmt0(v.totCarried)} rolled over` : "monthly · repeats every month", alert: false },
      { label: "Spent in budgeted categories", value: fmt0(v.totSpent), alert: over,
        sub: v.totBudget <= 0 ? "Set a budget below" : [v.totLeft > 0.005 || !v.totOver ? `${fmt0(v.totLeft)} left` : "",
          v.totOver > 0.005 ? `▲ ${fmt0(v.totOver)} over in ${plural(v.overCount, "budget")}` : ""].filter(Boolean).join(" · ") },
      { label: "Other spending", value: fmt0(v.otherSpent + b.uncategorized), alert: false,
        sub: b.uncategorized > 0 ? `incl. ${fmt0(b.uncategorized)} uncategorized` : "in categories without a budget" },
    ] as t (t.label)}
      <Card.Root class={cn("gap-2", t.alert && "border-destructive")}>
        <Card.Header>
          <Card.Description>{t.label}</Card.Description>
          <Card.Title class={cn("text-2xl tabular-nums", t.alert && "text-destructive")}>{t.value}</Card.Title>
        </Card.Header>
        <Card.Content class={cn("text-sm text-muted-foreground tabular-nums", t.alert && "text-destructive")}>{t.sub}</Card.Content>
      </Card.Root>
    {/each}
  </div>

  <Card.Root class="mb-6">
    <Card.Header><Card.Title><h2>Budgets</h2></Card.Title></Card.Header>
    <Card.Content>
      {#if v.inBudget.length}
        {#each v.inBudget as f (f.top.name)}{@render family(f, true, b, v.pace, v.countsToward)}{/each}
      {:else}
        <p class="py-4 text-center text-sm text-muted-foreground">No budgets yet. Set one below.</p>
      {/if}
    </Card.Content>
  </Card.Root>

  <Card.Root class="mb-4">
    <Card.Header><Card.Title><h2>Not budgeted</h2></Card.Title></Card.Header>
    <Card.Content>
      {#each v.notBudget as f (f.top.name)}{@render family(f, false, b, v.pace, v.countsToward)}{/each}
      {#if v.unusedTops.length}
        <div class={cn("flex min-h-9 items-center gap-2.5 py-3", v.notBudget.length && "border-t")}>
          <select bind:value={newCat} aria-label="Category to budget"
            class="h-9 min-w-0 cursor-pointer rounded-md border border-transparent bg-transparent py-1 pr-8 pl-2.5 text-sm outline-none hover:border-input focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 [&_option]:bg-popover">
            <option value="">Another category…</option>
            {#each v.unusedTops.flatMap((f) => [f.top, ...f.kids]) as c (c.name)}
              <option value={c.name}>{c.icon ? `${c.icon}  ` : ""}{c.parent ? `${c.parent} > ${c.name}` : c.name}</option>
            {/each}
          </select>
          <span class="group/money relative ml-auto inline-flex items-center">
            <span aria-hidden="true" class="pointer-events-none absolute left-2 hidden text-sm text-muted-foreground group-focus-within/money:inline">$</span>
            <input type="number" min="0" step="10" placeholder="Budget" aria-label="Budget for the chosen category" onchange={addBudget}
              class="h-8 w-20 rounded-md border border-transparent bg-transparent py-1 pr-1 pl-2 focus:pl-5 text-sm tabular-nums outline-none placeholder:text-primary hover:border-input focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 sm:w-24" />
          </span>
        </div>
      {/if}
    </Card.Content>
  </Card.Root>

  {#if b.income}<p class="text-sm text-muted-foreground">Money in this month: {fmt(b.income)}</p>{/if}
{/if}
