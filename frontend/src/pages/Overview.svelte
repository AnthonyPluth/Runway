<script lang="ts" module>
  // Survives redraws (a sync, an edited amount), like the classic app.
  let horizon: number | null = null;
</script>

<script lang="ts">
  import { api } from "$lib/api";
  import { app, reload } from "$lib/app.svelte";
  import MissedAlert from "$lib/components/MissedAlert.svelte";
  import CardsTable from "$lib/components/overview/CardsTable.svelte";
  import EventsList from "$lib/components/overview/EventsList.svelte";
  import ForecastChart from "$lib/components/overview/ForecastChart.svelte";
  import ForecastTable from "$lib/components/overview/ForecastTable.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Alert from "$lib/components/ui/alert";
  import * as Card from "$lib/components/ui/card";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { fmt, fmt0, fmtDate, fmtDow, nb, parseDate, plural, relDay } from "$lib/format";
  import type { Overview } from "$lib/types";
  import { cn } from "$lib/utils";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";

  let { sub: _sub = "" }: { sub?: string } = $props();
  const connected = $derived(app.state?.connected);
  const initial = horizon ?? app.state?.horizon_days ?? 90;
  let days = $state(initial);

  const load = (d: number) => api<Overview>(`/api/overview?days=${d}`);
  let data = $state<Promise<Overview>>(load(initial));
  function setDays(v: string) { days = horizon = Number(v); data = load(days); }

  const span = (d: number) => (d === 180 ? "6 months" : `${d} days`);
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

