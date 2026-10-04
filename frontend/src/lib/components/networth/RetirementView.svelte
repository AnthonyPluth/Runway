<script lang="ts">
  import { api } from "$lib/api";
  import RetirementPlanner from "$lib/components/investments/RetirementPlanner.svelte";
  import type { InvAccount, PlanData } from "$lib/components/investments/types";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { errMsg } from "$lib/act";

  // The Retirement tab of Net worth. The plan comes with the investments data (it starts from the portfolio's value and
  // history), which works without any investment accounts: the planner then starts from Runway's own figures.
  // `accounts`: how many investment accounts that starting value adds up (the ones Investments counts).
  let plan = $state.raw<PlanData | null>(null), accounts = $state(0);
  let error = $state<string | null>(null);
  async function load() {
    try {
      const r = await api<{ plan: PlanData; accounts?: InvAccount[] }>("/api/investments?period=1Y");
      plan = r.plan; accounts = (r.accounts ?? []).filter((a) => !a.hidden && !a.duplicate_of).length; error = null;
    } catch (err) { error = errMsg(err); }
  }
  load();
</script>

{#if error && !plan}
  <Card.Root>
    <Card.Content>
      <p class="text-sm">Something went wrong: {error}</p>
      <Button class="mt-3" variant="outline" onclick={load}>Try again</Button>
    </Card.Content>
  </Card.Root>
{:else if !plan}
  <div class="h-40 animate-pulse motion-reduce:animate-none rounded-xl bg-muted"></div>
{:else}
  <Card.Root class="mb-6">
    <Card.Header>
      <Card.Title>Retirement planner</Card.Title>
    </Card.Header>
    <Card.Content><RetirementPlanner data={plan} {accounts} /></Card.Content>
  </Card.Root>
{/if}
