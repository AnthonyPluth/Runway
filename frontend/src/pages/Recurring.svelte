<script lang="ts">
  import { app } from "$lib/app.svelte";
  import RecurringView from "$lib/components/recurring/RecurringView.svelte";
  import NotConnected from "$lib/components/NotConnected.svelte";

  // Recurring: the paychecks and bills behind the forecast (#recurring; the old #budget/recurring opens it too).
</script>

<div class="mb-4 flex items-center gap-1">
  <h1 class="text-[34px] leading-[1.05] font-extrabold tracking-[-0.035em]">Recurring</h1>
</div>
{#if !app.state?.connected}
  <NotConnected title="Connect a bank to track your bills and income"
    text="Runway spots the paychecks and bills in your history and forecasts your balance from them. The first sync brings in months of history." />
{:else}<RecurringView />{/if}