{#snippet warn(text: string, href?: string, linkText?: string)}
  <Alert.Root class="mb-3">
    <TriangleAlert />
    <Alert.Description>
      <p>{text} {#if href}<a class="font-medium text-foreground underline underline-offset-4" {href}>{linkText}</a>{/if}</p>
    </Alert.Description>
  </Alert.Root>
{/snippet}

{#if !connected}
  <Card.Root class="mx-auto mt-10 max-w-lg text-center">
    <Card.Header>
      <Card.Title>Connect your bank to get started</Card.Title>
      <Card.Description>Link SimpleFIN or Plaid and Runway projects where your cash is headed.</Card.Description>
    </Card.Header>
    <Card.Content><Button href="/#setup/connections">Go to Settings</Button></Card.Content>
  </Card.Root>
{:else}
  {#await data}
    <div class="h-40 animate-pulse rounded-xl bg-muted"></div>
  {:then fc}
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

    {#each fc.warnings as w (w)}{@render warn(w, `/#setup/${/Plaid/.test(w) ? "connections" : "accounts"}`, "Settings")}{/each}
    {#each fc.missed ?? [] as m (m.key)}<MissedAlert {m} />{/each}
    {#if !fc.accounts.length}{@render warn("No account to forecast yet. Choose your primary checking account in", "/#setup/accounts", "Settings.")}{/if}

    <section class="mb-8 flex flex-col justify-between gap-6 md:flex-row md:items-end md:gap-10">
      <div class="flex max-w-4xl flex-col gap-3">
        <span class="text-sm text-muted-foreground">
          {parseDate(fc.today).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })}
        </span>
        <h1 class="text-balance text-3xl font-semibold tracking-tight md:text-4xl">
          {#if low && fc.accounts.length}
            {#if lowBad}
              Heads up. {what} dips to <span class="font-bold text-destructive tabular-nums">{fmt0(low.balance)}</span>
              {nb(lowWhen(fc) === "today" ? "today" : "on " + lowWhen(fc))}.
            {:else}
              You’re on track. {what} stays above <span class="font-bold tabular-nums">{fmt0(low.balance)}</span> for the next {nb(span(days))}.
            {/if}
          {:else}Overview{/if}
        </h1>
        {#if low && fc.accounts.length}
          <p class="leading-relaxed text-muted-foreground">
            {#if low.date === fc.today}Today is the tightest point in the forecast.
            {:else}The tightest moment is {lowWhen(fc)}{#if lowEvents.length}, when {lowEvents[0].kind === "card" ? `the ${nb(lowEvents[0].name.replace(/ statement$/, ""))} payment` : lowEvents[0].name} goes out{/if}.{/if}
            {#if nextIn}Next money in: {nb(nextIn.name + ",")} {fmt0(nextIn.amount)} on {nb(relDay(nextIn.date, fc.today))}.{/if}
          </p>
        {/if}
      </div>
      <div class="flex shrink-0 flex-col gap-1 md:items-end">
        <span class="text-sm text-muted-foreground">{allChecking ? "In checking today" : "Cash today"}</span>
        <span class="text-4xl font-semibold tracking-tight tabular-nums">{fmt(cashNow)}</span>
        <span class="text-sm text-muted-foreground">{fc.accounts.map((a) => a.name).join(" + ") || "—"}</span>
      </div>
    </section>

    <div class="mb-6 grid gap-4 md:grid-cols-3">
      {#each [
        { label: lowBad ? "Goes negative" : "Lowest point", sub: low ? fmtDow(low.date) : "", value: low ? fmt(low.balance) : "—", alert: lowBad, low: true, tone: "" },
        { label: `In ${span(days)}`, sub: `${end - cashNow >= 0 ? "+" : "−"}${fmt(Math.abs(end - cashNow))}`, value: fmt(end), alert: false, low: false, tone: end - cashNow >= 0 ? "text-emerald-500" : "text-destructive" },
        { label: "Owed on cards", sub: `${plural(allCards.length, "card")}${nextDue ? ` · next due ${fmtDate(nextDue.due_date)}` : ""}`, value: fmt(owed), alert: false, low: false, tone: "" },
      ] as t (t.label)}
        <Card.Root class={cn("gap-2", t.alert && "border-destructive")}>
          <Card.Header>
            <Card.Description>{t.label}</Card.Description>
            <Card.Title class={cn("text-2xl tabular-nums", t.alert && "text-destructive")}>{t.value}</Card.Title>
          </Card.Header>
          <Card.Content class={cn("text-sm text-muted-foreground tabular-nums", t.tone)}>{t.sub}</Card.Content>
        </Card.Root>
      {/each}
    </div>

    <Card.Root class="mb-6">
      <Card.Header class="max-sm:has-data-[slot=card-action]:grid-cols-1">
        <Card.Title>The next {span(days)}{fc.accounts.length === 1 && !allChecking ? ` · ${fc.accounts[0].name}` : ""}</Card.Title>
        <Card.Description>Projected balance, day by day</Card.Description>
        <Card.Action class="max-sm:col-start-1 max-sm:row-span-1 max-sm:row-start-3 max-sm:justify-self-start"><Segmented label="Forecast length" value={String(days)} onchange={setDays}
          options={[...new Set([30, 60, 90, 180, days])].sort((a, b) => a - b).map((d) => ({ value: String(d), label: span(d) }))} /></Card.Action>
      </Card.Header>
      <Card.Content>
      {#if fc.budget}
        <div class="mb-2 flex flex-wrap gap-4 text-xs text-muted-foreground">
          <span class="flex items-center gap-1.5"><i class="inline-block h-0.5 w-4 bg-chart-1"></i>Forecast</span>
          <span class="flex items-center gap-1.5" title={`Spends your budgets (${fmt0(fc.budget.monthly)} a month) on each budget's account or card, in place of estimated card statements. A budget's recurring payments count toward it, so only the rest is spent on top of them.${budgetSkipped(fc.budget.skipped)}`}>
            <i class="inline-block h-0 w-4 border-t-2 border-dashed border-chart-2"></i>If you stick to your budget · low {fmt0(fc.budget.low.balance)} on {fmtDate(fc.budget.low.date)}
          </span>
        </div>
      {/if}
      <ForecastChart {fc} />
      {#if fc.accounts.length > 1}
        <p class="mt-3 text-sm text-muted-foreground">{fc.accounts.length} accounts combined · <a class="font-medium text-foreground underline underline-offset-4" href="/#setup/accounts">pick a primary account</a></p>
      {/if}
      <details class="mt-2">
        <summary class="cursor-pointer text-sm text-muted-foreground">Show as table</summary>
        <ForecastTable {fc} />
      </details>
      </Card.Content>
    </Card.Root>

    <div class="grid gap-6 lg:grid-cols-2">
      <Card.Root><Card.Header><Card.Title>Coming up</Card.Title></Card.Header><Card.Content><EventsList events={fc.events} /></Card.Content></Card.Root>
      <Card.Root><Card.Header><Card.Title>Credit cards</Card.Title></Card.Header><Card.Content><CardsTable cards={fc.cards} /></Card.Content></Card.Root>
    </div>
  {:catch err}
    <Card.Root>
      <Card.Content>
        <p class="text-sm">Something went wrong: {err.message}</p>
        <Button class="mt-3" variant="outline" onclick={reload}>Try again</Button>
      </Card.Content>
    </Card.Root>
  {/await}
{/if}
