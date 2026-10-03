<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState } from "$lib/app.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { barWidth, plural } from "$lib/format";
  import { undoable } from "$lib/undo";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import Check from "@lucide/svelte/icons/check";
  import { openForecastSettings } from "./forecastSheet.svelte";

  // Getting started: four steps that tick themselves off as you do them, each with a short line on what it means. On its
  // own (`welcome`) before a bank is connected; at the top of the Overview after that, until every step is done or you
  // put it away (with Undo). Until there's an account the later steps can't be done, so they wait, greyed. The forecast
  // account is chosen in the forecast settings on the Overview itself, so its step opens them there. Accounts Plaid found
  // that wait for a decision come first: those steps point at Settings → Accounts, where they're added.
  let { welcome = false }: { welcome?: boolean } = $props();
  const s = $derived(app.state?.setup);
  const undecided = $derived(app.state?.plaid_undecided ?? 0);
  const locked = $derived(!s?.bank);
  const review = (title: string, text: string) => ({ done: false, title, text, action: "Review", href: "#setup/accounts" });
  type Step = { done: boolean; title: string; text: string; action: string; href?: string; onclick?: () => void; waits?: boolean };
  const steps: Step[] = $derived([
    !s?.bank && undecided && !welcome
      ? review("Connect a bank", `Add the ${plural(undecided, "account")} Plaid found in Settings → Accounts`)
      : { done: !!s?.bank, title: "Connect a bank", text: "SimpleFIN or Plaid, with months of history",
        href: "#setup/connections", action: "Connect" },
    s?.bank && undecided && !s?.primary
      ? review("Choose your forecast account", `First add the ${plural(undecided, "new account")} from Plaid in Settings → Accounts`)
      : { done: !!s?.primary, title: "Choose your forecast account", text: "Where your pay lands and bills come out",
        action: "Choose", ...(locked ? { waits: true } : { onclick: openForecastSettings }) },
    { done: !!s?.recurring, title: "Add paychecks and bills", text: "Or accept the ones Runway spots",
      href: "#recurring", action: "Add", waits: locked },
    { done: !!s?.budgets, title: "Set a few budgets", text: "Groceries and dining are a good start",
      href: "#budget", action: "Budget", waits: locked },
  ]);
  const doneCount = $derived(steps.filter((x) => x.done).length);
  const next = $derived(steps.findIndex((x) => !x.done));

  const setDismissed = async (on: boolean) => {
    await api("/api/settings", { method: "POST", body: { setup_dismissed: on } });
    await refreshState();
  };
  async function dismiss() {
    try { await setDismissed(true); }
    catch (err) { toast.error((err as Error).message); return; }
    undoable("Setup checklist dismissed", () => setDismissed(false));
  }
</script>

<Card.Root class={cn(welcome ? "mx-auto mt-6 max-w-2xl md:mt-12" : "mb-6")}>
  <Card.Header>
    {#if welcome}<img src="/logo.svg" alt="" width="40" height="40" class="mb-2" />{/if}
    <Card.Title class={welcome ? "text-2xl" : ""}>{welcome ? "Welcome to Runway" : "Finish setting up"}</Card.Title>
    {#if !welcome}<Card.Action><Button variant="ghost" size="sm" onclick={dismiss}>Dismiss</Button></Card.Action>{/if}
  </Card.Header>
  <Card.Content>
    <div class="mb-4 flex items-center gap-3">
      <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuemin={0} aria-valuemax={4} aria-valuenow={doneCount} aria-label="Setup progress">
        <div class="h-full rounded-full bg-good transition-[width] duration-500" style:width={barWidth(doneCount / 4)}></div>
      </div>
      <span class="text-xs text-muted-foreground tabular-nums">{doneCount} of 4 done</span>
    </div>
    {#if locked}<span id="setup-waits" class="sr-only">{app.state?.connected ? "after you add an account" : "after your bank connects"}</span>{/if}
    <ol class="flex flex-col">
      {#each steps as step, i (i)}
        <li class={cn("flex items-center gap-3.5 border-t py-3.5 first:border-t-0", step.waits && !step.done && "opacity-50")}>
          <span class={cn("flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
            step.done ? "bg-good/15 text-good" : i === next ? "bg-primary text-primary-foreground" : "border text-muted-foreground")}>
            {#if step.done}<Check class="size-4" aria-label="Done" />{:else}{i + 1}{/if}
          </span>
          <div class="min-w-0 flex-1">
            <div class={cn("text-sm font-medium", step.done && "text-muted-foreground line-through decoration-muted-foreground/50")}>{step.title}</div>
            {#if !step.done}<div class="text-xs text-muted-foreground">{step.text}</div>{/if}
          </div>
          {#if !step.done && step.waits}
            <Button size="sm" class="h-10 shrink-0 sm:h-8" variant="outline" disabled aria-describedby="setup-waits">{step.action}</Button>
          {:else if !step.done}
            <Button href={step.href} onclick={step.onclick} size="sm" class="h-10 shrink-0 sm:h-8" variant={i === next ? "default" : "outline"}>{step.action}</Button>
          {/if}
        </li>
      {/each}
    </ol>
  </Card.Content>
</Card.Root>
