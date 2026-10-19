<script lang="ts">
  import HoldingDetail from "$lib/components/investments/HoldingDetail.svelte";
  import InvestmentsView from "$lib/components/investments/InvestmentsView.svelte";

  // Investments (#investments); #investments/holding/<security id> is one holding's page. (The old #networth/investments
  // opens here: lib/app.svelte.ts.)
  let { sub = "" }: { sub?: string } = $props();
  const holdingId = $derived.by(() => {
    if (!sub.startsWith("holding/")) return null;
    try { return decodeURIComponent(sub.slice("holding/".length)) || null; } catch { return null; /* a mangled address: the list */ }
  });
</script>

{#if holdingId}
  {#key holdingId}<HoldingDetail id={holdingId} />{/key}
{:else}
  <div class="mb-4 flex items-center gap-1">
    <h1 class="text-[34px] leading-[1.05] font-extrabold tracking-[-0.035em]">Investments</h1>
  </div>
  <InvestmentsView />
{/if}
