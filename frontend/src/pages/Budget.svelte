<script lang="ts">
  import { app } from "$lib/app.svelte";
  import BudgetView from "$lib/components/budget/BudgetView.svelte";
  import NotConnected from "$lib/components/NotConnected.svelte";

  // Budget: the month's budgets. (Recurring paychecks and bills are their own page, #recurring.)
</script>

<div class="mb-4 flex items-center gap-1">
  <h1 class="text-[34px] leading-[1.05] font-extrabold tracking-[-0.035em]">Budget</h1>
</div>
{#if !app.state?.connected}
  <NotConnected title="Connect a bank to set a budget"
    text="Budgets track what you spend in each category, from your transactions. The first sync brings in months of history." />
{:else}<BudgetView />{/if}
