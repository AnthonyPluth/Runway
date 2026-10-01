<script lang="ts" module>
  // Survives redraws (a sync, an edited amount), like the classic app.
  let horizon: number | null = null;
  let comingAll = $state(false);   // Coming up's "Show all"
  // The forecast last on screen, drawn at once when the page is drawn afresh (after a change) until the new one arrives.
  let last: { fc: Overview; days: number } | null = null;
</script>

<script lang="ts">
  import { api } from "$lib/api";
  import { app, reload } from "$lib/app.svelte";
  import MissedAlert from "$lib/components/MissedAlert.svelte";
  import NotConnected from "$lib/components/NotConnected.svelte";
  import CardsTable from "$lib/components/overview/CardsTable.svelte";
  import EventsList from "$lib/components/overview/EventsList.svelte";
  import ForecastChart from "$lib/components/overview/ForecastChart.svelte";
  import ForecastSettings from "$lib/components/overview/ForecastSettings.svelte";
  import { assumptions } from "$lib/components/overview/assumptions";
  import { openForecastSettings } from "$lib/components/overview/forecastSheet.svelte";
  import SetupChecklist from "$lib/components/overview/SetupChecklist.svelte";
  import ThisMonth from "$lib/components/overview/ThisMonth.svelte";
  import ForecastTable from "$lib/components/overview/ForecastTable.svelte";
  import StatStrip from "$lib/components/StatStrip.svelte";
  import { Button } from "$lib/components/ui/button";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { fmt, fmt0, fmt0Down, fmtDate, fmtDow, nb, parseDate, plural, relDay } from "$lib/format";
  import { balanceAsOf } from "$lib/nav.svelte";
  import { isPhone } from "$lib/phone.svelte";
  import type { Overview } from "$lib/types";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import Group from "$lib/components/ui/group/Group.svelte";

  let { sub: _sub = "" }: { sub?: string } = $props();
  const connected = $derived(app.state?.connected);
  const setup = $derived(app.state?.setup);
  // Setup (connecting a bank, choosing the main account, budgets) is done on a computer.
  const setupLeft = $derived(!isPhone() && !!setup && !setup.dismissed && !(setup.bank && setup.primary && setup.recurring && setup.budgets));
  const initial = horizon ?? app.state?.horizon_days ?? 90;
  let days = $state(initial);

  // Another length (or a change) keeps the forecast on screen until the new one arrives, and only the latest request
  // draws, if you click through lengths quickly.
  let shown = $state.raw(last);
  let error = $state<Error | null>(null);
  let seq = 0;
  function load(d: number) {
    const n = ++seq;
    api<Overview>(`/api/overview?days=${d}`).then(
      (fc) => { if (n === seq) { shown = last = { fc, days: d }; error = null; } },
      (e: Error) => { if (n !== seq) return; if (shown) toast.error(e.message); else error = e; });
  }
  load(initial);
  function setDays(v: string) { days = horizon = Number(v); load(days); }

  const span = (d: number) => (d === 180 ? "6 months" : `${d} days`);
  const short = (d: number) => (d % 30 === 0 ? `${d / 30}M` : `${d}D`);
  const lowWhen = (fc: Overview) => (fc.low.date === fc.today ? "today" : relDay(fc.low.date, fc.today));

  // "Left out: Mortgage and Utilities, which recurring items already cover; Medical, whose account isn't in the forecast."
  function budgetSkipped(skipped: { category: string; reason: string }[]): string {
    if (!skipped.length) return "";
    const list = (xs: string[]) => (xs.length < 3 ? xs.join(" and ") : xs.slice(0, -1).join(", ") + " and " + xs[xs.length - 1]);
    const by: Record<string, string[]> = {};
    for (const k of skipped) (by[k.reason] ||= []).push(k.category);
    return " Left out: " + Object.entries(by).map(([reason, cats]) => {
      const why = reason.startsWith("a recurring") ? (cats.length > 1 ? "which recurring items already cover" : "which a recurring item already covers")
        : reason.startsWith("its account") ? (cats.length > 1 ? "whose accounts aren't in the forecast" : "whose account isn't in the forecast") : reason;
      return `${list(cats)}, ${why}`;
    }).join("; ") + ".";
  }

</script>

