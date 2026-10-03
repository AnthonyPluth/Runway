<script lang="ts">
  import { api } from "$lib/api";
  import { app } from "$lib/app.svelte";
  import AmountEdit from "$lib/components/AmountEdit.svelte";
  import CatIcon from "$lib/components/CatIcon.svelte";
  import Logo from "$lib/components/Logo.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { fmt, fmtDate, fmtDow } from "$lib/format";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import ChevronUp from "@lucide/svelte/icons/chevron-up";
  import type { ForecastEvent } from "$lib/types";
  import { toast } from "svelte-sonner";

  // What's coming up. Click an amount to change just that one occurrence; a recurring item's name opens it in Recurring, to
  // change every one. A churning card's annual fee (kind "fee") says which card payment it's in, instead of a balance.
  // `limit` is how many show before
  // "Show all"; `accounts` adds each one's account (Transactions shows several accounts' items together). After an amount
  // changes, `onchanged` loads the page's forecast again (in place: the page isn't drawn afresh). `onBudget`: the budget
  // line's balance by day (all the forecast's accounts together), from the first day it differs from the forecast's; each
  // day shows it beside its projected balance.
  let { events, limit = 8, accounts = false, all = $bindable(false), onchanged, onBudget }: {
    events: (ForecastEvent & { late_from?: string | null })[];
    limit?: number; accounts?: boolean; all?: boolean; onchanged: () => void; onBudget?: Record<string, number>;
  } = $props();
  const shown = $derived(all ? events : events.slice(0, limit));
  // A day at a time, under its date, with no dividers inside a day. A bank settles a day's payments together, so the
  // projected balance shows once a day for each account, under the day's items: the balance after its last item that day
  // (an annual fee is a card charge: it never moves one).
  const days = $derived.by(() => {
    type Ev = (typeof events)[number];
    const out: { date: string; rows: { e: Ev; i: number }[]; balances: { account?: string | null; amount: number }[]; budgeted?: number }[] = [];
    shown.forEach((e, i) => {
      if (out.at(-1)?.date !== e.date) out.push({ date: e.date, rows: [], balances: [] });
      out.at(-1)!.rows.push({ e, i });
    });
    for (const d of out) {
      const last = new Map<string, Ev>();
      for (const { e } of d.rows) if (e.kind !== "fee") last.set(e.account_id ?? "", e);
      d.balances = [...last.values()].map((e) => ({ account: e.account, amount: e.balance_after ?? 0 }));
      d.budgeted = d.balances.length ? onBudget?.[d.date] : undefined;   // the budget line's, beside the balance
    }
    return out;
  });

  async function change(e: ForecastEvent, value: number) {
    try {
      // An edit is what the occurrence comes to in all: on "the rest" of one paid in parts, what's paid so far is added.
      const total = value + Math.abs(e.paid_so_far ?? 0);
      await api("/api/overrides", { method: "POST", body: { key: e.key, amount: (e.amount < 0 ? -1 : 1) * total } });
      toast.success("Updated for this date only");
      onchanged();
    } catch (err) { toast.error((err as Error).message); }
  }
  async function reset(e: ForecastEvent) {
    try { await api("/api/overrides", { method: "DELETE", body: { key: e.key } }); toast.success("Back to the usual amount"); onchanged(); }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

<!-- Rows of a grouped list (the caller puts them in a Group). -->
{#if !events.length}
  <p class="cell text-sm text-muted-foreground">Nothing scheduled. Add paychecks and bills on&nbsp;<a class="font-medium text-primary" href="#recurring">Recurring</a>.</p>
{:else}
  {#each days as d (d.date)}
  <!-- One block a day: the group's hairlines fall between days, not between a day's items. -->
  <div data-day={d.date}>
  <div class="px-4 pt-2.5 pb-1 text-[13px] font-medium text-muted-foreground" role="heading" aria-level="3">{fmtDow(d.date)}</div>
  {#each d.rows as { e, i } (e.key ?? `${e.date}-${e.name}-${i}`)}
    {@const bank = e.kind === "card" && e.card_id ? app.state?.brands?.[e.card_id] : undefined}
    <div class="cell min-h-12 py-2">
      <!-- Logos as they are, with nothing behind them, as in Transactions. -->
      {#if e.logo}
        <Logo src={e.logo} />
      {:else if bank?.src}
        <img class="size-8 shrink-0 rounded-lg object-contain" src={bank.src} alt="" title={bank.institution ?? ""} loading="lazy" width="32" height="32" />
      {:else}
        <CatIcon name={e.kind === "card" ? "Credit Card Payment" : e.category} size={32} />
      {/if}
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-1.5 text-[15px]">
          {#if e.recurring_id}
            <!-- A recurring item's name opens it in Recurring, to change every one. -->
            <a class="truncate hover:underline" href={`#recurring?item=${e.recurring_id}`} title="Open in Recurring">{e.name}</a>
          {:else}<span class="truncate">{e.name}</span>{/if}
          {#if e.paid_so_far}<Badge variant="secondary" title={`${fmt(Math.abs(e.paid_so_far))} has ${e.amount > 0 ? "come in" : "gone out"} already; this is the rest`}>rest</Badge>{/if}
          {#if e.late_from}<Badge variant="secondary" title={`Was due ${e.late_from} and ${e.paid_so_far ? "the rest " : ""}hasn't shown up yet`}>late</Badge>{/if}
          {#if e.overridden}<Badge variant="secondary" title={`Usually ${fmt(e.original_amount)}`}>edited</Badge>{/if}
        </div>
        {#if e.kind === "fee"}
          <!-- An annual fee is a charge on its card: it reaches cash in the card's statement payment, not on its own. -->
          <div class="truncate text-[13px] text-muted-foreground tabular-nums"
            title="Charged to the card: it's paid with the card's statement, so it's in that payment rather than taken out of your balance on its own">
            {#if !e.account}card not linked to an account, so not in the forecast
            {:else if e.paid_on}on {e.account}, paid with its {fmtDate(e.paid_on)} payment
            {:else}on {e.account}; its payment isn’t in the forecast{/if}
          </div>
        {:else if accounts && e.account}
          <div class="truncate text-[13px] text-muted-foreground">{e.account}</div>
        {/if}
      </div>
      <div class={["flex shrink-0 flex-col items-end text-[15px] tabular-nums", e.amount > 0 && "text-emerald-400"]}>
        <!-- An estimate is marked with an asterisk after its amount; what it's based on is in its tooltip. -->
        <!-- The asterisk hangs past the amount, so amounts line up on the right with or without one. -->
        <span class="relative flex items-baseline">{#if e.key}<AmountEdit amount={e.amount} signed label="Amount" title="Change this amount for this date only"
            save={(v) => change(e, v)} />{:else}{fmt(e.amount)}{/if}{#if e.estimated}<span class="absolute top-0 left-full ml-0.5 cursor-help text-muted-foreground" role="img" aria-label="estimate" title={`Estimate: ${e.kind !== "card" ? "based on recent payments" : e.from_budgets
              ? "the statement hasn't closed yet; your budgets paid with this card, plus its average spending outside them over its last 3 statements"
              : "the statement hasn't closed yet; based on the card's average over its last 3 statements"}`}>*</span>{/if}</span>
        {#if e.overridden}
          <Button variant="link" size="sm" class="h-auto p-0 text-xs" title="Go back to the usual amount" onclick={() => reset(e)}>reset</Button>
        {/if}
      </div>
    </div>
  {/each}
  {#each d.balances as b, k (k)}
    <div class="flex flex-wrap justify-end gap-x-1.5 px-4 pt-0.5 pb-3 text-[13px] text-muted-foreground tabular-nums">
      {#if accounts && b.account}<span class="truncate">{b.account} ·</span>{/if}
      <span class={b.amount < 0 ? "font-medium text-destructive" : ""}>projected balance {fmt(b.amount)}</span>
      <!-- With one account, its balance on budget beside it; with several, once for them all (below). -->
      {#if d.budgeted !== undefined && d.balances.length === 1}<span class={d.budgeted < 0 ? "font-medium text-destructive" : "text-chart-2"}
        title="If you stick to your budget">· on budget {fmt(d.budgeted)}</span>{/if}
    </div>
  {/each}
  {#if d.budgeted !== undefined && d.balances.length > 1}
    <div class={["-mt-2 px-4 pb-3 text-right text-[13px] tabular-nums", d.budgeted < 0 ? "font-medium text-destructive" : "text-chart-2"]}
      title="If you stick to your budget">on budget, all accounts {fmt(d.budgeted)}</div>
  {/if}
  </div>
  {/each}
  {#if events.length > shown.length}
    <button type="button" class="cell justify-between text-[15px] text-primary" onclick={() => (all = true)}>
      Show all {events.length}<ChevronRight class="size-4 text-muted-foreground" aria-hidden="true" />
    </button>
  {/if}
  {#if all && events.length > limit}
    <button type="button" class="cell justify-between text-[15px] text-primary" onclick={() => (all = false)}>
      Show fewer<ChevronUp class="size-4 text-muted-foreground" aria-hidden="true" />
    </button>
  {/if}
{/if}
