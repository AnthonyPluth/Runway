<script lang="ts">
  import { api } from "$lib/api";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { fmt0 } from "$lib/format";
  import Plus from "@lucide/svelte/icons/plus";
  import X from "@lucide/svelte/icons/x";
  import PlannerChart from "./PlannerChart.svelte";
  import { project, saleProceeds } from "./planner";
  import type { Plan, PlanData } from "./types";

  // The retirement planner: your household's plan from now to the end, projected a thousand ways (planner.ts).
  // Everything is in today's dollars. Changes are kept a moment after you stop typing.
  let { data }: { data: PlanData } = $props();

  const copy = (p: Plan): Plan => JSON.parse(JSON.stringify(p));
  const initialPlan = () => copy(data.plan);
  let plan = $state<Plan>(initialPlan());
  const wasDefault = () => data.is_default;
  let isDefault = $state(wasDefault());
  let problem = $state<string | null>(null);
  const year = $derived(data.year);

  // Keep the plan (a moment after the last change). A plan the server refuses stays on screen with the reason.
  let timer: ReturnType<typeof setTimeout>;
  function keep() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        await api("/api/investments/plan", { method: "POST", body: { plan: $state.snapshot(plan) } });
        problem = null; isDefault = false;
      } catch (err) { problem = (err as Error).message; }
    }, 700);
  }
  async function startOver() {
    clearTimeout(timer);
    try { await api("/api/investments/plan", { method: "POST", body: { plan: null } }); }
    catch (err) { problem = (err as Error).message; return; }
    plan = copy({ ...data.plan, ...defaults() });
    isDefault = true; problem = null;
  }
  // Runway's own starting figures (what planner.default() gives).
  const defaults = (): Partial<Plan> => ({
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
    const p = $state.snapshot(plan) as Plan;
    p.people = p.people.map((x) => ({ ...x, savings: num(x.savings) }));
    p.spending = num(p.spending);
    p.income = p.income.filter((i) => i.person < p.people.length).map((i) => ({ ...i, amount: num(i.amount), start_age: num(i.start_age),
      end_age: typeof i.end_age === "number" && isFinite(i.end_age) ? i.end_age : null }));
    p.events = p.events.map((e) => ({ ...e, amount: num(e.amount), year: num(e.year) }));
    return project(p, data.current, year, data.assets);
  });
  const names = $derived(plan.people.map((p, i) => p.name || (i ? "Partner" : "You")));
  const successCls = (s: number) => (s >= 0.85 ? "text-[var(--good)]" : s >= 0.7 ? "text-[var(--warning)]" : "text-[var(--low)]");

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

{#if isDefault}
  <p class="mb-3 text-sm text-muted-foreground">Starting from Runway's figures: your investments, and what you've spent and saved over the
    last year. Enter your birth year and when you'd like to retire to make it yours.</p>
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
        <div class="text-2xl font-semibold tabular-nums text-[var(--low)]">age {proj.runsOutAge}</div>
        <div class="text-sm text-muted-foreground">save more, spend less or retire later</div>
      {:else}
        <div class="text-sm text-muted-foreground">Left at age {plan.plan_to_age}</div>
        <div class="text-2xl font-semibold tabular-nums">{fmt0(proj.atEnd)}</div>
        <div class="text-sm text-muted-foreground">{proj.lowRunsOutAge != null ? `in poor markets it runs out at ${proj.lowRunsOutAge}` : "typically; in today's dollars"}</div>
      {/if}
    </div>
  </div>
  <div class="mt-4"><PlannerChart p={proj} {names} /></div>
{:else}
  <p class="py-6 text-center text-sm text-muted-foreground">Enter a birth year and retirement age to see the projection.</p>
{/if}
{#if problem}<p class="mt-2 text-sm text-destructive" role="alert">Not saved: {problem}</p>{/if}

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
              <span>{a.name} <span class="text-sm text-muted-foreground">{fmt0(a.value - a.owed)}{a.owed ? " after the loan" : ""}</span></span>
            </label>
            {#if s}
              <label class="flex items-center gap-2 text-sm">Sell in
                <Input type="number" step="1" class="w-24" value={s.sell_year} oninput={(e) => { s.sell_year = Number(e.currentTarget.value); keep(); }} /></label>
              <span class="text-sm text-muted-foreground tabular-nums">≈ {fmt0(saleProceeds(a, num(s.sell_year), year, plan.inflation))}</span>
            {/if}
          </li>
        {/each}
      </ul>
      <p class="mt-2 text-sm text-muted-foreground">Tick one to sell it into your investments that year, say downsizing. Its value grows by the yearly
        change set on the Net worth page, less inflation.</p>
    {:else}
      <p class="text-sm text-muted-foreground">Homes, vehicles and company equity you add on the Net worth page can be sold into the plan here.</p>
    {/if}
  </section>

  <section class="lg:col-span-2">
    <h3 class="mb-2 font-medium">Assumptions</h3>
    <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <label class="flex flex-col gap-1">{@render field("Return while saving", "after inflation")}
        {@render percent(plan.return_before, (s) => setPct("return_before", s), "Return while saving")}</label>
      <label class="flex flex-col gap-1">{@render field("Return in retirement", "after inflation")}
        {@render percent(plan.return_after, (s) => setPct("return_after", s), "Return in retirement")}</label>
      <label class="flex flex-col gap-1">{@render field("Ups and downs", "yearly")}
        {@render percent(plan.volatility, (s) => setPct("volatility", s), "Ups and downs")}</label>
      <label class="flex flex-col gap-1">{@render field("Inflation")}
        {@render percent(plan.inflation, (s) => setPct("inflation", s), "Inflation")}</label>
    </div>
    <p class="mt-2 text-sm text-muted-foreground">
      Starts from the {fmt0(data.current)} you have invested. Each of the 1,000 runs draws every year's return around these averages; "ups
      and downs" is how far a year typically strays (a stock-heavy portfolio is about 15%, a balanced one about 10%). Taxes aren't
      modeled: enter spending as what you'd withdraw before tax.
      {#if !isDefault}<Button variant="link" size="sm" class="h-auto px-1" onclick={startOver}>Start over from Runway's figures</Button>{/if}
    </p>
  </section>
</div>
