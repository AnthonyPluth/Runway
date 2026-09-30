<script lang="ts">
  import { api } from "$lib/api";
  import RetirementPlanner from "$lib/components/investments/RetirementPlanner.svelte";
  import type { PlanData } from "$lib/components/investments/types";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";

  // The Retirement tab of Net worth. The plan comes with the investments data (it starts from the portfolio's value and
  // history), which works without any investment accounts: the planner then starts from Runway's own figures.
  let plan = $state.raw<PlanData | null>(null);
  let error = $state<string | null>(null);
  async function load() {
    try { plan = (await api<{ plan: PlanData }>("/api/investments?period=1Y")).plan; error = null; }
    catch (err) { error = (err as Error).message; }
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
  <div class="h-40 animate-pulse rounded-xl bg-muted"></div>
{:else}
  <Card.Root class="mb-6">
    <Card.Header>
      <Card.Title>Retirement planner</Card.Title>
      <Card.Description>What retirement looks like for you: your plan run through 1,000 possible markets, in today's dollars.</Card.Description>
    </Card.Header>
    <Card.Content><RetirementPlanner data={plan} /></Card.Content>
  </Card.Root>
{/if}
