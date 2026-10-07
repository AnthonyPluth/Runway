<script lang="ts" module>
  // Survives redraws (a sync, an edited amount), like the classic app.
  let horizon: number | null = null;
  let comingAll = $state(false);   // Coming up's "Show all"
  // Alerts put away on Needs attention, by their message: one stays hidden while its message reads the same, so a
  // warning worded differently (or one you ask to see again) comes back. Saved with the other settings.
  let dismissed = $state<string[]>([]);
  let showDismissed = $state(false);
  // The forecast last on screen, drawn at once when the page is drawn afresh (after a change) until the new one arrives.
  let last: { fc: Overview; days: number } | null = null;
</script>

<script lang="ts">
  import { act } from "$lib/act";
  import { api } from "$lib/api";
  import { app, reload } from "$lib/app.svelte";
  import MissedAlert from "$lib/components/MissedAlert.svelte";
  import CardsTable from "$lib/components/overview/CardsTable.svelte";
  import EventsList from "$lib/components/overview/EventsList.svelte";
  import ForecastChart from "$lib/components/overview/ForecastChart.svelte";
  import ForecastSettings from "$lib/components/overview/ForecastSettings.svelte";
  import { comingUp } from "$lib/components/overview/comingUp";
  import SetupChecklist from "$lib/components/overview/SetupChecklist.svelte";
  import ThisMonth from "$lib/components/overview/ThisMonth.svelte";
  import { Button } from "$lib/components/ui/button";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { fmt, fmt0, fmt0Down, nb, parseDate, relDay } from "$lib/format";
  import { balanceAsOf } from "$lib/nav.svelte";
  import type { Overview } from "$lib/types";
  import { undoable } from "$lib/undo";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import Group from "$lib/components/ui/group/Group.svelte";

  let { sub: _sub = "" }: { sub?: string } = $props();
  const connected = $derived(app.state?.connected);
  const setup = $derived(app.state?.setup);
  const setupLeft = $derived(!!setup && !setup.dismissed && !(setup.bank && setup.primary && setup.recurring && setup.budgets));
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
      (fc) => { if (n === seq) { shown = last = { fc, days: d }; dismissed = fc.dismissed_warnings ?? []; showDismissed = false; error = null; } },
      (e: Error) => { if (n !== seq) return; if (shown) toast.error(e.message); else error = e; });
  }
  load(initial);
  function setDays(v: string) { days = horizon = Number(v); load(days); }

  // An alert is put away by its message, so one worded differently later (the same link, a new problem) shows again.
  const remember = (list: string[]) => api("/api/settings", { method: "POST", body: { overview_warnings_dismissed: list } });
  async function dismiss(message: string) {
    const next = [...dismissed, message];
    if (!(await act(async () => { await remember(next); dismissed = next; }))) return;   // the alert stays up if it didn't save
    undoable("Alert dismissed", () => restore(message));
  }
  async function restore(message: string) {
    const next = dismissed.filter((m) => m !== message);
    await remember(next);   // a failure throws, so Undo's toast says what went wrong
    dismissed = next;
  }

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

</script>

