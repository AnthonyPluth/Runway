<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState } from "$lib/app.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { barWidth } from "$lib/format";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import Check from "@lucide/svelte/icons/check";
  import { openForecastSettings } from "./forecastSheet.svelte";

  // Getting started: four steps that tick themselves off as you do them. On its own (`welcome`) before a bank is
  // connected; at the top of the Overview after that, until every step is done or you put it away. The main account is
  // chosen in the forecast settings on the Overview itself, so its step opens them there (and waits for a bank before).
  let { welcome = false }: { welcome?: boolean } = $props();
  const s = $derived(app.state?.setup);
  type Step = { done: boolean; title: string; text: string; action: string; href?: string; onclick?: () => void; hint?: string };
  const steps: Step[] = $derived([
    { done: !!s?.bank, title: "Connect a bank", text: "Link your accounts through SimpleFIN or Plaid. The first sync brings in months of history.",
      href: "#setup/connections", action: "Connect" },
    { done: !!s?.primary, title: "Pick your main account", text: "The checking account your paychecks land in and your bills come out of. Runway forecasts its balance.",
      action: "Choose", ...(welcome ? { hint: "after your bank connects" } : { onclick: openForecastSettings }) },
    { done: !!s?.recurring, title: "Add paychecks and bills", text: "Tell Runway what comes in and goes out on a schedule, or accept the ones it spots in your history.",
      href: "#budget/recurring", action: "Add" },
    { done: !!s?.budgets, title: "Set a few budgets", text: "Start with the categories you'd like to keep an eye on, like groceries and restaurants.",
      href: "#budget", action: "Budget" },
  ]);
  const doneCount = $derived(steps.filter((x) => x.done).length);
  const next = $derived(steps.findIndex((x) => !x.done));

  async function dismiss() {
    try { await api("/api/settings", { method: "POST", body: { setup_dismissed: true } }); await refreshState(); }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

<Card.Root class={cn(welcome ? "mx-auto mt-6 max-w-2xl md:mt-12" : "mb-6")}>
  <Card.Header>
    {#if welcome}<img src="/logo.svg" alt="" width="40" height="40" class="mb-2" />{/if}
    <Card.Title class={welcome ? "text-2xl" : ""}>{welcome ? "Welcome to Runway" : "Finish setting up"}</Card.Title>
    <Card.Description>Four steps to see where your cash is headed.</Card.Description>
    {#if !welcome}<Card.Action><Button variant="ghost" size="sm" onclick={dismiss}>Dismiss</Button></Card.Action>{/if}
  </Card.Header>
  <Card.Content>
    <div class="mb-4 flex items-center gap-3">
      <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuemin={0} aria-valuemax={4} aria-valuenow={doneCount} aria-label="Setup progress">
        <div class="h-full rounded-full bg-emerald-500 transition-[width] duration-500" style:width={barWidth(doneCount / 4)}></div>
      </div>
      <span class="text-xs text-muted-foreground tabular-nums">{doneCount} of 4 done</span>
    </div>
    <ol class="flex flex-col">
      {#each steps as step, i (step.title)}
        <li class="flex items-center gap-3.5 border-t py-3.5 first:border-t-0">
          <span class={cn("flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
            step.done ? "bg-emerald-500/15 text-emerald-500" : i === next ? "bg-primary text-primary-foreground" : "border text-muted-foreground")}>
            {#if step.done}<Check class="size-4" aria-label="Done" />{:else}{i + 1}{/if}
          </span>
          <div class="min-w-0 flex-1">
            <div class={cn("text-sm font-medium", step.done && "text-muted-foreground line-through decoration-muted-foreground/50")}>{step.title}</div>
            {#if !step.done}<p class="text-xs text-muted-foreground">{step.text}</p>{/if}
          </div>
          {#if !step.done && step.hint}
            <span class="flex shrink-0 flex-col items-end gap-1">
              <Button size="sm" variant="outline" disabled aria-describedby={`step-${i}-hint`}>{step.action}</Button>
              <span id={`step-${i}-hint`} class="text-[11px] text-muted-foreground">{step.hint}</span>
            </span>
          {:else if !step.done}
            <Button href={step.href} onclick={step.onclick} size="sm" variant={i === next ? "default" : "outline"}>{step.action}</Button>
          {/if}
        </li>
      {/each}
    </ol>
  </Card.Content>
</Card.Root>
