<script lang="ts">
  import { api } from "$lib/api";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { fmt, fmt0, shortMoney } from "$lib/format";
  import { toast } from "svelte-sonner";
  import LineChart from "./LineChart.svelte";
  import type { Fire, FireFigures } from "./types";

  // Financial independence: when your investments reach 25× your yearly spending (the 4% rule), with the four
  // assumptions editable. Anything you type is kept, so the projection is the same when you come back.
  let { fire }: { fire: Fire } = $props();

  // The four assumptions, and how each is typed: percentages as percent.
  const FIELDS: { key: keyof FireFigures; label: string; pct: boolean; step: number }[] = [
    { key: "annual_spending", label: "Yearly spending", pct: false, step: 1000 },
    { key: "yearly_savings", label: "Saved per year", pct: false, step: 1000 },
    { key: "expected_return", label: "Return after inflation", pct: true, step: 0.5 },
    { key: "withdrawal_rate", label: "Withdrawal rate", pct: true, step: 0.25 },
  ];
  const shown = (k: keyof FireFigures, v: number) => (FIELDS.find((f) => f.key === k)!.pct ? String(+(v * 100).toFixed(2)) : String(Math.round(v)));
  const initial = () => Object.fromEntries(FIELDS.map((f) => [f.key, shown(f.key, fire[f.key])])) as Record<keyof FireFigures, string>;
  let typed = $state(initial());
  const savedAtFirst = () => [...fire.saved];
  let saved = $state(savedAtFirst());

  // Typing a figure keeps it (a moment after you stop typing).
  let saveTimer: ReturnType<typeof setTimeout>;
  function keep() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      const body = Object.fromEntries(FIELDS.map((f) => {
        const n = Number(typed[f.key]);
        const v = isFinite(n) && typed[f.key] !== "" ? (f.pct ? n / 100 : n) : null;
        // A figure that matches Runway's own isn't an override, so that one keeps following your accounts.
        const same = v != null && Math.abs(v - fire.computed[f.key]) < (f.pct ? 1e-6 : 0.5);
        return [f.key, same ? null : v];
      }));
      try { saved = (await api<{ saved: string[] }>("/api/investments/fire", { method: "POST", body })).saved; }
      catch (err) { toast.error((err as Error).message); }
    }, 600);
  }
  async function reset() {
    clearTimeout(saveTimer);
    try { await api("/api/investments/fire", { method: "POST", body: Object.fromEntries(FIELDS.map((f) => [f.key, null])) }); }
    catch (err) { toast.error((err as Error).message); return; }
    typed = Object.fromEntries(FIELDS.map((f) => [f.key, shown(f.key, fire.computed[f.key])])) as Record<keyof FireFigures, string>;
    saved = [];
    toast("Back to Runway's own figures");
  }

  // Grow what you have by the return and add a year's savings, until it reaches the target (or 60 years pass).
  const calc = $derived.by(() => {
    const spend = Number(typed.annual_spending) || 0, save = Number(typed.yearly_savings) || 0;
    const r = (Number(typed.expected_return) || 0) / 100, wr = (Number(typed.withdrawal_rate) || 4) / 100;
    const target = wr > 0 ? spend / wr : 0;
    let v = fire.current, years = 0;
    const path = [v];
    while (v < target && years < 60) { v = v * (1 + r) + save; years += 1; path.push(v); }
    return { target, years, path, reached: v >= target };
  });
  const year = new Date().getFullYear();
</script>

<p class="text-sm text-muted-foreground">Target: 25× yearly spending (the 4% rule). Anything you change here is kept.</p>
<div class="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
  {#each FIELDS as f (f.key)}
    <label class="flex flex-col gap-1 text-sm">{f.label}
      <span class="relative">
        <span class={`pointer-events-none absolute top-1/2 -translate-y-1/2 text-muted-foreground ${f.pct ? "right-2.5" : "left-2.5"}`} aria-hidden="true">{f.pct ? "%" : "$"}</span>
        <Input type="number" step={f.step} class={f.pct ? "pr-7" : "pl-6"} value={typed[f.key]} oninput={(e) => { typed[f.key] = e.currentTarget.value; keep(); }} />
      </span>
    </label>
  {/each}
</div>
{#if saved.length}
  <p class="mt-2 text-sm text-muted-foreground">Using your own figures.
    <Button variant="link" size="sm" class="h-auto px-1" onclick={reset}>Go back to Runway's</Button></p>
{/if}
<div class="mt-4 flex flex-wrap gap-7">
  <div><div class="text-sm text-muted-foreground">Target</div><div class="text-xl font-semibold tabular-nums">{fmt0(calc.target)}</div></div>
  <div><div class="text-sm text-muted-foreground">You have</div><div class="text-xl font-semibold tabular-nums">{fmt0(fire.current)}</div>
    <div class="text-sm text-muted-foreground">{calc.target ? ((fire.current / calc.target) * 100).toFixed(0) : 0}% of the way</div></div>
  <div><div class="text-sm text-muted-foreground">{calc.reached ? "Reached in about" : "Not reached within"}</div>
    <div class="text-xl font-semibold tabular-nums">{calc.reached ? `${calc.years} yr${calc.years === 1 ? "" : "s"}` : "60 yrs"}</div>
    <div class="text-sm text-muted-foreground">{calc.reached && calc.years ? `around ${year + calc.years}` : calc.reached ? "already there" : "try saving more"}</div></div>
</div>
<div class="mt-2">
  <LineChart xs={calc.path.map((_, i) => `${year + i}`)} labels height={170} fmtY={shortMoney} fmtTip={fmt} series={[
    { name: "Projected", values: calc.path, cls: "s-main", area: true },
    { name: "Target", values: calc.path.map(() => calc.target), cls: "s-muted" },
  ]} />
</div>
