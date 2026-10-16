<script lang="ts">
  import { app } from "$lib/app.svelte";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { fmt, fmt0, fmt0Down, nb, relDay } from "$lib/format";
  import { balanceAsOf } from "$lib/nav.svelte";
  import type { Overview } from "$lib/types";
  import { cn } from "$lib/utils";
  import Settings2 from "@lucide/svelte/icons/settings-2";
  import ForecastChart from "./ForecastChart.svelte";
  import ForecastSettings from "./ForecastSettings.svelte";

  // The Overview's first card: which cash the forecast shows (the account switcher is its label), whether it holds up
  // (the chip), today's balance, the forecast chart and the two facts that matter, the next money in and the lowest
  // point. What the forecast counts stays here too, behind "Forecast details". Display only: the figures are the
  // forecast's own. The chart is green while the balance stays above zero and red when it dips below.
  let { fc, days, shownDays, setDays, onchange }: {
    fc: Overview; days: number; shownDays: number; setDays: (v: string) => void; onchange: () => void;
  } = $props();

  const span = (d: number) => (d === 180 ? "6 months" : `${d} days`);
  const short = (d: number) => (d % 30 === 0 ? `${d / 30}M` : `${d}D`);
  const lowWhen = (f: Overview) => (f.low.date === f.today ? "today" : relDay(f.low.date, f.today));

  // "Left out: Mortgage and Utilities, which recurring items already cover; Medical, whose account isn't in the forecast."
  function budgetSkipped(skipped: { category: string; reason: string }[]): string {
    if (!skipped.length) return "";
    const list = (xs: string[]) => (xs.length < 3 ? xs.join(" and ") : xs.slice(0, -1).join(", ") + " and " + xs[xs.length - 1]);
    const by: Record<string, string[]> = {};
    for (const k of skipped) (by[k.reason] ||= []).push(k.category);
    return " Left out: " + Object.entries(by).map(([reason, cats]) => {
      const why = reason.startsWith("a recurring") ? (cats.length > 1 ? "which recurring items already cover" : "which a recurring item already covers")
        : reason.startsWith("its account") ? (cats.length > 1 ? "whose accounts aren't in the forecast" : "whose account isn't in the forecast")
        : reason.startsWith("its card isn't") ? (cats.length > 1 ? "whose cards aren't paid from a forecast account" : "whose card isn't paid from a forecast account")
        : reason.startsWith("its card's statement") ? (cats.length > 1 ? "whose cards' statements are out of date" : "whose card's statement is out of date") : reason;
      return `${list(cats)}, ${why}`;
    }).join("; ") + ".";
  }

  // "Balance as of today, 7:02 AM, including −$375.00 pending": the balance is the bank's plus what's pending.
  function balanceNote(asOf: string | undefined, pending: number): string {
    if (Math.abs(pending) < 0.005) return asOf ?? "";
    const p = `${pending < 0 ? "−" : ""}${fmt(Math.abs(pending))} pending`;
    return asOf ? `${asOf}, including ${p}` : `Including ${p}`;
  }

  const cashNow = $derived(fc.accounts.reduce((s, a) => s + a.balance, 0));
  const low = $derived(fc.low);
  const lowBad = $derived(!!low && low.balance < 0);
  const allChecking = $derived(fc.accounts.length > 0 && fc.accounts.every((a) => a.kind === "checking"));
  const what = $derived(fc.accounts.length === 1 ? (allChecking ? "Checking" : fc.accounts[0].name) : "Your cash");
  const lowEvents = $derived(fc.events.filter((e) => e.date === low.date && e.amount < 0).sort((a, b) => a.amount - b.amount));
  const nextIn = $derived(fc.events.find((e) => e.amount > 0 && e.date > fc.today));
  const asOf = $derived(balanceAsOf(fc.accounts.map((a) => a.balance_date), fc.today, app.state?.last_sync_ok));
  const note = $derived(balanceNote(asOf?.text, fc.accounts.reduce((s, a) => s + (a.pending ?? 0), 0)));
  // The balance large, its cents muted.
  const money = $derived.by(() => { const s = fmt(cashNow), i = s.lastIndexOf("."); return i < 0 ? [s, ""] : [s.slice(0, i), s.slice(i)]; });
  const lowName = $derived(lowEvents.length ? (lowEvents[0].kind === "card" ? `${lowEvents[0].name.replace(/ statement$/, "")} payment` : lowEvents[0].name) : "");
