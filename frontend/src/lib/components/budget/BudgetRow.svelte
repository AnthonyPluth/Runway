<script lang="ts">
  import { apiCall } from "$lib/contract";
  import { api } from "$lib/api";
  import { app } from "$lib/app.svelte";
  import { catLook, categories } from "$lib/categories.svelte";
  import { autosave } from "$lib/autosave";
  import BankBadge from "$lib/components/BankBadge.svelte";
  import CatIcon from "$lib/components/CatIcon.svelte";
  import { Button } from "$lib/components/ui/button";
  import { showTransactions } from "$lib/filters.svelte";
  import { barWidth, fmt, fmt0, monthShort } from "$lib/format";
  import Repeat from "@lucide/svelte/icons/repeat";
  import Ellipsis from "@lucide/svelte/icons/ellipsis";
  import { cn } from "$lib/utils";
  import { commas } from "$lib/commas";
  import { toast } from "svelte-sonner";
  import type { BudgetCategory, PayAccount } from "./types";
  import { selectCls } from "$lib/components/settings/ui";
  import { act } from "$lib/act";

  // One line per category: its name, spent "of" its budget (edited in place), then the bar underneath (a top-level
  // category's only: a subcategory's figures turn red when it's over). `budgets` is true in the Budgets card, where the
  // budget box is always shown. `account` is the card or account its spending goes on (its own, else its parent's,
  // else the one used most; a row's ⋯ menu sets it): its bank's logo sits on the category's emoji. What's still
  // expected this month (recurring payments that haven't come yet, `expected`) is the bar's lighter part after what's spent.
  // `income`: an income category, whose budget is what's expected to come in and `spent` what has. More is good: its
  // bar is green, going over isn't a warning, and it has no rollover or card. A row's ⋯ menu holds two settings:
  // whether a budget's left rolls over (a blue "Rolls over" label on the row says when it's on) and the account its
  // spending is paid with; only the paid-with part shows on a subcategory.
  let { c, month, sub = false, budgets = false, income = false, pace, payAccounts, account = null, onsave, onchanged }: {
    c: BudgetCategory; month: string; sub?: boolean; budgets?: boolean; income?: boolean; pace: number; payAccounts: PayAccount[];
    account?: string | null; onsave: (category: string, amount: string) => void; onchanged: () => void;
  } = $props();

  const brand = $derived(account ? app.state?.brands?.[account] : undefined);
  const accountName = $derived(account ? payAccounts.find((x) => x.id === account)?.name ?? brand?.institution ?? undefined : undefined);
  const avail = $derived(c.available ?? c.budget);
  const pct = $derived(avail != null && avail > 0 ? Math.max(0, c.spent / avail) : c.spent > 0 ? 1 : 0);
  const over = $derived(avail != null && c.spent > avail);
  const expected = $derived(c.expected ?? 0);
  const coming = $derived(expected > 0.005);
  // Over once what's still expected comes (and not over already).
  const overSoon = $derived(!income && !over && coming && avail != null && c.spent + expected > avail + 0.005);
  const expShare = $derived(avail != null && avail > 0 ? expected / avail : coming ? 1 : 0);
  const carried = $derived(c.carried ?? 0);
  const showPace = $derived(pace > 0 && pace < 1);
  const color = $derived(income ? "var(--good)" : catLook(c.name).color);
  const warnOver = $derived(over && !income);   // spending over its budget is red; income over what's expected isn't
  // Whole dollars, like the rest of the page; cents only when rounding would make a small amount read as $0.
  const money = (v: number | null) => (v != null && Math.abs(v) >= 0.005 && Math.abs(v) < 0.5 ? fmt(v) : fmt0(v));
  // A subcategory has no bar: its figure turns red when it's over (bold), or will be with what's still coming.
  const subNote = $derived(!sub || income ? "" : over ? `${money(c.spent - avail!)} over` : overSoon ? `${money(c.spent + expected - avail!)} over with what’s coming` : "");

  // Rollover's on/off and the account paid with live in the row's ⋯ menu; the row itself only says "Rolls over" (blue)
  // when the budget's left carries into next month. An income category has neither setting, a subcategory no rollover.
  let menu = $state(false);
  const canRoll = $derived(budgets && c.budget != null && !sub && !income);
  const canPay = $derived(!income && payAccounts.length);
  const hasMenu = $derived(canRoll || canPay);

  // Paid with: the card or account the spending goes on, and the forecast spends its budget there. Automatic is the
  // nearest parent's choice, else the account used most for it. Saved through /api/categories/pay-with, then the page
  // reloads (onchanged), so the bank badge and the subcategories' "Automatic" follow.
  const acctName = (id: string | null | undefined) => (id ? payAccounts.find((x) => x.id === id)?.name : undefined);
  const inherited = $derived(c.path.slice(0, -1).reverse().map((p) => categories.list.find((x) => x.name === p)).find((x) => x?.pay_with));
  // The automatic option reads like an explicit one, account first ("Visa (automatic)"), so it fits a phone; why it's
  // that account goes in the select's tooltip.
  const automatic = $derived.by(() => {
    const via = acctName(inherited?.pay_with);
    if (via) return { label: `${via} (automatic)`, why: `Automatic: the card set on ${inherited!.name}` };
    const usual = acctName(c.usual_account);
    return usual ? { label: `${usual} (automatic)`, why: "Automatic: the account used most for it" } : { label: "Automatic", why: "Automatic: the account used most for it, once it has spending" };
  });
  const payTitle = $derived(c.pay_with ? "Which card or account this spending goes on; the forecast uses it" : `${automatic.why}; the forecast uses it`);
  async function savePayWith(f: HTMLSelectElement) {
    await api("/api/categories/pay-with", { method: "POST", body: { name: c.name, pay_with: f.value } });
    onchanged();
  }

  // The category name and its spent amount open Transactions showing exactly what adds up to it (and what's still
  // expected, in its Upcoming).
  function open(e: MouseEvent) {
    e.preventDefault();
    showTransactions({ category: c.name, month, scope: "budget" });
  }

  // Rolling over: what's left at the end of a month adds to the next, starting this month.
  async function setRollover(on: boolean) {
    await act(async () => {
      await apiCall<"POST /api/budget">("/api/budget", { method: "POST", body: { category: c.name, rollover: on } });
      toast.success(on ? `${c.name} rolls over from this month on` : `${c.name} no longer rolls over`);
    });
    onchanged();
  }