{#snippet attention(text: string, href: string)}
  <a class="cell" {href}>
    <span class="flex size-8 shrink-0 items-center justify-center rounded-lg bg-amber-500 text-black" aria-hidden="true"><TriangleAlert class="size-4" /></span>
    <span class="min-w-0 flex-1 text-sm">{text}</span>
    <ChevronRight class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
  </a>
{/snippet}

{#if !connected}
  {#if isPhone()}
    <NotConnected title="Welcome to Runway" text="Runway reads your accounts and forecasts where your cash is headed. Setting it up takes a few minutes." />
  {:else}
    <SetupChecklist welcome />
  {/if}
{:else}
  {#if error && !shown}
    <div class="rounded-2xl bg-card p-5">
      <p class="text-sm">Something went wrong: {error.message}</p>
      <Button class="mt-3" variant="outline" onclick={reload}>Try again</Button>
    </div>
  {:else if !shown}
    <div class="space-y-4" aria-busy="true"><div class="h-24 w-72 animate-pulse rounded-2xl bg-card"></div><div class="h-64 animate-pulse rounded-2xl bg-card"></div></div>
  {:else}
    {@const fc = shown.fc}
    {@const cashNow = fc.accounts.reduce((s, a) => s + a.balance, 0)}
    {@const low = fc.low}
    {@const lowBad = !!low && low.balance < 0}
    {@const end = fc.total.length ? fc.total[fc.total.length - 1] : cashNow}
    {@const allCards = fc.cards.concat(fc.unlinked_cards ?? [])}
    {@const owed = allCards.reduce((s, c) => s + (c.owed_now || 0), 0)}
    {@const nextDue = fc.cards.filter((c) => c.remaining > 0 && c.due_date >= fc.today).sort((a, b) => a.due_date.localeCompare(b.due_date))[0]}
    {@const allChecking = fc.accounts.length > 0 && fc.accounts.every((a) => a.kind === "checking")}
    {@const what = fc.accounts.length === 1 ? (allChecking ? "Checking" : fc.accounts[0].name) : "Your cash"}
    {@const lowEvents = fc.events.filter((e) => e.date === low.date && e.amount < 0).sort((a, b) => a.amount - b.amount)}
    {@const nextIn = fc.events.find((e) => e.amount > 0 && e.date > low.date)}
    {@const asOf = balanceAsOf(fc.accounts.map((a) => a.balance_date), fc.today, app.state?.last_sync_ok)}
    {@const alerts = fc.warning_links.length + (fc.missed?.length ?? 0) + (fc.accounts.length ? 0 : 1)}
    {@const assumed = assumptions(fc)}

    <header class="mb-5">
      <div class="text-[13px] font-semibold tracking-wide text-muted-foreground uppercase">
        {parseDate(fc.today).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })}
      </div>
      <h1 class="text-[34px] leading-tight font-bold tracking-tight">Overview</h1>
    </header>

    {#if alerts}
      <Group title="Needs attention" inset="3.75rem" class="mb-6">
        {#each fc.warning_links as w (w.text)}{@render attention(w.text, `/${w.href}`)}{/each}
        {#each fc.missed ?? [] as m (m.key)}<MissedAlert {m} />{/each}
        {#if !fc.accounts.length}{@render attention("No account to forecast yet. Choose your main checking account.", "/#overview?forecast")}{/if}
      </Group>
    {/if}
    {#if setupLeft}<SetupChecklist />{/if}

    <!-- The hero: today's balance, whether it holds up, and the forecast under it. The chart is green while the
         balance stays above zero and red when it dips below. -->
    <section class="mb-6" style:--chart-1={lowBad ? "var(--destructive)" : "#30d158"} style:--chart-2="#64d2ff">
      <ForecastSettings label={fc.accounts.map((a) => a.name).join(" + ") || (allChecking ? "Checking" : "Cash")} accounts={fc.accounts}
        onhorizon={(d) => setDays(String(d))} onchange={() => load(days)} />
      <div class="text-[44px] leading-none font-bold tracking-tight tabular-nums md:text-[56px]">{fmt(cashNow)}</div>
      {#if asOf}<p class={cn("mt-1.5 text-[13px]", asOf.stale ? "text-amber-500" : "text-muted-foreground")}>{asOf.text}</p>{/if}
      {#if low && fc.accounts.length}
        <p class={cn("mt-2 flex items-baseline gap-1.5 text-[15px] font-semibold", lowBad ? "text-destructive" : "text-emerald-400")}>
          <span class="size-2 shrink-0 translate-y-[-1px] rounded-full bg-current" aria-hidden="true"></span>
          {#if lowBad}Heads up · {what} dips to {fmt0Down(low.balance)} {nb(lowWhen(fc) === "today" ? "today" : "on " + lowWhen(fc))}
          {:else}On track · {what} stays above {fmt0Down(low.balance)} for {nb(span(shown.days))}{/if}
        </p>
        <p class="mt-1 max-w-3xl text-[15px] leading-relaxed text-muted-foreground">
          {#if low.date === fc.today}Today is the tightest point in the forecast.
          {:else}The tightest moment is {lowWhen(fc)}{#if lowEvents.length}, when {lowEvents[0].kind === "card" ? `the ${nb(lowEvents[0].name.replace(/ statement$/, ""))} payment` : lowEvents[0].name} goes out{/if}.{/if}
          {#if nextIn}Next money in: {nb(nextIn.name + ",")} {fmt0(nextIn.amount)} on {nb(relDay(nextIn.date, fc.today))}.{/if}
        </p>
        <!-- What that verdict counts, and what it leaves out (everyday spending, unless it's turned on). -->
        <p class="mt-1 max-w-3xl text-[13px] text-muted-foreground">{assumed.text}{#if !isPhone()}{" · "}<button type="button" class="cursor-pointer font-medium text-primary" onclick={openForecastSettings}>{assumed.action}</button>{/if}</p>
      {/if}
      {#if fc.accounts.length > 1}
        <p class="mt-1 text-[13px] text-muted-foreground">{fc.accounts.length} accounts combined{#if !isPhone()}{" · "}<button type="button" class="cursor-pointer font-medium text-primary" onclick={openForecastSettings}>choose one account</button>{/if}</p>
      {/if}

      <div class="mt-5">
        {#if fc.budget}
          <div class="mb-1 flex flex-wrap gap-4 text-xs text-muted-foreground">
            <span class="flex items-center gap-1.5"><i class="inline-block h-0.5 w-4 bg-chart-1"></i>Forecast</span>
            <span class="flex items-center gap-1.5" title={`Spends your budgets (${fmt0(fc.budget.monthly)} a month) on each budget's account or card, in place of estimated card statements. A budget's recurring payments count toward it, so only the rest is spent on top of them.${budgetSkipped(fc.budget.skipped)}`}>
              <i class="inline-block h-0 w-4 border-t-2 border-dashed border-chart-2"></i>If you stick to your budget · low {fmt0Down(fc.budget.low.balance)} on {fmtDate(fc.budget.low.date)}
            </span>
          </div>
        {/if}
        <ForecastChart {fc} />
      </div>
      <Segmented label="Forecast length" value={String(days)} onchange={setDays} class="mt-3 flex w-full"
        options={[...new Set([30, 60, 90, 180, days])].sort((a, b) => a - b).map((d) => ({ value: String(d), label: short(d) }))} />
      <details class="group/table mt-3">
        <summary class="flex cursor-pointer list-none items-center gap-1 text-[15px] text-primary [&::-webkit-details-marker]:hidden">
          Day by day<ChevronRight class="size-4 transition-transform group-open/table:rotate-90" aria-hidden="true" />
        </summary>
        <div class="mt-2 rounded-2xl bg-card p-4"><ForecastTable {fc} /></div>
      </details>
    </section>

    <StatStrip class="mb-8" items={[
      { label: `${lowBad ? "Goes negative" : "Lowest"} · ${low ? fmtDow(low.date) : "—"}`, value: low ? fmt(low.balance) : "—", tone: lowBad ? "bad" : undefined },
      { label: `In ${span(shown.days)}`, value: fmt(end), sub: `${end - cashNow >= 0 ? "+" : "−"}${fmt(Math.abs(end - cashNow))}`,
        subTone: end - cashNow >= 0 ? "good" : "bad" },
      { label: "Owed on cards", value: fmt(owed), sub: `${plural(allCards.length, "card")}${nextDue ? ` · next due ${fmtDate(nextDue.due_date)}` : ""}` },
    ]} />

    <div class="grid items-start gap-6 lg:grid-cols-2">
      <div class="flex min-w-0 flex-col gap-6">
        <Group title="Coming up" inset="3.75rem"><EventsList events={fc.events} limit={6} bind:all={comingAll} /></Group>
        <Group title="Credit cards"><CardsTable cards={fc.cards} /></Group>
      </div>
      <ThisMonth />
    </div>
  {/if}
{/if}
