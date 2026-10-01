<script lang="ts">
  import { api } from "$lib/api";
  import ConfirmButton from "$lib/components/ConfirmButton.svelte";
  import DesktopOnly from "$lib/components/DesktopOnly.svelte";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { fmt0 } from "$lib/format";
  import { isPhone } from "$lib/phone.svelte";
  import Plus from "@lucide/svelte/icons/plus";
  import X from "@lucide/svelte/icons/x";
  import { onDestroy } from "svelte";
  import PlannerChart from "./PlannerChart.svelte";
  import { loanProjected, project, sale as saleAt, saleProceeds } from "./planner";
  import type { PlanAsset, PlanData, RetirementPlan } from "./types";

  // The retirement planner: your household's plan from now to the end, projected a thousand ways (planner.ts).
  // Everything is in today's dollars. Changes are kept a moment after you stop typing (or when you leave). On a phone
  // it's the result only; the plan itself is edited on a computer.
  let { data }: { data: PlanData } = $props();

  const copy = (p: RetirementPlan): RetirementPlan => JSON.parse(JSON.stringify(p));
  const initialPlan = () => copy(data.plan);
  let plan = $state<RetirementPlan>(initialPlan());
  const wasDefault = () => data.is_default;
  let isDefault = $state(wasDefault());
  let problem = $state<string | null>(null);
  const year = $derived(data.year);

  // Keep the plan (a moment after the last change, or as you leave, so a quick tab switch doesn't drop it). A plan the
  // server refuses stays on screen with the reason; one it takes shows "Saved ✓" for a moment.
  let timer: ReturnType<typeof setTimeout>, savedTimer: ReturnType<typeof setTimeout>;
  let dirty = false, saved = $state(false);
  async function save() {
    clearTimeout(timer); dirty = false;
    try {
      await api("/api/investments/plan", { method: "POST", body: { plan: $state.snapshot(plan) } });
      problem = null; isDefault = false; saved = true;
      clearTimeout(savedTimer); savedTimer = setTimeout(() => (saved = false), 1600);
    } catch (err) { problem = (err as Error).message; }
  }
  function keep() {
    dirty = true; clearTimeout(timer);
    timer = setTimeout(save, 700);
  }
  onDestroy(() => { clearTimeout(savedTimer); if (dirty) save(); });
  async function startOver() {
    clearTimeout(timer); dirty = false;
    try { await api("/api/investments/plan", { method: "POST", body: { plan: null } }); }
    catch (err) { problem = (err as Error).message; return; }
    plan = copy({ ...data.plan, ...defaults() });
    isDefault = true; problem = null;
  }
  // Runway's own starting figures (what planner.default() gives).
  const defaults = (): Partial<RetirementPlan> => ({
    people: [{ name: "You", birth_year: year - 40, retire_age: 65, savings: data.computed.yearly_savings }],
    plan_to_age: 95, spending: data.computed.annual_spending, return_before: data.computed.expected_return,
    return_after: 0.04, volatility: 0.12, inflation: 0.025, income: [], events: [], assets: [],
  });

  // Numbers as typed: an empty or half-typed box counts as nothing rather than breaking the projection.
  const num = (v: unknown) => (typeof v === "number" && isFinite(v) ? v : 0);
  const pctIn = (v: number) => String(+(v * 100).toFixed(2));
  const setPct = (k: "return_before" | "return_after" | "volatility" | "inflation", s: string) => { plan[k] = (Number(s) || 0) / 100; keep(); };

  const usable = $derived(plan.people.every((p) => num(p.birth_year) > 1900 && num(p.retire_age) > 0) && num(plan.plan_to_age) > 0);
  const proj = $derived.by(() => {
    if (!usable) return null;
    const p = $state.snapshot(plan) as RetirementPlan;
    p.people = p.people.map((x) => ({ ...x, savings: num(x.savings) }));
    p.spending = num(p.spending);
    p.income = p.income.filter((i) => i.person < p.people.length).map((i) => ({ ...i, amount: num(i.amount), start_age: num(i.start_age),
      end_age: typeof i.end_age === "number" && isFinite(i.end_age) ? i.end_age : null }));
    p.events = p.events.map((e) => ({ ...e, amount: num(e.amount), year: num(e.year) }));
    return project(p, data.current, year, data.assets);
  });
  const names = $derived(plan.people.map((p, i) => p.name || (i ? "Partner" : "You")));
  // Nothing invested and no plan of your own yet: nothing to project from. With investments but still Runway's guesses,
  // the results are a sample, so they're shown muted rather than as a verdict.
  const empty = $derived(data.current <= 0 && isDefault);
  const sample = $derived(isDefault && data.current > 0);
  const successCls = (s: number) => (sample ? "text-muted-foreground" : s >= 0.85 ? "text-[var(--good)]" : s >= 0.7 ? "text-[var(--warning)]" : "text-[var(--low)]");

  function addPartner() {
    plan.people.push({ name: "Partner", birth_year: plan.people[0].birth_year, retire_age: plan.people[0].retire_age, savings: 0 });
    keep();
  }
  function removePartner() {
    plan.people.splice(1, 1);
    plan.income = plan.income.filter((i) => i.person === 0);
    keep();
  }
  const addIncome = (name: string, start_age: number) => { plan.income.push({ name, amount: 0, person: 0, start_age, end_age: null }); keep(); };
  const addEvent = () => { plan.events.push({ name: "", year: year + 5, amount: -10000 }); keep(); };
  const sale = (key: string) => plan.assets.find((a) => a.key === key);
  function toggleSale(key: string, on: boolean) {
    plan.assets = on ? [...plan.assets, { key, sell_year: plan.people[0].birth_year + plan.people[0].retire_age }] : plan.assets.filter((a) => a.key !== key);
    keep();
  }
  // What a sale's estimate assumes, for its tooltip: "Home worth $X in 2057, less $Y still owed on the loan at 6.25%".
  const PAYMENT_FROM = { plaid: "from Plaid", manual: "as you set it", inferred: "from recent payments" } as const;
  function saleTitle(a: PlanAsset, sellYear: number): string {
    const { value, owed } = saleAt(a, sellYear, year, plan.inflation);
    const worth = a.kind === "equity" ? `${a.name}: ${fmt0(value)} vested by ${sellYear}, at today’s share price`
      : `${a.name} worth ${fmt0(value)} in ${sellYear}`;
    const l = a.loan;
    let loan = "";
    if (l && loanProjected(a)) {
      const terms = `${+(l.rate ?? 0).toFixed(3)}% and ${fmt0(l.payment ?? 0)} a month ${PAYMENT_FROM[l.source ?? "manual"]}`;
      loan = owed > 0 ? `, less ${fmt0(owed)} still owed on the loan at ${terms}` : `; the loan (${terms}) is paid off by then`;
    } else if (a.owed) loan = `, less ${fmt0(owed)} owed on the loan today`;
    return `${worth}${loan}. In today’s dollars.`;
  }
  // Why a loan's balance isn't projected (what's owed today is used instead), and what would fix it.
  const LOAN_NOTES = {
    no_rate: "add the loan’s interest rate", no_payment: "add the loan’s monthly payment",
    payment_below_interest: "its payment doesn’t cover the interest: check the loan’s terms",
  } as const;

  // An event is typed as money in or out plus a positive amount; it's kept signed.
  const setEventSign = (i: number, out: boolean) => { plan.events[i].amount = (out ? -1 : 1) * Math.abs(num(plan.events[i].amount)); keep(); };
  const setEventAmount = (i: number, s: string) => { plan.events[i].amount = (plan.events[i].amount < 0 ? -1 : 1) * Math.abs(Number(s) || 0); keep(); };
