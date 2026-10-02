<script lang="ts">
  import { app } from "$lib/app.svelte";
  import BudgetView from "$lib/components/budget/BudgetView.svelte";
  import RecurringView from "$lib/components/recurring/RecurringView.svelte";
  import NotConnected from "$lib/components/NotConnected.svelte";
  import SubTabs from "$lib/components/SubTabs.svelte";

  // Budget: the month's budgets, and the recurring paychecks and bills behind the forecast (#budget/recurring).
  let { sub = "" }: { page?: string; sub?: string } = $props();
  const TABS = [
    { id: "budget", label: "Budget", href: "#budget" },
    { id: "recurring", label: "Bills & income", href: "#budget/recurring" },
  ];
  const tab = $derived(sub === "recurring" ? "recurring" : "budget");
</script>

<h1 class="mb-4 text-[34px] leading-tight font-bold tracking-tight">Budget</h1>
<SubTabs label="Budget" current={tab} tabs={TABS} />
{#if !app.state?.connected}
  <NotConnected title={tab === "recurring" ? "Connect a bank to track your bills and income" : "Connect a bank to set a budget"}
    text={tab === "recurring" ? "Runway spots the paychecks and bills in your history and forecasts your balance from them. The first sync brings in months of history."
      : "Budgets track what you spend in each category, from your transactions. The first sync brings in months of history."} />
{:else if tab === "recurring"}<RecurringView />{:else}<BudgetView />{/if}
