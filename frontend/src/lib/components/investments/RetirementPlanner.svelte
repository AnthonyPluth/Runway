<script lang="ts">
  import { api } from "$lib/api";
  import ConfirmButton from "$lib/components/ConfirmButton.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { fmt0, fmtDate, plural } from "$lib/format";
  import { commas } from "$lib/commas";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import Plus from "@lucide/svelte/icons/plus";
  import X from "@lucide/svelte/icons/x";
  import { debounced } from "$lib/debounce";
  import { onDestroy } from "svelte";
  import PlannerChart from "./PlannerChart.svelte";
  import { addedPayments, counted, type Dollars, endingPayments, inDollars, loanProjected, paymentEnds, project,
    projectionIn, sale as saleAt, saleProceeds, saleYear } from "./planner";
  import type { PlanAsset, PlanData, RetirementPlan } from "./types";
  import { errMsg } from "$lib/act";

  // The retirement planner: your household's plan from now to the end, projected a thousand ways (planner.ts).
  // The plan and its projection are in today's dollars; the figures can be shown in future dollars instead.
  // Changes are kept a moment after you stop typing (or when you leave). Phones get the same fields, stacked.
  // `accounts`: how many investment accounts the starting figure adds up, for the line above the results.
  let { data, accounts = 0 }: { data: PlanData; accounts?: number } = $props();
  const uid = $props.id();

  const copy = (p: RetirementPlan): RetirementPlan => JSON.parse(JSON.stringify(p));
  const initialPlan = () => copy(data.plan);
  let plan = $state<RetirementPlan>(initialPlan());
  const wasDefault = () => data.is_default;
  let isDefault = $state(wasDefault());
  let problem = $state<string | null>(null);
  const year = $derived(data.year);

  // Keep the plan (a moment after the last change, or as you leave, so a quick tab switch doesn't drop it). A plan the
  // server refuses stays on screen with the reason; one it takes shows "Saved ✓" for a moment.
  let saved = $state(false);
  const unflash = debounced(() => (saved = false), 1600);
  async function save() {
    later.cancel();
    try {
      await api("/api/investments/plan", { method: "POST", body: { plan: $state.snapshot(plan) } });
      problem = null; isDefault = false; saved = true;
      unflash.call();
    } catch (err) { problem = errMsg(err); }
  }
  const later = debounced(save, 700);
  const keep = () => later.call();
  onDestroy(() => { unflash.cancel(); later.flush(); });
  async function startOver() {
    later.cancel();
    try { await api("/api/investments/plan", { method: "POST", body: { plan: null } }); }
    catch (err) { problem = errMsg(err); return; }
    plan = copy({ ...data.plan, ...defaults() });
    isDefault = true; problem = null; assumptionsOpen = true;
  }
  // Runway's own starting figures (what planner.default() gives).
  const defaults = (): Partial<RetirementPlan> => ({
    people: [{ name: "You", birth_year: year - 40, retire_age: 65, savings: data.computed.yearly_savings }],
    plan_to_age: 95, spending: data.computed.annual_spending, spending_own: false, return_before: data.computed.expected_return,
    return_after: 0.04, volatility: 0.12, inflation: 0.025, income: [], events: [], assets: [],
  });

  // Numbers as typed: an empty or half-typed box counts as nothing rather than breaking the projection.
  const num = (v: unknown) => (typeof v === "number" && isFinite(v) ? v : 0);
  const pctIn = (v: number) => String(+(v * 100).toFixed(2));
  const setPct = (k: "return_before" | "return_after" | "volatility" | "inflation", s: string) => { plan[k] = (Number(s) || 0) / 100; keep(); };

  // Ages that can't be: retiring before the age you are now, or planning to an age before retirement. Each says so
  // under its field, and there's no projection until they're fixed.
  const ageNow = (p: RetirementPlan["people"][number]) => year - num(p.birth_year);
  const retireErr = (p: RetirementPlan["people"][number]) =>
    num(p.birth_year) > 1900 && num(p.retire_age) > 0 && num(p.retire_age) < ageNow(p) ? `At least ${ageNow(p)}, your age now` : null;
  // How long to plan for is in the first person's age, like the chart's.
  const retireAt = $derived(num(plan.people[0].retire_age));
  const planToErr = $derived(num(plan.plan_to_age) > 0 && retireAt > 0 && num(plan.plan_to_age) <= retireAt ? `Past the retirement age (${retireAt})` : null);
  const agesOk = $derived(plan.people.every((p) => !retireErr(p)) && !planToErr);
  const usable = $derived(plan.people.every((p) => num(p.birth_year) > 1900 && num(p.retire_age) > 0) && num(plan.plan_to_age) > 0 && agesOk);
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

  // Today's or future dollars: a way of looking at the plan rather than part of it, so each browser remembers its own
  // choice (the plan is the household's, and switching shouldn't save it). Without storage it starts in today's dollars.
  const DOLLARS_KEY = "runway.planner.dollars";
  function storedDollars(): Dollars {
    try { return localStorage.getItem(DOLLARS_KEY) === "future" ? "future" : "today"; } catch { return "today"; }
  }
  let dollars = $state<Dollars>(storedDollars());
  function setDollars(v: string) {
    dollars = v === "future" ? "future" : "today";
    try { localStorage.setItem(DOLLARS_KEY, dollars); } catch { /* not remembered; it still switches */ }
  }
  // A figure for a year in the dollars chosen, and the projection with each year's figures in them (planner.ts).
  const shownIn = (v: number, y: number) => inDollars(v, y, year, num(plan.inflation), dollars);
  const shown = $derived(proj && projectionIn(proj, year, num(plan.inflation), dollars));
  // Where the saving figure comes from, when its history is shorter than a year.
  const savingsNote = $derived(data.computed.savings_measured !== false && data.computed.savings_since
    ? `Your investment history only goes back to ${fmtDate(data.computed.savings_since, { month: "short", day: "numeric", year: "numeric" })}.` : undefined);
  const inflationPct = $derived(`${pctIn(num(plan.inflation))}%`);
  // Nothing invested and no plan of your own yet: nothing to project from. With investments but still Runway's guesses,
  // the results are a sample, so they're shown muted rather than as a verdict.
  const empty = $derived(data.current <= 0 && isDefault);
  const sample = $derived(isDefault && data.current > 0);
  const successCls = (s: number) => (sample ? "text-muted-foreground" : s >= 0.85 ? "text-[var(--good)]" : s >= 0.7 ? "text-[var(--warning)]" : "text-[var(--low)]");
  // The odds in a word, by the same thresholds as their color; the thresholds are in its tooltip.
  const verdict = (s: number) => (s >= 0.85 ? "Likely" : s >= 0.7 ? "Uncertain" : "Unlikely");
  const VERDICT_TIP = "Likely at 85% or more, uncertain from 70%, unlikely below 70%";

  // Set-once figures (birth years, how long to plan for, income in retirement, returns and inflation) sit under
  // Assumptions: open while something's still missing (Runway's sample plan, or a birth year that isn't one), folded
  // to a one-line summary once they're in. It stays as you leave it while you type.
  const bornOk = (y: unknown) => num(y) >= year - 100 && num(y) <= year - 14;
  const assumptionsMissing = () => isDefault || !plan.people.every((p) => bornOk(p.birth_year));
  let assumptionsOpen = $state(assumptionsMissing());
  // An age to fix that's folded away under Assumptions opens them.
  $effect(() => { if (planToErr) assumptionsOpen = true; });
  const assumptionsSummary = $derived.by(() => {
    const born = plan.people.length > 1 ? plan.people.map((p, i) => `${names[i]} born ${p.birth_year}`).join(", ") : `Born ${plan.people[0].birth_year}`;
    const income = plan.income.map((inc) => `${inc.name || "Income"} ${fmt0(num(inc.amount))} a year at ${inc.start_age}`);
    const returns = `${pctIn(num(plan.return_before))}% returns (${pctIn(num(plan.return_after))}% retired), ${inflationPct} inflation`;
    return [born, `to age ${plan.plan_to_age}`, ...(income.length ? income : ["no retirement income yet"]), returns].join(" · ");
  });

  function addPartner() {
    plan.people.push({ name: "Partner", birth_year: plan.people[0].birth_year, retire_age: plan.people[0].retire_age, savings: 0 });
    assumptionsOpen = true;   // where the partner's birth year goes
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
    const retires = plan.people[0].birth_year + plan.people[0].retire_age;
    plan.assets = on ? [...plan.assets, { key, sell_year: saleYear(retires, year) }] : plan.assets.filter((a) => a.key !== key);
    keep();
  }
  // A sale's year as typed: one before this year isn't kept (the server refuses it), and counts this year meanwhile.
  function setSellYear(s: RetirementPlan["assets"][number], v: string) {
    s.sell_year = Number(v);
    delete s.was;   // you've chosen a year: the note about the one that had passed has done its job
    keep();
  }
  const pastYear = (s: RetirementPlan["assets"][number]) => num(s.sell_year) < year;
  // Equity still vesting: what it comes to once it's all vested, at today's share price (value_by_year's last entry).
  const vestsTo = (a: PlanAsset) => (a.kind === "equity" && (a.value_by_year?.length ?? 0) > 1 ? a.value_by_year![a.value_by_year!.length - 1] : null);
  // What the plan counts, and what it lists only for a loan's payment (loans against nothing). Vehicles aren't listed:
  // the plan neither counts nor sells them, though a loan against one is still paid from spending until it's paid off.
  const plannable = $derived(data.assets.filter(counted));
  const listed = $derived(data.assets.filter((a) => a.kind === "loan"));
  // What a sale's estimate assumes, for its tooltip: "Home worth $X in 2057, less $Y still owed on the loan at 6.25%".
  const PAYMENT_FROM = { plaid: "from Plaid", manual: "as you set it", inferred: "from recent payments" } as const;
  // In future dollars each figure is the sale year's: the home's value grown at its own rate, the loan's balance then.
  function saleTitle(a: PlanAsset, sellYear: number): string {
    const real = saleAt(a, sellYear, year, plan.inflation);
    const value = shownIn(real.value, sellYear), owed = shownIn(real.owed, sellYear);
    const worth = a.kind === "equity" ? `${a.name}: ${fmt0(value)} vested by ${sellYear}, at today’s share price${dollars === "future" ? " grown with inflation" : ""}`
      : `${a.name} worth ${fmt0(value)} in ${sellYear}`;
    const l = a.loan;
    let loan = "";
    if (l && loanProjected(a)) {
      const terms = `${+(l.rate ?? 0).toFixed(3)}% and ${fmt0(l.payment ?? 0)} a month ${PAYMENT_FROM[l.source ?? "manual"]}`;
      loan = owed > 0 ? `, less ${fmt0(owed)} still owed on the loan at ${terms}` : `; the loan (${terms}) is paid off by then`;
    } else if (a.owed) {   // held at today's balance in today's dollars, so it grows with inflation in future ones
      loan = dollars === "future" ? `, less the ${fmt0(a.owed)} owed on the loan today (${fmt0(owed)} in ${sellYear} dollars)`
        : `, less ${fmt0(owed)} owed on the loan today`;
    }
    const unit = dollars === "future" ? `In ${sellYear} dollars, at ${inflationPct} a year inflation.` : "In today’s dollars.";
    return `${worth}${loan}. ${unit}`;
  }
  // Loan payments and Runway's spending figure (planner.ts flows): one that's in the figure comes off it once the loan
  // is paid off or its asset sold; one that isn't (a payment categorized as a transfer) is added to it while it's
  // still paid. A figure you typed is taken as it is.
  const ending = $derived(endingPayments($state.snapshot(plan) as RetirementPlan, data.assets, year));
  const added = $derived(addedPayments($state.snapshot(plan) as RetirementPlan, data.assets, year));
  function useRunwaySpending() { plan.spending = data.computed.annual_spending; plan.spending_own = false; keep(); }

  // What happens to a loan's monthly payment in the plan, in plain words: whether it's already in the spending figure
  // and when it stops.
  function paymentLine(a: PlanAsset, sellYear: number | null): string {
    const l = a.loan!;
    const pay = `${fmt0(l.payment ?? 0)}/month`;
    const ends = paymentEnds(a, sellYear);
    const when = ends == null ? null : l.payoff_year == null || ends <= l.payoff_year ? `until it’s sold in ${ends}` : `until it’s paid off in ${l.payoff_year}`;
    const why = l.note === "payment_below_interest" ? " It doesn’t cover the interest, so it never pays the loan down."
      : l.note === "no_rate" ? " Add the loan’s interest rate in Settings → Accounts to see when it ends." : "";
    if (plan.spending_own) {
      return `Its ${pay} loan payment goes on ${when ?? "past the end of the plan"}.${why}`;
    }
    if (l.payment_counted) {
      return when ? `Its ${pay} loan payment is already in your spending, ${when}; from ${ends} the plan takes it off.${why}`
        : `Its ${pay} loan payment is already in your spending, and stays in it.${why}`;
    }
    if (l.payment_counted == null) {
      return `Runway couldn’t tell whether its ${pay} loan payment is in your spending, so the plan leaves your spending as it is.${why}`;
    }
    return `Its ${pay} loan payment was paid as a transfer, so it isn’t in your spending and the plan adds it to your spending in retirement ${when ?? "for as long as the plan runs"}.${why}`;
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
    <Input type="number" step="1000" min="0" class="pl-6" aria-label={label} value={value} {@attach commas}
      oninput={(e) => { set(Number(e.currentTarget.value) || 0); keep(); }} />
  </span>
{/snippet}
{#snippet percent(value: number, set: (s: string) => void, label: string, id?: string)}
  <span class="relative">
    <span class="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true">%</span>
    <Input type="number" step="0.5" class="pr-7" {id} aria-label={label} value={pctIn(value)} oninput={(e) => set(e.currentTarget.value)} />
  </span>
{/snippet}

{#if empty}
  <div class="rounded-lg border border-dashed px-6 py-10 text-center"
    title="Runway plans from your investment accounts. Connect one, or enter holdings by hand, and the planner starts from real numbers.">
    <h3 class="font-medium">Nothing to plan from yet</h3>
    <Button class="mt-4" href="#networth/investments">Go to Investments</Button>
  </div>
{:else}
{#if shown}
  {@const future = dollars === "future"}
  <div class="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1.5">
    <Segmented label="Show amounts in" value={dollars} onchange={setDollars}
      options={[{ value: "today", label: "Today’s dollars" }, { value: "future", label: "Future dollars" }]} />
    {#if sample}<Badge variant="secondary" data-testid="starting-estimate" title="These results start from Runway’s own guesses; change any figure below to make the plan yours">Starting estimate · edit below</Badge>{/if}
  </div>
  {#if data.current > 0}
    <p class="mb-3 text-sm text-muted-foreground" data-testid="starting-from">Starting from <span class="font-medium text-foreground tabular-nums">{fmt0(data.current)}</span> invested today{accounts ? ` (${plural(accounts, "account")})` : ""}</p>
  {/if}
  <div class="flex flex-wrap gap-x-8 gap-y-3">
    <div>
      <div class="text-sm text-muted-foreground">Chance your money lasts</div>
      <div class="flex items-baseline gap-2">
        <span class={`text-2xl font-semibold tabular-nums ${successCls(shown.success)}`}>{Math.round(shown.success * 100)}%</span>
        <span class={`rounded-full border border-current/30 px-2 py-0.5 text-xs font-medium ${successCls(shown.success)}`} title={VERDICT_TIP} data-testid="verdict">{verdict(shown.success)}</span>
      </div>
      <div class="text-sm text-muted-foreground">to age {plan.plan_to_age}, across 1,000 simulated market paths</div>
    </div>
    <div>
      <div class="text-sm text-muted-foreground">Invested at retirement{future ? ` · ${shown.years[shown.retireIndex]}` : ""}</div>
      <div class="text-2xl font-semibold tabular-nums">{fmt0(shown.atRetirement)}</div>
      <div class="text-sm text-muted-foreground">{fmt0(shown.low[shown.retireIndex])} – {fmt0(shown.high[shown.retireIndex])} likely</div>
    </div>
    <div>
      {#if shown.runsOutAge != null}
        <div class="text-sm text-muted-foreground">Runs out</div>
        <div class={`text-2xl font-semibold tabular-nums ${sample ? "text-muted-foreground" : "text-[var(--low)]"}`}>age {shown.runsOutAge}</div>
      {:else}
        <div class="text-sm text-muted-foreground">Left at age {plan.plan_to_age}{future ? ` · ${shown.years[shown.years.length - 1]}` : ""}</div>
        <div class="text-2xl font-semibold tabular-nums">{fmt0(shown.atEnd)}</div>
        {#if shown.lowRunsOutAge != null}<div class="text-sm text-muted-foreground">in poor markets it runs out at {shown.lowRunsOutAge}</div>{/if}
      {/if}
    </div>
  </div>
  <div class="mt-4"><PlannerChart p={shown} {names} {dollars} /></div>
{:else}
  <p class="py-6 text-center text-sm text-muted-foreground">{agesOk ? "Enter a birth year and retirement age to see the projection." : "Fix the ages below to see the projection."}</p>
{/if}
{#if problem}<p class="mt-2 text-sm text-destructive" role="alert">Not saved: {problem}</p>{/if}

<div class="mt-5 grid gap-6 lg:grid-cols-2">
  <section>
    <h3 class="mb-2 font-medium">Who's retiring</h3>
    {#each plan.people as person, i (i)}
      <div class="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <label class="col-span-2 flex flex-col gap-1 sm:col-span-1">{@render field("Name")}
          <Input value={person.name} oninput={(e) => { person.name = e.currentTarget.value; keep(); }} /></label>
        <div class="flex flex-col gap-1">
          <label class="flex flex-col gap-1">{@render field("Retires at", `age`)}
            <Input type="number" step="1" value={person.retire_age} aria-invalid={!!retireErr(person)} aria-describedby={retireErr(person) ? `${uid}-retire-${i}` : undefined}
              oninput={(e) => { person.retire_age = Number(e.currentTarget.value); keep(); }} /></label>
          {#if retireErr(person)}<span id={`${uid}-retire-${i}`} class="text-xs text-destructive">{retireErr(person)}</span>{/if}
        </div>
        <label class="flex flex-col gap-1" title={savingsNote}>{@render field("Saves a year")}
          {@render money(person.savings, (v) => (person.savings = v), `${names[i]}'s yearly savings`)}</label>
      </div>
    {/each}
    {#if plan.people.length === 1}
      <Button variant="outline" size="sm" onclick={addPartner}><Plus /> Add a partner</Button>
    {:else}
      <Button variant="ghost" size="sm" onclick={removePartner}><X /> Remove {names[1]}</Button>
    {/if}
  </section>

  <section>
    <h3 class="mb-2 font-medium">In retirement</h3>
    <label class="flex max-w-60 flex-col gap-1">{@render field("Spending a year", "today’s dollars")}
      {@render money(plan.spending, (v) => { plan.spending = v; plan.spending_own = true; }, "Yearly spending in retirement")}</label>
    {#if plan.spending_own && (ending.length || added.length)}
      <p class="mt-2 text-sm"><button type="button" class="font-medium text-foreground underline underline-offset-4" onclick={useRunwaySpending}>Use Runway's figure</button>
        <span class="text-muted-foreground">({fmt0(data.computed.annual_spending)})</span></p>
    {/if}
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
              <Input type="number" step="1000" min="0" class="pl-6" value={Math.abs(ev.amount)} {@attach commas} oninput={(e) => setEventAmount(i, e.currentTarget.value)} />
            </span></label>
        </div>
        <Button variant="ghost" size="icon" aria-label={`Remove ${ev.name || "event"}`} onclick={() => { plan.events.splice(i, 1); keep(); }}><X /></Button>
      </div>
    {/each}
    <Button variant="outline" size="sm" onclick={addEvent}><Plus /> Add an event</Button>
  </section>

  {#if plannable.length || listed.length}
  <section class="lg:col-span-2">
    <h3 class="mb-2 font-medium">{data.assets.some((a) => a.kind === "loan") ? "Homes, other assets & loans" : "Homes & other assets"}</h3>
    <ul class="space-y-2">
      {#each plannable as a (a.key)}
        {@const s = sale(a.key)}
        <li class="flex flex-wrap items-center gap-x-3 gap-y-1">
          <label class="flex min-w-40 flex-1 items-center gap-2">
            <input type="checkbox" class="size-4 cursor-pointer accent-primary" checked={!!s} onchange={(e) => toggleSale(a.key, e.currentTarget.checked)} />
            <span>{a.name} <span class="text-sm text-muted-foreground">{fmt0(a.value - a.owed)}{a.owed ? " after the loan today" : ""}{vestsTo(a) != null ? ` vested today · ${fmt0(vestsTo(a)!)} once all vested` : ""}</span></span>
          </label>
          {#if s}
            {@const at = saleYear(num(s.sell_year), year)}
            <label class="flex items-center gap-2 text-sm">Sell in
              <Input type="number" step="1" min={year} max={year + 100} class="w-24" value={s.sell_year} aria-invalid={pastYear(s)}
                oninput={(e) => setSellYear(s, e.currentTarget.value)} /></label>
            <!-- A fixed width, right-aligned, so each row's Sell in lines up whatever the amount -->
            <span class="min-w-28 text-right text-sm text-muted-foreground tabular-nums" title={saleTitle(a, at)}>≈ {fmt0(shownIn(saleProceeds(a, at, year, plan.inflation), at))}</span>
            {#if s.was != null}
              <p class="basis-full pl-6 text-xs text-[var(--warning)]">Was {s.was}, now past: counted as sold in {year}.</p>
            {:else if pastYear(s)}
              <p class="basis-full pl-6 text-xs text-destructive">Sell in {year} or later.</p>
            {/if}
          {/if}
          {#if a.loan?.payment}
            <p class="basis-full pl-6 text-sm text-muted-foreground">{paymentLine(a, s ? saleYear(num(s.sell_year), year) : null)}</p>
          {/if}
        </li>
      {/each}
      {#each listed as a (a.key)}
        <li class="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span class="flex min-w-40 flex-1 items-center gap-2 pl-6">{a.name} <span class="text-sm text-muted-foreground">Loan · {fmt0(a.owed)} owed</span></span>
          {#if a.loan?.payment}<p class="basis-full pl-6 text-sm text-muted-foreground">{paymentLine(a, null)}</p>{/if}
        </li>
      {/each}
    </ul>
  </section>
  {/if}

  <details class="group rounded-xl border lg:col-span-2" bind:open={assumptionsOpen}>
    <summary class="flex min-h-14 cursor-pointer list-none items-center gap-3 rounded-xl px-4 py-2 select-none [&::-webkit-details-marker]:hidden">
      <span class="flex min-w-0 flex-1 flex-col">
        <span class="font-medium">Assumptions</span>
        <span class="text-xs text-muted-foreground" data-summary>{assumptionsSummary}</span>
      </span>
      <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
    </summary>
    <div class="@container flex flex-col gap-6 border-t px-4 py-4">
      <section>
        <h3 class="mb-2 font-medium">Birthdays and how long to plan for</h3>
        <div class="grid grid-cols-2 gap-3 @xl:grid-cols-4">
          {#each plan.people as person, i (i)}
            <label class="flex flex-col gap-1">{@render field(plan.people.length > 1 ? `${names[i]} · born in` : "Born in")}
              <Input type="number" step="1" aria-label="Born in" value={person.birth_year} oninput={(e) => { person.birth_year = Number(e.currentTarget.value); keep(); }} /></label>
          {/each}
          <div class="flex flex-col gap-1">
            <label class="flex flex-col gap-1">{@render field("Plan until age")}
              <Input type="number" step="1" value={plan.plan_to_age} aria-invalid={!!planToErr} aria-describedby={planToErr ? `${uid}-planto` : undefined}
                oninput={(e) => { plan.plan_to_age = Number(e.currentTarget.value); keep(); }} /></label>
            {#if planToErr}<span id={`${uid}-planto`} class="text-xs text-destructive">{planToErr}</span>{/if}
          </div>
        </div>
      </section>

      <section>
        <h3 class="mb-2 font-medium">Income in retirement</h3>
        {#each plan.income as inc, i (i)}
          <div class="mb-2 grid grid-cols-[1fr_auto] items-end gap-2">
            <div class="grid grid-cols-2 gap-2 @2xl:grid-cols-5">
              <label class="col-span-2 flex flex-col gap-1 @2xl:col-span-1">{@render field("What")}
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
      </section>

      <section>
        <h3 class="mb-2 font-medium">Returns and inflation</h3>
        <!-- Labels that wrap push their box down, so each label spreads to the row's height and the boxes line up at the bottom -->
        <div class="grid grid-cols-2 gap-3 @3xl:grid-cols-4">
          <label class="flex flex-col justify-between gap-1">{@render field("Return while saving", "after inflation")}
            {@render percent(plan.return_before, (s) => setPct("return_before", s), "Return while saving")}</label>
          <label class="flex flex-col justify-between gap-1">{@render field("Return in retirement", "after inflation")}
            {@render percent(plan.return_after, (s) => setPct("return_after", s), "Return in retirement")}</label>
          <label class="flex flex-col justify-between gap-1">{@render field("Ups and downs", "yearly")}
            {@render percent(plan.volatility, (s) => setPct("volatility", s), "Ups and downs")}</label>
          <label class="flex flex-col justify-between gap-1">{@render field("Inflation", "yearly")}
            {@render percent(plan.inflation, (s) => setPct("inflation", s), "Inflation", `${uid}-inflation`)}</label>
        </div>
        {#if !isDefault}
          <p class="mt-2 text-sm"><ConfirmButton confirm="Start over? This clears everything you entered here." class="h-auto px-1" onconfirm={startOver}>Start over from Runway's figures</ConfirmButton></p>
        {/if}
      </section>
    </div>
  </details>
</div>
{/if}

{#if saved}
  <div class="fixed right-4 bottom-20 z-40 rounded-md bg-card px-3 py-1.5 text-sm text-[var(--good)] shadow-lg ring-1 ring-border lg:bottom-4" role="status"
    style:animation="saved-fade 1.6s ease forwards">Saved ✓</div>
{/if}
