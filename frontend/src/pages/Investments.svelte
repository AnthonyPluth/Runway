<script lang="ts">
  import InvestmentsView from "$lib/components/investments/InvestmentsView.svelte";

  // Investments as a page of its own (#investments); Net worth's Investments tab (#networth/investments) shows the same view.
</script>

<div class="mb-4 flex items-center gap-1">
  <h1 class="text-[34px] leading-[1.05] font-extrabold tracking-[-0.035em]">Investments</h1>
</div>
<InvestmentsView />