<!-- An alert links to where it's put right. -->
{#snippet attention(text: string, href: string)}
    <a class="cell" {href}>
      <span class="flex size-8 shrink-0 items-center justify-center rounded-lg bg-warning text-black" aria-hidden="true"><TriangleAlert class="size-4" /></span>
      <span class="min-w-0 flex-1 text-sm">{text}</span>
      <ChevronRight class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
    </a>
{/snippet}

<!-- One that can be put away, by its message. `held` is one you've put away: shown again so it can be brought back. -->
{#snippet warn(text: string, href: string, held: boolean)}
    <div class="cell hover:bg-white/4" class:opacity-60={held}>
      <a href={`/${href}`} class="flex min-w-0 flex-1 items-center gap-3">
        <span class="flex size-8 shrink-0 items-center justify-center rounded-lg bg-warning text-black" aria-hidden="true"><TriangleAlert class="size-4" /></span>
        <span class="min-w-0 flex-1 text-sm">{text}</span>
        <ChevronRight class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      </a>
      <Button variant="ghost" size="sm" class="shrink-0 text-muted-foreground" aria-label={`${held ? "Show again" : "Dismiss"} ${text}`}
        onclick={() => (held ? act(() => restore(text)) : dismiss(text))}>{held ? "Show again" : "Dismiss"}</Button>
    </div>
{/snippet}

{#if !connected}
  <SetupChecklist welcome />
{:else}
  {#if error && !shown}
    <div class="rounded-2xl bg-card p-5">
      <p class="text-sm">Something went wrong: {error.message}</p>
      <Button class="mt-3" variant="outline" onclick={reload}>Try again</Button>
    </div>
  {:else if !shown}
    <div class="space-y-4" aria-busy="true"><div class="h-24 w-72 animate-pulse motion-reduce:animate-none rounded-2xl bg-card"></div><div class="h-64 animate-pulse motion-reduce:animate-none rounded-2xl bg-card"></div></div>
  {:else}
    {@const fc = shown.fc}
    {@const cashNow = fc.accounts.reduce((s, a) => s + a.balance, 0)}
    {@const low = fc.low}
    {@const lowBad = !!low && low.balance < 0}
    {@const allChecking = fc.accounts.length > 0 && fc.accounts.every((a) => a.kind === "checking")}
    {@const what = fc.accounts.length === 1 ? (allChecking ? "Checking" : fc.accounts[0].name) : "Your cash"}
    {@const lowEvents = fc.events.filter((e) => e.date === low.date && e.amount < 0).sort((a, b) => a.amount - b.amount)}
    {@const nextIn = fc.events.find((e) => e.amount > 0 && e.date > low.date)}
    {@const asOf = balanceAsOf(fc.accounts.map((a) => a.balance_date), fc.today, app.state?.last_sync_ok)}
    {@const note = balanceNote(asOf?.text, fc.accounts.reduce((s, a) => s + (a.pending ?? 0), 0))}
    {@const gone = new Set(dismissed)}
    {@const open = fc.warning_links.filter((w) => !gone.has(w.text))}
    {@const held = fc.warning_links.filter((w) => gone.has(w.text))}
    {@const alerts = open.length + (fc.missed?.length ?? 0) + (fc.accounts.length ? 0 : 1)}
    {@const rows = showDismissed ? [...open, ...held] : open}

    <header class="mb-5">
      <div class="text-[13px] font-semibold tracking-[0.14em] text-muted-foreground uppercase">
        {parseDate(fc.today).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })}
      </div>
      <div class="flex items-center gap-1">
        <h1 class="text-[34px] leading-[1.05] font-extrabold tracking-[-0.035em]">Overview</h1>
      </div>
    </header>

    {#if alerts || held.length}
      <Group title="Needs attention" inset="3.75rem" class="mb-6">
        {#snippet action()}
          {#if held.length && (alerts || showDismissed)}
            <button type="button" class="text-[13px] text-primary" onclick={() => (showDismissed = !showDismissed)}>
              {showDismissed ? "Hide dismissed" : "Show dismissed"}
            </button>
          {/if}
        {/snippet}
        {#each rows as w (w.text)}{@render warn(w.text, w.href, gone.has(w.text))}{/each}
        {#each fc.missed ?? [] as m (m.key)}<MissedAlert {m} today={fc.today} />{/each}
        {#if !fc.accounts.length}{@render attention("No account to forecast yet. Choose your forecast account.", "/#overview?forecast")}{/if}
        {#if held.length && !alerts && !showDismissed}
          <!-- Every alert here is put away: the way back, so the group isn't left empty. -->
          <button type="button" class="cell" onclick={() => (showDismissed = true)}>Show dismissed</button>
        {/if}
      </Group>
    {/if}
    {#if setupLeft}<SetupChecklist />{/if}

    <!-- The hero: today's balance, whether it holds up, and the forecast under it. The chart is green while the
         balance stays above zero and red when it dips below. -->
    <section class="mb-6" style:--chart-1={lowBad ? "var(--destructive)" : "var(--good)"}>
      <ForecastSettings label={fc.accounts.map((a) => a.name).join(" + ") || (allChecking ? "Checking" : "Cash")}
        onhorizon={(d) => setDays(String(d))} onchange={() => load(days)} />
      <div class="text-[44px] leading-none font-extrabold tracking-[-0.04em] tabular-nums md:text-[56px]">{fmt(cashNow)}</div>
      {#if note}<p class={cn("mt-1.5 text-[13px]", asOf?.stale ? "text-warning" : "text-muted-foreground")}>{note}</p>{/if}
      {#if low && fc.accounts.length}
        <p class={cn("mt-2 flex items-baseline gap-1.5 text-[15px] font-semibold", lowBad ? "text-destructive" : "text-good")}>
          <span class="size-2 shrink-0 translate-y-[-1px] rounded-full bg-current" aria-hidden="true"></span>
          {#if lowBad}Heads up · {what} dips to {fmt0Down(low.balance)} {nb(lowWhen(fc) === "today" ? "today" : "on " + lowWhen(fc))}
          {:else}On track · {what} stays above {fmt0Down(low.balance)} for {nb(span(shown.days))}{/if}
        </p>
        {#if low.date !== fc.today || nextIn}
        <p class="mt-1 max-w-3xl text-[15px] leading-relaxed text-muted-foreground">
          {#if low.date !== fc.today}The tightest moment is {lowWhen(fc)}{#if lowEvents.length}, when {lowEvents[0].kind === "card" ? `the ${nb(lowEvents[0].name.replace(/ statement$/, ""))} payment` : lowEvents[0].name} goes out{/if}.{/if}
          {#if nextIn}Next money in: {nb(nextIn.name + ",")} {fmt0(nextIn.amount)} on {nb(relDay(nextIn.date, fc.today))}.{/if}
        </p>
        {/if}
      {/if}
      {#if fc.accounts.length > 1}
        <p class="mt-1 text-[13px] text-muted-foreground">{fc.accounts.length} accounts combined</p>
      {/if}

      <div class="mt-5">
        <ForecastChart {fc} />
        <!-- What the line spends besides scheduled items: your budgets, and nothing else. -->
        <p class="mt-1 text-xs text-muted-foreground" title={fc.budget ? budgetSkipped(fc.budget.skipped).trim() || undefined : undefined}>
          {#if fc.budget?.used.length}Recurring bills and income, plus {fmt0(fc.budget.monthly)} a month of budgeted spending.
          {:else}Recurring bills and income only: <a class="font-medium text-foreground underline underline-offset-4" href="#budget">set budgets</a> to include everyday spending.{/if}
        </p>
      </div>
      <Segmented label="Forecast length" value={String(days)} onchange={setDays} class="mt-3 flex w-full"
        options={[...new Set([30, 60, 90, 180, days])].sort((a, b) => a - b).map((d) => ({ value: String(d), label: short(d) }))} />
    </section>

    <div class="grid items-start gap-6 lg:grid-cols-2">
      <div class="flex min-w-0 flex-col gap-6">
        <Group title="Coming up" inset="3.75rem"><EventsList events={comingUp(fc)} limit={6} bind:all={comingAll}
          onchanged={() => load(days)} /></Group>
        <Group title="Credit cards"><CardsTable cards={fc.cards} onchanged={() => load(days)} /></Group>
      </div>
      <ThisMonth />
    </div>
  {/if}
{/if}