</script>

<section class="rounded-2xl border border-edge bg-card p-4 md:p-5" style:--chart-1={lowBad ? "var(--destructive)" : "var(--good)"} data-testid="balance-card">
  <div class="flex items-center justify-between gap-3">
    <ForecastSettings label={fc.accounts.map((a) => a.name).join(" + ") || (allChecking ? "Checking" : "Cash")}
      onhorizon={(d) => setDays(String(d))} {onchange} />
    {#if low && fc.accounts.length}
      <span class={cn("shrink-0 rounded-full px-2.5 py-0.5 text-[12px] font-semibold", lowBad ? "bg-destructive/15 text-destructive" : "bg-good/15 text-good")}>{lowBad ? "Heads up" : "On track"}</span>
    {/if}
  </div>

  <div class="mt-2 text-[40px] leading-none font-extrabold tracking-[-0.04em] tabular-nums md:text-[48px]">
    <span class="sr-only">{fmt(cashNow)}</span>
    <span aria-hidden="true">{money[0]}<span class="text-[0.55em] font-semibold tracking-normal text-muted-foreground">{money[1]}</span></span>
  </div>
  {#if note}<p class={cn("mt-1.5 text-[13px]", asOf?.stale ? "text-warning" : "text-muted-foreground")}>{note}</p>{/if}
  {#if low && fc.accounts.length}
    <p class={cn("mt-2 text-[15px] leading-snug font-medium", lowBad ? "text-destructive" : "text-foreground")}>
      {#if lowBad}{what} dips to {fmt0Down(low.balance)} {nb(lowWhen(fc) === "today" ? "today" : "on " + lowWhen(fc))}.
      {:else}{what} stays above {fmt0Down(low.balance)} for {nb(span(shownDays))}.{/if}
    </p>
  {/if}
  {#if fc.accounts.length > 1}
    <p class="mt-1 text-[13px] text-muted-foreground">{fc.accounts.length} accounts combined</p>
  {/if}

  <div class="mt-3"><ForecastChart {fc} compact /></div>
  <Segmented label="Forecast length" value={String(days)} onchange={setDays} class="mt-2 flex w-full"
    options={[...new Set([30, 60, 90, 180, days])].sort((a, b) => a - b).map((d) => ({ value: String(d), label: short(d) }))} />

  {#if low && fc.accounts.length}
    <dl class="mt-4 grid grid-cols-2 gap-4 border-t border-border pt-3">
      <div class="min-w-0">
        <dt class="text-[12px] text-muted-foreground">Next money in</dt>
        {#if nextIn}
          <dd class="text-[17px] font-semibold tabular-nums">{fmt0(nextIn.amount)}</dd>
          <dd class="truncate text-[13px] text-muted-foreground">{nextIn.name} · {relDay(nextIn.date, fc.today)}</dd>
        {:else}<dd class="text-[15px] text-muted-foreground">Nothing scheduled</dd>{/if}
      </div>
      <div class="min-w-0">
        <dt class="text-[12px] text-muted-foreground">Lowest point</dt>
        <dd class={cn("text-[17px] font-semibold tabular-nums", lowBad && "text-destructive")}>{fmt0Down(low.balance)}</dd>
        <dd class="truncate text-[13px] text-muted-foreground">{lowWhen(fc)}{#if lowName}{" · "}{lowName}{/if}</dd>
      </div>
    </dl>
  {/if}

  <!-- What the line spends besides scheduled items: your budgets, and nothing else. -->
  <details class="group mt-3 border-t border-border pt-2">
    <summary class="flex cursor-pointer list-none items-center gap-1.5 text-[13px] text-muted-foreground hover:text-foreground">
      <Settings2 class="size-4" aria-hidden="true" />Forecast details
    </summary>
    <p class="mt-2 text-xs text-muted-foreground" title={fc.budget ? budgetSkipped(fc.budget.skipped).trim() || undefined : undefined}>
      {#if fc.budget?.used.length}Recurring bills and income, plus {fmt0(fc.budget.monthly)} a month of budgeted spending.
      {:else}Recurring bills and income only: <a class="font-medium text-foreground underline underline-offset-4" href="#budget">set budgets</a> to include everyday spending.{/if}
    </p>
  </details>
</section>
