<script lang="ts" module>
  // Survives redraws (a sync, an edited amount), like the classic app.
  let horizon: number | null = null;
</script>

<script lang="ts">
  import { api } from "$lib/api";
  import { app, reload } from "$lib/app.svelte";
  import CardsTable from "$lib/components/overview/CardsTable.svelte";
  import EventsList from "$lib/components/overview/EventsList.svelte";
  import ForecastChart from "$lib/components/overview/ForecastChart.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { fmt, fmt0, fmtDate, fmtDow, nb, parseDate, plural, relDay } from "$lib/format";
  import type { Missed, Overview } from "$lib/types";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";

  let { sub: _sub = "" }: { sub?: string } = $props();
  const connected = $derived(app.state?.connected);
  const initial = horizon ?? app.state?.horizon_days ?? 90;
  let days = $state(initial);
  let dismissed = $state<string[]>([]);

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

  async function dismiss(m: Missed) {
    try { await api("/api/recurring/dismiss", { method: "POST", body: { key: m.key } }); dismissed.push(m.key); toast.success("Dismissed"); }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

{#snippet warn(text: string, href?: string, linkText?: string)}
  <div class="mb-2.5 flex items-start gap-2.5 rounded-xl bg-warning-wash px-3.5 py-2.5 text-sm ring-1 ring-warning/30">
    <span class="flex size-5 shrink-0 items-center justify-center rounded-full bg-warning text-xs font-extrabold text-[#1a1406]">!</span>
    <span>{text} {#if href}<a class="text-primary-ink" {href}>{linkText}</a>{/if}</span>
  </div>
{/snippet}

{#if !connected}
  <Card.Root class="mx-auto mt-10 max-w-lg text-center">
    <h2 class="text-lg font-semibold text-foreground-strong">Connect your bank to get started</h2>
    <p class="mt-2 text-sm text-muted-foreground">Link SimpleFIN or Plaid and Runway projects where your cash is headed.</p>
    <Button class="mt-5" href="/#setup/connections">Go to Settings</Button>
  </Card.Root>
{:else}
  {#await data}
    <div class="h-40 animate-pulse rounded-xl bg-card"></div>
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
    {#each (fc.missed ?? []).filter((m) => !dismissed.includes(m.key)) as m (m.key)}
      <div class="mb-2.5 flex items-start gap-2.5 rounded-xl bg-warning-wash px-3.5 py-2.5 text-sm ring-1 ring-warning/30">
        <span class="flex size-5 shrink-0 items-center justify-center rounded-full bg-warning text-xs font-extrabold text-[#1a1406]">!</span>
        <span>
          <b>{m.name}</b>: {fmt(Math.abs(m.amount))} {m.amount > 0 ? "expected in" : "expected"} {fmtDate(m.date)} hasn't shown up in {m.account_name || "the account"}.
          <a class="text-primary-ink" href="/#transactions">Find it</a> ·
          <Button variant="link" size="xs" class="p-0" onclick={() => dismiss(m)}>Dismiss</Button>
        </span>
      </div>
    {/each}
    {#if !fc.accounts.length}{@render warn("No account to forecast yet. Choose your primary checking account in", "/#setup/accounts", "Settings.")}{/if}

    <section class="mb-7 mt-1 flex flex-col justify-between gap-6 md:flex-row md:items-end md:gap-10">
      <div class="flex max-w-[900px] flex-col gap-3">
        <span class="text-[12.5px] uppercase tracking-[0.14em] text-muted-foreground">
          {parseDate(fc.today).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })}
        </span>
        <h1 class="text-balance text-[28px] font-semibold leading-tight tracking-[-0.025em] text-foreground-strong md:text-4xl">
          {#if low && fc.accounts.length}
            {#if lowBad}
              Heads up. {what} dips to <span class="font-bold text-destructive tabular">{fmt0(low.balance)}</span>
              {nb(lowWhen(fc) === "today" ? "today" : "on " + lowWhen(fc))}.
            {:else}
              You’re on track. {what} stays above <span class="font-bold text-primary tabular">{fmt0(low.balance)}</span> for the next {nb(span(days))}.
            {/if}
          {:else}Overview{/if}
        </h1>
        {#if low && fc.accounts.length}
          <p class="text-[15px] leading-relaxed text-subtle-foreground">
            {#if low.date === fc.today}Today is the tightest point in the forecast.
            {:else}The tightest moment is {lowWhen(fc)}{#if lowEvents.length}, when {lowEvents[0].kind === "card" ? `the ${nb(lowEvents[0].name.replace(/ statement$/, ""))} payment` : lowEvents[0].name} goes out{/if}.{/if}
            {#if nextIn}Next money in: {nb(nextIn.name + ",")} {fmt0(nextIn.amount)} on {nb(relDay(nextIn.date, fc.today))}.{/if}
          </p>
        {/if}
      </div>
      <div class="flex shrink-0 flex-col gap-1 md:items-end">
        <span class="text-[13px] text-muted-foreground">{allChecking ? "In checking today" : "Cash today"}</span>
        <span class="text-[40px] font-semibold leading-none tracking-[-0.025em] text-foreground-strong tabular">{fmt(cashNow)}</span>
        <span class="text-[13px] text-muted-foreground">{fc.accounts.map((a) => a.name).join(" + ") || "—"}</span>
      </div>
    </section>

    <div class="mb-5 grid gap-4 md:grid-cols-3">
      {#each [
        { label: lowBad ? "Goes negative" : "Lowest point", sub: low ? fmtDow(low.date) : "", value: low ? fmt(low.balance) : "—", alert: lowBad, low: true, tone: "" },
        { label: `In ${span(days)}`, sub: `${end - cashNow >= 0 ? "+" : "−"}${fmt(Math.abs(end - cashNow))}`, value: fmt(end), alert: false, low: false, tone: end - cashNow >= 0 ? "text-good" : "text-destructive" },
        { label: "Owed on cards", sub: `${plural(allCards.length, "card")}${nextDue ? ` · next due ${fmtDate(nextDue.due_date)}` : ""}`, value: fmt(owed), alert: false, low: false, tone: "" },
      ] as t (t.label)}
        <div class={cn("flex items-center justify-between gap-4 rounded-[14px] bg-card px-5 py-4 ring-1 ring-border", t.alert && "bg-destructive-wash ring-destructive")}>
          <div><div class="text-[13px] text-muted-foreground">{t.label}</div><div class={cn("mt-1 text-[13px] text-muted-foreground tabular", t.tone)}>{t.sub}</div></div>
          <div class={cn("text-2xl font-semibold tracking-[-0.02em] text-foreground-strong tabular", t.low && (t.alert ? "text-destructive" : "text-low"))}>{t.value}</div>
        </div>
      {/each}
    </div>

    <Card.Root class="mb-5">
      <Card.Header>
        <Card.Title>The next {span(days)}{fc.accounts.length === 1 && !allChecking ? ` · ${fc.accounts[0].name}` : ""}</Card.Title>
        <Segmented label="Forecast length" value={String(days)} onchange={setDays}
          options={[...new Set([30, 60, 90, 180, days])].sort((a, b) => a - b).map((d) => ({ value: String(d), label: span(d) }))} />
      </Card.Header>
      {#if fc.budget}
        <div class="mb-2 flex flex-wrap gap-4 text-xs text-muted-foreground">
          <span class="flex items-center gap-1.5"><i class="inline-block h-0.5 w-4 bg-primary"></i>Forecast</span>
          <span class="flex items-center gap-1.5" title={`Spends your budgets (${fmt0(fc.budget.monthly)} a month) on each budget's card, in place of estimated card statements.${budgetSkipped(fc.budget.skipped)}`}>
            <i class="inline-block h-0 w-4 border-t-2 border-dashed border-good"></i>If you stick to your budget · low {fmt0(fc.budget.low.balance)} on {fmtDate(fc.budget.low.date)}
          </span>
        </div>
      {/if}
      <ForecastChart {fc} />
      {#if fc.accounts.length > 1}
        <p class="mt-3 text-[13px] text-muted-foreground">{fc.accounts.length} accounts combined · <a class="text-primary-ink" href="/#setup/accounts">pick a primary account</a></p>
      {/if}
      <details class="mt-2">
        <summary class="cursor-pointer text-[13px] text-muted-foreground">Show as table</summary>
        <table class="mt-2 w-full max-w-[360px] text-sm">
          <thead><tr class="text-left text-xs text-muted-foreground"><th class="pb-1 font-medium">Date</th><th class="pb-1 text-right font-medium">Projected balance</th></tr></thead>
          <tbody>
            {#each fc.dates.filter((_, i) => i % 7 === 0) as d, k (d)}
              <tr class="border-t border-line"><td class="py-1.5">{fmtDow(d)}</td><td class="py-1.5 text-right tabular">{fmt(fc.total[k * 7])}</td></tr>
            {/each}
          </tbody>
        </table>
      </details>
    </Card.Root>

    <div class="grid gap-5 lg:grid-cols-2">
      <Card.Root><Card.Header><Card.Title>Coming up</Card.Title></Card.Header><EventsList events={fc.events} /></Card.Root>
      <Card.Root><Card.Header><Card.Title>Credit cards</Card.Title></Card.Header><CardsTable cards={fc.cards} /></Card.Root>
    </div>
  {:catch err}
    <Card.Root>
      <p class="text-sm">Something went wrong: {err.message}</p>
      <Button class="mt-3" variant="secondary" onclick={reload}>Try again</Button>
    </Card.Root>
  {/await}
{/if}
