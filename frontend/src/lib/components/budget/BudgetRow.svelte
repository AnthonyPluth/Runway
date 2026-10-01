<script lang="ts">
  import { api } from "$lib/api";
  import { catLook } from "$lib/categories.svelte";
  import CatIcon from "$lib/components/CatIcon.svelte";
  import { showTransactions } from "$lib/filters.svelte";
  import { barWidth, fmt, fmt0, monthShort } from "$lib/format";
  import { isPhone } from "$lib/phone.svelte";
  import Repeat from "@lucide/svelte/icons/repeat";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import type { BudgetCategory, PayAccount } from "./types";

  // One line per category: its name, spent "of" its budget (edited in place), then the bar underneath.
  // `budgets` is true in the Budgets card, where the budget box is always shown and you can choose the card it's paid with.
  // On a phone the budget is just a figure: setting budgets, the card and rolling over are for a computer.
  let { c, month, sub = false, budgets = false, counts = false, pace, payAccounts, onsave, onchanged }: {
    c: BudgetCategory; month: string; sub?: boolean; budgets?: boolean; counts?: boolean; pace: number; payAccounts: PayAccount[];
    onsave: (category: string, amount: string) => void; onchanged: () => void;
  } = $props();

  const acctName = (id: string | null) => (id ? payAccounts.find((x) => x.id === id)?.name : undefined);
  const avail = $derived(c.available ?? c.budget);
  const pct = $derived(avail != null && avail > 0 ? Math.max(0, c.spent / avail) : c.spent > 0 ? 1 : 0);
  const over = $derived(avail != null && c.spent > avail);
  const carried = $derived(c.carried ?? 0);
  const showPace = $derived(pace > 0 && pace < 1);
  const color = $derived(catLook(c.name).color);
  // Whole dollars, like the rest of the page; cents only when rounding would make a small amount read as $0.
  const money = (v: number | null) => (v != null && Math.abs(v) >= 0.005 && Math.abs(v) < 0.5 ? fmt(v) : fmt0(v));

  // The category name and the spent amount both open Transactions showing exactly what adds up to that number.
  function open(e: MouseEvent) {
    e.preventDefault();
    showTransactions({ category: c.name, month, scope: "budget" });
  }

  // Rolling over: what's left at the end of a month adds to the next, starting this month.
  async function setRollover(on: boolean) {
    try {
      await api("/api/budget", { method: "POST", body: { category: c.name, rollover: on } });
      toast.success(on ? `${c.name} rolls over from this month on` : `${c.name} no longer rolls over`);
    } catch (err) { toast.error((err as Error).message); }
    onchanged();
  }

  // Which card a budget is paid with: out of the way until you want to change it.
  let choosing = $state(false);
  let done = false;
  function focus(el: HTMLSelectElement) { el.focus(); }
  async function finish(sel: HTMLSelectElement, save: boolean) {
    if (done) return;
    done = true;
    if (save && sel.value !== (c.pay_with || "")) {
      try { await api("/api/budget", { method: "POST", body: { category: c.name, pay_with: sel.value } }); toast.success("Saved"); }
      catch (err) { toast.error((err as Error).message); }
    }
    choosing = false;
    onchanged();
  }
</script>