</script>

{#snippet payWith()}
  <select class={cn(selectCls, "h-8 w-72 max-w-full truncate text-sm md:text-xs hover:border-input dark:bg-transparent phone:h-11 phone:w-full", !c.pay_with && "text-muted-foreground")}
    value={c.pay_with ?? ""} aria-label={`Account ${c.name} is paid with`} title={payTitle}
    use:autosave={(f) => savePayWith(f as HTMLSelectElement)}>
    <option value="">{automatic.label}</option>
    <optgroup label="Cards">
      {#each payAccounts.filter((x) => x.kind === "credit") as x (x.id)}<option value={x.id}>{x.name}</option>{/each}
    </optgroup>
    <optgroup label="Bank accounts">
      {#each payAccounts.filter((x) => x.kind !== "credit") as x (x.id)}<option value={x.id}>{x.name}</option>{/each}
    </optgroup>
  </select>
{/snippet}

<div class={cn("py-1", sub && "pl-5")}>
  <div class="flex min-h-8 flex-wrap items-center gap-x-2.5">
    <span class="relative shrink-0">
      <CatIcon name={c.name} size={sub ? 20 : 28} />
      <!-- The account as a small badge on the emoji, as Transactions puts it on a merchant's logo; not on a phone. -->
      {#if account}<BankBadge accountId={account} name={accountName ?? ""} size={sub ? "size-3 rounded text-[7px]" : "size-4 rounded-md text-[9px]"}
        class={cn("pointer-events-auto phone:hidden", sub ? "-right-1 -bottom-1" : "lg:-right-1.5 lg:-bottom-1.5")} />{/if}
    </span>
    <a href="#transactions" onclick={open}
      class={cn("max-w-full min-w-0 truncate hover:underline", sub ? "text-muted-foreground" : "font-semibold")}>{c.name}</a>
    {#if canRoll && c.rollover_from}
      <span class="inline-flex items-center gap-1 rounded-md px-1.5 text-xs whitespace-nowrap text-primary"
        title={`What's left each month carries into the next (since ${monthShort(c.rollover_from, true)}); the row's ⋯ menu turns it off`}>
        <Repeat class="size-3" aria-hidden="true" />Rolls over
      </span>
    {/if}
    <span class="ml-auto inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap tabular-nums">
      <a href="#transactions" onclick={open} class={cn("hover:underline", sub && warnOver && "font-semibold text-destructive", sub && overSoon && "text-destructive")}
        title={subNote || undefined}>{money(c.spent)}{#if subNote}<span class="sr-only">{` (${subNote})`}</span>{/if}</a>
      {#if c.budget != null || budgets}<span class="text-muted-foreground">of</span>{/if}
      <!-- The "$" sits just before the number, so it reads "$60" like the spent figure beside it. -->
      <span class="group/money relative inline-flex items-center">
        <span aria-hidden="true" class={cn("pointer-events-none absolute left-1.5 text-sm text-muted-foreground", c.budget == null && "hidden group-focus-within/money:inline")}>$</span>
        <input type="number" min="0" step="10" value={c.budget ?? ""} {@attach commas} placeholder={c.budget == null ? (sub ? "—" : income ? "Expected" : "Budget") : ""}
          aria-label={`Budget for ${c.name}`} onchange={(e) => onsave(c.name, e.currentTarget.value)}
          class={cn("h-9 w-20 rounded-md border border-transparent bg-transparent py-1 pr-1 text-sm tabular-nums outline-none hover:border-input focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 sm:h-7 sm:w-24",
            c.budget == null ? "pl-2 placeholder:text-primary focus:pl-[15px]" : "pl-[15px]")} />
      </span>
    </span>
    {#if hasMenu}
      <Button variant="ghost" size="icon" class={cn("size-8 shrink-0 text-muted-foreground hover:text-foreground focus-visible:opacity-100",
        menu ? "opacity-100" : "hoverable:opacity-0 hoverable:group-hover/family:opacity-100 hoverable:group-focus-within/family:opacity-100")}
        aria-label={`Actions for ${c.name}`} aria-expanded={menu} onclick={() => (menu = !menu)}><Ellipsis /></Button>
    {/if}
  </div>
  {#if menu}
    <div class="flex flex-wrap items-center gap-x-3 gap-y-2 px-1 pt-1 pb-1 sm:pl-[38px]">
      {#if canRoll}
        <button type="button" aria-pressed={!!c.rollover_from} onclick={() => setRollover(!c.rollover_from)}
          title={c.rollover_from ? `What's left each month carries into the next (since ${monthShort(c.rollover_from, true)}). Click to stop.`
            : "Carry what's left at the end of each month into the next"}
          class={cn("inline-flex cursor-pointer items-center gap-1 rounded-md border px-2.5 py-1.5 text-sm whitespace-nowrap hover:bg-muted focus-visible:bg-muted focus-visible:outline-none",
            c.rollover_from ? "border-primary/40 text-primary" : "border-transparent text-muted-foreground")}>
          <Repeat class="size-3.5" aria-hidden="true" />{c.rollover_from ? "Rolls over" : "Roll over"}
        </button>
      {/if}
      {#if canPay}
        <label class="flex items-center gap-2 text-xs text-muted-foreground">Paid with{@render payWith()}</label>
      {/if}
      <Button variant="link" size="sm" class="h-auto justify-start p-0" onclick={() => (menu = false)}>Cancel</Button>
    </div>
  {/if}
  {#if c.budget != null && !sub}
    <div class="flex items-center gap-3 sm:pl-[38px]">
      <div class="relative h-2 min-w-28 flex-1 rounded-full bg-muted" role="img"
        aria-label={income ? `${Math.round(pct * 100)}% of expected income received${coming ? `, ${Math.round(expShare * 100)}% more scheduled` : ""}`
          : `${Math.round(pct * 100)}% of budget used${coming ? `, ${Math.round(expShare * 100)}% more coming` : ""}`}>
        <!-- What's still coming: drawn from the start, under what's spent, so the lighter part shows after it. -->
        {#if coming && !over}
          <div class={cn("absolute inset-y-0 left-0 rounded-full opacity-45", overSoon && "bg-destructive")} style:background={overSoon ? undefined : color}
            style:width={barWidth(pct + expShare)} data-expected></div>
        {/if}
        <div class={cn("relative h-full rounded-full", warnOver && "bg-destructive")} style:background={warnOver ? undefined : color}
          style:width={barWidth(pct)}></div>
        {#if showPace}
          <div class="absolute -top-[3px] -bottom-[3px] w-0.5 rounded-sm bg-muted-foreground" style:left={`${(pace * 100).toFixed(1)}%`}
            title="Where you'd be at an even pace today"></div>
        {/if}
      </div>
      <!-- At least wide enough for "▲ $1,234 over with what’s coming", so the bars end at the same x whatever it says (a longer
           text, with five-figure amounts, takes the room it needs from its bar). -->
      <span class="shrink-0 text-right text-xs whitespace-nowrap tabular-nums sm:min-w-52">
        {#if income}
          <!-- What's still to come of what's expected, and how much of it is scheduled (paychecks in the forecast). -->
          {#if over}<span class="font-semibold text-good">▲ {money(c.spent - avail!)} over</span>
          {:else}<span class="text-muted-foreground">{money(avail! - c.spent)} to come{coming ? ` · ${money(expected)} scheduled` : ""}</span>{/if}
        {:else if over}<span class="font-semibold text-destructive">▲ {money(c.spent - avail!)} over</span>
        {:else if overSoon}<span class="text-destructive">▲ {money(c.spent + expected - avail!)} over with what’s coming</span>
        {:else if coming}<span class="text-muted-foreground">{money(Math.max(0, avail! - c.spent - expected))} left · {money(expected)} coming</span>
        {:else if showPace && c.spent > avail! * pace * 1.1}<span class="text-muted-foreground">{money(c.left)} left · ahead of pace</span>
        {:else if Math.abs(c.spent) > 0.005}<span class="text-muted-foreground">{money(c.left)} left</span>{/if}
      </span>
    </div>
  {/if}
  {#if c.budget != null && carried > 0.005}
    <p class={cn("mt-0.5 text-xs text-muted-foreground tabular-nums", sub ? "sm:pl-[30px]" : "sm:pl-[38px]")}>{fmt0(c.budget)} + {money(carried)} rolled over from earlier months</p>
  {/if}
</div>
