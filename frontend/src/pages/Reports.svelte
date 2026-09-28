<script lang="ts">
  import SubTabs from "$lib/components/SubTabs.svelte";
  import Breakdown from "$lib/components/reports/Breakdown.svelte";
  import Cashflow from "$lib/components/reports/Cashflow.svelte";
  import Income from "$lib/components/reports/Income.svelte";
  import Merchants from "$lib/components/reports/Merchants.svelte";
  import Trends from "$lib/components/reports/Trends.svelte";

  // Reports: five views of where the money went, each a route of its own (#reports/trends). Cash flow is the default.
  let { sub = "" }: { page?: string; sub?: string } = $props();
  const TABS = [
    { id: "cashflow", label: "Cash flow", view: Cashflow },
    { id: "trends", label: "Over time", view: Trends },
    { id: "merchants", label: "Merchants", view: Merchants },
    { id: "income", label: "Income vs spending", view: Income },
    { id: "breakdown", label: "Breakdown", view: Breakdown },
  ];
  const tab = $derived(TABS.find((t) => t.id === sub) ?? TABS[0]);
</script>

<h1 class="mb-6 text-3xl font-semibold tracking-tight">Reports</h1>
<SubTabs label="Reports" current={tab.id} tabs={TABS.map((t) => ({ id: t.id, label: t.label, href: `#reports/${t.id}` }))} />
<tab.view />