</script>

{#snippet field(label: string, hint?: string)}
  <span class="text-sm">{label}{#if hint}<span class="text-muted-foreground">{` · ${hint}`}</span>{/if}</span>
{/snippet}
{#snippet money(value: number, set: (v: number) => void, label: string)}
  <span class="relative">
    <span class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true">$</span>
    <Input type="number" step="1000" min="0" class="pl-6" aria-label={label} value={value}
      oninput={(e) => { set(Number(e.currentTarget.value) || 0); keep(); }} />
  </span>
{/snippet}
{#snippet percent(value: number, set: (s: string) => void, label: string)}
  <span class="relative">
    <span class="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true">%</span>
    <Input type="number" step="0.5" class="pr-7" aria-label={label} value={pctIn(value)} oninput={(e) => set(e.currentTarget.value)} />
  </span>
{/snippet}

{#if empty}
  <div class="rounded-lg border border-dashed px-6 py-10 text-center">
    <h3 class="font-medium">Nothing to plan from yet</h3>
    <p class="mx-auto mt-1 max-w-md text-sm text-muted-foreground">Runway plans from your investment accounts. Connect one, or enter holdings by
      hand, and the planner starts from real numbers.</p>
    {#if !isPhone()}<Button class="mt-4" href="#networth/investments">Go to Investments</Button>{/if}
  </div>
{:else}
{#if sample}
  <p class="mb-3 text-sm text-muted-foreground">
    <span class="mr-2 inline-block rounded-full border px-2 py-0.5 text-xs font-medium">Sample · based on default assumptions</span>
    Starting from Runway's figures: your investments, and what you've spent and saved over the last year. <strong class="font-medium text-foreground">Enter
    your birth year and retirement age{isPhone() ? " on a computer" : ""} to make it yours.</strong></p>
{/if}

{#if proj}
  <div class="flex flex-wrap gap-x-8 gap-y-3">
    <div>
      <div class="text-sm text-muted-foreground">Chance your money lasts</div>
      <div class={`text-2xl font-semibold tabular-nums ${successCls(proj.success)}`}>{Math.round(proj.success * 100)}%</div>
      <div class="text-sm text-muted-foreground">to age {plan.plan_to_age}, across 1,000 markets</div>
    </div>
    <div>
      <div class="text-sm text-muted-foreground">Invested at retirement</div>
      <div class="text-2xl font-semibold tabular-nums">{fmt0(proj.atRetirement)}</div>
      <div class="text-sm text-muted-foreground">{fmt0(proj.low[proj.retireIndex])} – {fmt0(proj.high[proj.retireIndex])} likely</div>
    </div>
    <div>
      {#if proj.runsOutAge != null}
        <div class="text-sm text-muted-foreground">Typically runs out</div>
        <div class={`text-2xl font-semibold tabular-nums ${sample ? "text-muted-foreground" : "text-[var(--low)]"}`}>age {proj.runsOutAge}</div>
        <div class="text-sm text-muted-foreground">save more, spend less or retire later</div>
      {:else}
        <div class="text-sm text-muted-foreground">Left at age {plan.plan_to_age}</div>
        <div class="text-2xl font-semibold tabular-nums">{fmt0(proj.atEnd)}</div>
        <div class="text-sm text-muted-foreground">{proj.lowRunsOutAge != null ? `in poor markets it runs out at ${proj.lowRunsOutAge}` : "typically; in today's dollars"}</div>
      {/if}
    </div>
  </div>
  <p class="mt-3 text-sm text-muted-foreground">Based on your {fmt0(data.current)} in investments today. Cash, home equity and equity comp aren't
    included, and taxes aren't modeled. Not financial advice.</p>
  <div class="mt-4"><PlannerChart p={proj} {names} /></div>
{:else}
  <p class="py-6 text-center text-sm text-muted-foreground">Enter a birth year and retirement age{isPhone() ? " on a computer" : ""} to see the projection.</p>
{/if}
{#if problem}<p class="mt-2 text-sm text-destructive" role="alert">Not saved: {problem}</p>{/if}

{#if isPhone()}
  <DesktopOnly class="mt-5" what="change the plan" />
{:else}
<div class="mt-5 grid gap-6 lg:grid-cols-2">
  <section>
    <h3 class="mb-2 font-medium">Who's retiring</h3>
    {#each plan.people as person, i (i)}
      <div class="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <label class="flex flex-col gap-1">{@render field("Name")}
          <Input value={person.name} oninput={(e) => { person.name = e.currentTarget.value; keep(); }} /></label>
        <label class="flex flex-col gap-1">{@render field("Born in")}
          <Input type="number" step="1" value={person.birth_year} oninput={(e) => { person.birth_year = Number(e.currentTarget.value); keep(); }} /></label>
        <label class="flex flex-col gap-1">{@render field("Retires at", `age`)}
          <Input type="number" step="1" value={person.retire_age} oninput={(e) => { person.retire_age = Number(e.currentTarget.value); keep(); }} /></label>
        <label class="flex flex-col gap-1">{@render field("Saves a year")}
          {@render money(person.savings, (v) => (person.savings = v), `${names[i]}'s yearly savings`)}</label>
      </div>
    {/each}
    {#if plan.people.length === 1}
      <Button variant="outline" size="sm" onclick={addPartner}><Plus /> Add a partner</Button>
    {:else}
      <Button variant="ghost" size="sm" onclick={removePartner}><X /> Remove {names[1]}</Button>
    {/if}
    <p class="mt-2 text-sm text-muted-foreground">Savings stop at each person's retirement. Spending comes from the investments once everyone has retired.</p>
  </section>

  <section>
    <h3 class="mb-2 font-medium">In retirement</h3>
    <div class="grid grid-cols-2 gap-3">
      <label class="flex flex-col gap-1">{@render field("Spending a year", "today's dollars")}
        {@render money(plan.spending, (v) => (plan.spending = v), "Yearly spending in retirement")}</label>
      <label class="flex flex-col gap-1">{@render field("Plan until age")}
        <Input type="number" step="1" value={plan.plan_to_age} oninput={(e) => { plan.plan_to_age = Number(e.currentTarget.value); keep(); }} /></label>
    </div>
  </section>

  <section class="lg:col-span-2">
    <h3 class="mb-2 font-medium">Income in retirement</h3>
    {#each plan.income as inc, i (i)}
      <div class="mb-2 grid grid-cols-[1fr_auto] items-end gap-2">
        <div class="grid grid-cols-2 gap-2 sm:grid-cols-5">
          <label class="col-span-2 flex flex-col gap-1 sm:col-span-1">{@render field("What")}
            <Input value={inc.name} oninput={(e) => { inc.name = e.currentTarget.value; keep(); }} /></label>
          {#if plan.people.length > 1}
            <label class="flex flex-col gap-1">{@render field("Whose")}
              <NativeSelect value={String(inc.person)} onchange={(e) => { inc.person = Number(e.currentTarget.value); keep(); }}>
                {#each names as nm, k (k)}<option value={String(k)}>{nm}</option>{/each}
              </NativeSelect></label>
          {/if}
          <label class="flex flex-col gap-1">{@render field("A year")}{@render money(inc.amount, (v) => (inc.amount = v), `${inc.name} a year`)}</label>
          <label class="flex flex-col gap-1">{@render field("From age")}
            <Input type="number" step="1" value={inc.start_age} oninput={(e) => { inc.start_age = Number(e.currentTarget.value); keep(); }} /></label>
          <label class="flex flex-col gap-1">{@render field("Until age")}
            <Input type="number" step="1" placeholder="for life" value={inc.end_age ?? ""}
              oninput={(e) => { inc.end_age = e.currentTarget.value === "" ? null : Number(e.currentTarget.value); keep(); }} /></label>
        </div>
        <Button variant="ghost" size="icon" aria-label={`Remove ${inc.name || "income"}`} onclick={() => { plan.income.splice(i, 1); keep(); }}><X /></Button>
      </div>
    {/each}
    <div class="flex flex-wrap gap-2">
      <Button variant="outline" size="sm" onclick={() => addIncome("Social Security", 67)}><Plus /> Social Security</Button>
      <Button variant="outline" size="sm" onclick={() => addIncome("Pension", 65)}><Plus /> Pension</Button>
      <Button variant="outline" size="sm" onclick={() => addIncome("", 60)}><Plus /> Other income</Button>
    </div>
    <p class="mt-2 text-sm text-muted-foreground">Your Social Security estimate is at ssa.gov/myaccount, in today's dollars.</p>
  </section>

  <section class="lg:col-span-2">
    <h3 class="mb-2 font-medium">One-time events</h3>
    {#each plan.events as ev, i (i)}
      <div class="mb-2 grid grid-cols-[1fr_auto] items-end gap-2">
        <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <label class="col-span-2 flex flex-col gap-1 sm:col-span-1">{@render field("What")}
            <Input placeholder="College, new roof…" value={ev.name} oninput={(e) => { ev.name = e.currentTarget.value; keep(); }} /></label>
          <label class="flex flex-col gap-1">{@render field("Year")}
            <Input type="number" step="1" value={ev.year} oninput={(e) => { ev.year = Number(e.currentTarget.value); keep(); }} /></label>
          <label class="flex flex-col gap-1">{@render field("Money")}
            <NativeSelect value={ev.amount < 0 ? "out" : "in"} onchange={(e) => setEventSign(i, e.currentTarget.value === "out")}>
              <option value="out">Spend</option><option value="in">Receive</option>
            </NativeSelect></label>
          <label class="flex flex-col gap-1">{@render field("Amount")}
            <span class="relative">
              <span class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true">$</span>
              <Input type="number" step="1000" min="0" class="pl-6" value={Math.abs(ev.amount)} oninput={(e) => setEventAmount(i, e.currentTarget.value)} />
            </span></label>
        </div>
        <Button variant="ghost" size="icon" aria-label={`Remove ${ev.name || "event"}`} onclick={() => { plan.events.splice(i, 1); keep(); }}><X /></Button>
      </div>
    {/each}
    <Button variant="outline" size="sm" onclick={addEvent}><Plus /> Add an event</Button>
    <p class="mt-2 text-sm text-muted-foreground">A home purchase, college, a wedding, an inheritance: money in or out in one year.</p>
  </section>

  <section class="lg:col-span-2">
    <h3 class="mb-2 font-medium">Homes &amp; other assets</h3>
    {#if data.assets.length}
      <ul class="space-y-2">
        {#each data.assets as a (a.key)}
          {@const s = sale(a.key)}
          <li class="flex flex-wrap items-center gap-x-3 gap-y-1">
            <label class="flex min-w-40 flex-1 items-center gap-2">
              <input type="checkbox" class="size-4 cursor-pointer accent-primary" checked={!!s} onchange={(e) => toggleSale(a.key, e.currentTarget.checked)} />
              <span>{a.name} <span class="text-sm text-muted-foreground">{fmt0(a.value - a.owed)}{a.owed ? " after the loan today" : ""}</span></span>
            </label>
            {#if s}
              <label class="flex items-center gap-2 text-sm">Sell in
                <Input type="number" step="1" class="w-24" value={s.sell_year} oninput={(e) => { s.sell_year = Number(e.currentTarget.value); keep(); }} /></label>
              <!-- A fixed width, right-aligned, so each row's Sell in lines up whatever the amount -->
              <span class="min-w-28 text-right text-sm text-muted-foreground tabular-nums" title={saleTitle(a, num(s.sell_year))}>≈ {fmt0(saleProceeds(a, num(s.sell_year), year, plan.inflation))}</span>
              {#if a.owed > 0 && a.loan?.note}
                <p class="basis-full pl-6 text-xs text-muted-foreground">Counts what’s owed today: {LOAN_NOTES[a.loan.note]} in
                  <a class="font-medium whitespace-nowrap text-foreground underline underline-offset-4" href="#setup/accounts">Settings → Accounts</a> to project it.</p>
              {/if}
            {/if}
          </li>
        {/each}
      </ul>
      <p class="mt-2 flex flex-wrap items-center gap-x-1 text-sm text-muted-foreground">Tick one to sell it into your investments that year, say
        downsizing. Its value grows by the yearly change set on the Net worth page, a loan is paid down on its terms and
        equity keeps vesting until then, and sale proceeds are reduced by
        <span class="relative inline-block w-20">
          <span class="pointer-events-none absolute top-1/2 right-2 -translate-y-1/2 text-xs" aria-hidden="true">%</span>
          <Input type="number" step="0.5" class="h-7 pr-6 text-sm" aria-label="Inflation" value={pctIn(plan.inflation)} oninput={(e) => setPct("inflation", e.currentTarget.value)} />
        </span> a year of inflation.</p>
    {:else}
      <p class="text-sm text-muted-foreground">Homes, vehicles and company equity you add on the Net worth page can be sold into the plan here.</p>
    {/if}
  </section>

  <section class="lg:col-span-2">
    <h3 class="mb-2 font-medium">Assumptions</h3>
    <div class="grid grid-cols-2 gap-3 sm:grid-cols-3">
      <label class="flex flex-col gap-1">{@render field("Return while saving", "after inflation")}
        {@render percent(plan.return_before, (s) => setPct("return_before", s), "Return while saving")}</label>
      <label class="flex flex-col gap-1">{@render field("Return in retirement", "after inflation")}
        {@render percent(plan.return_after, (s) => setPct("return_after", s), "Return in retirement")}</label>
      <label class="flex flex-col gap-1">{@render field("Ups and downs", "yearly")}
        {@render percent(plan.volatility, (s) => setPct("volatility", s), "Ups and downs")}</label>
    </div>
    <p class="mt-2 text-sm text-muted-foreground">
      Starts from the {fmt0(data.current)} you have invested. Each of the 1,000 runs draws every year's return around these averages, which are
      already after inflation (everything is in today's dollars); "ups and downs" is how far a year typically strays (a stock-heavy portfolio is
      about 15%, a balanced one about 10%). Enter spending as what you'd withdraw before tax.
      {#if !isDefault}<ConfirmButton confirm="Start over? This clears everything you entered here." class="h-auto px-1" onconfirm={startOver}>Start over from Runway's figures</ConfirmButton>{/if}
    </p>
  </section>
</div>
{/if}
{/if}

{#if saved}
  <div class="fixed right-4 bottom-20 z-40 rounded-md bg-card px-3 py-1.5 text-sm text-[var(--good)] shadow-lg ring-1 ring-border lg:bottom-4" role="status"
    style:animation="saved-fade 1.6s ease forwards">Saved ✓</div>
{/if}