<div class={cn("py-2", sub && "pl-5")}>
  <div class="flex min-h-9 flex-wrap items-center gap-x-2.5">
    {#if !sub}<CatIcon name={c.name} size={28} solid />{/if}
    <a href="#transactions" onclick={open}
      class={cn("max-w-full min-w-0 truncate hover:underline", sub ? "text-muted-foreground" : "font-semibold")}>{c.name}</a>
    {#if budgets && c.budget != null && counts && !isPhone()}
      {#if choosing}
        {@const usual = acctName(c.usual_account)}
        <select use:focus data-editor aria-label={`Account ${c.name} is paid with`}
          class="h-8 max-w-48 min-w-0 cursor-pointer rounded-md border border-input bg-transparent px-2 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30 [&_option]:bg-popover [&>optgroup]:bg-popover"
          onchange={(e) => finish(e.currentTarget, true)} onblur={(e) => finish(e.currentTarget, true)}
          onkeydown={(e) => { if (e.key === "Escape") finish(e.currentTarget, false); }}>
          <option value="" selected={!c.pay_with}>{usual ? `Automatic (usually ${usual})` : "Automatic"}</option>
          <optgroup label="Cards">
            {#each payAccounts.filter((x) => x.kind === "credit") as x (x.id)}<option value={x.id} selected={x.id === c.pay_with}>{x.name}</option>{/each}
          </optgroup>
          <optgroup label="Bank accounts">
            {#each payAccounts.filter((x) => x.kind !== "credit") as x (x.id)}<option value={x.id} selected={x.id === c.pay_with}>{x.name}</option>{/each}
          </optgroup>
        </select>
      {:else}
        {@const chosen = acctName(c.pay_with)}
        <button type="button" title="Which card or account this spending goes on (used by the budget forecast)"
          onclick={() => { done = false; choosing = true; }}
          class={cn("cursor-pointer rounded-md px-1.5 py-0.5 text-xs whitespace-nowrap text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:bg-muted focus-visible:text-foreground focus-visible:outline-none",
            !chosen && "opacity-0 group-hover/family:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100")}>
          {chosen ?? "Set card"}
        </button>
      {/if}
    {/if}
    {#if budgets && c.budget != null && !sub && !isPhone()}
      <button type="button" aria-pressed={!!c.rollover_from} onclick={() => setRollover(!c.rollover_from)}
        title={c.rollover_from ? `What's left each month carries into the next (since ${monthShort(c.rollover_from, true)}). Click to stop.`
          : "Carry what's left at the end of each month into the next"}
        class={cn("inline-flex cursor-pointer items-center gap-1 rounded-md px-1.5 py-0.5 text-xs whitespace-nowrap hover:bg-muted focus-visible:bg-muted focus-visible:outline-none",
          c.rollover_from ? "text-primary" : "text-muted-foreground opacity-0 group-hover/family:opacity-100 focus-visible:opacity-100 hover:text-foreground [@media(hover:none)]:opacity-100")}>
        <Repeat class="size-3" aria-hidden="true" />{c.rollover_from ? "Rolls over" : "Roll over"}
      </button>
    {/if}
    <span class="ml-auto inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap tabular-nums">
      <a href="#transactions" onclick={open} title="See the transactions behind this amount"
        class="underline decoration-muted-foreground/50 decoration-dotted underline-offset-4 hover:decoration-foreground">{money(c.spent)}</a>
      {#if isPhone()}
        {#if c.budget != null}<span class="text-muted-foreground">of</span><span>{fmt0(c.budget)}</span>{/if}
      {:else}
      {#if c.budget != null || budgets}<span class="text-muted-foreground">of</span>{/if}
      <span class="group/money relative inline-flex items-center">
        <span aria-hidden="true" class={cn("pointer-events-none absolute left-2 text-sm text-muted-foreground", c.budget == null && "hidden group-focus-within/money:inline")}>$</span>
        <input type="number" min="0" step="10" value={c.budget ?? ""} placeholder={c.budget == null ? (sub ? "—" : "Budget") : ""}
          aria-label={`Budget for ${c.name}`} onchange={(e) => onsave(c.name, e.currentTarget.value)}
          class={cn("h-8 w-20 rounded-md border border-transparent bg-transparent py-1 pr-1 text-sm tabular-nums outline-none hover:border-input focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 sm:w-24",
            c.budget == null ? "pl-2 placeholder:text-primary focus:pl-5" : "pl-5")} />
      </span>
      {/if}
    </span>
  </div>
  {#if c.budget != null}
    <div class={cn("mt-0.5 flex items-center gap-3", !sub && "sm:pl-[38px]")}>
      <div class={cn("relative min-w-28 flex-1 rounded-full bg-muted", sub ? "h-1.5 opacity-85" : "h-2")} role="img" aria-label={`${Math.round(pct * 100)}% of budget used`}>
        <div class={cn("h-full rounded-full", over && "bg-destructive")} style:background={over ? undefined : color}
          style:width={barWidth(pct)}></div>
        {#if showPace}
          <div class="absolute -top-[3px] -bottom-[3px] w-0.5 rounded-sm bg-muted-foreground" style:left={`${(pace * 100).toFixed(1)}%`}
            title="Where you'd be at an even pace today"></div>
        {/if}
      </div>
      <!-- Fixed width so the bars end at the same x whether the text says "left", "over" or "ahead of pace". -->
      <span class="shrink-0 text-right text-xs whitespace-nowrap tabular-nums sm:w-48">
        {#if over}<span class="font-semibold text-destructive">▲ {money(c.spent - avail!)} over</span>
        {:else if showPace && c.spent > avail! * pace * 1.1}<span class="text-muted-foreground">{money(c.left)} left · ahead of pace</span>
        {:else if c.spent > 0.005}<span class="text-muted-foreground">{money(c.left)} left</span>{/if}
      </span>
    </div>
    {#if carried > 0.005}
      <p class={cn("mt-1 text-xs text-muted-foreground tabular-nums", !sub && "sm:pl-[38px]")}>{fmt0(c.budget)} + {money(carried)} rolled over from earlier months</p>
    {/if}
  {/if}
</div>
