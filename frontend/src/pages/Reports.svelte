<script lang="ts">
  import { app } from "$lib/app.svelte";
  import DesktopOnly from "$lib/components/DesktopOnly.svelte";
  import NotConnected from "$lib/components/NotConnected.svelte";
  import SubTabs from "$lib/components/SubTabs.svelte";
  import Breakdown from "$lib/components/reports/Breakdown.svelte";
  import Cashflow from "$lib/components/reports/Cashflow.svelte";
  import Income from "$lib/components/reports/Income.svelte";
  import Merchants from "$lib/components/reports/Merchants.svelte";
  import Trends from "$lib/components/reports/Trends.svelte";
  import { isPhone } from "$lib/phone.svelte";

  // Reports: five views of where the money went, each a route of its own (#reports/trends). Cash flow is the default, and
  // on a phone the only one: the rest are for a computer.
  let { sub = "" }: { page?: string; sub?: string } = $props();
  const TABS = [
    { id: "cashflow", label: "Cash flow", view: Cashflow },
    { id: "trends", label: "Over time", view: Trends },
    { id: "merchants", label: "Merchants", view: Merchants },
    { id: "income", label: "Income vs spending", view: Income },
    { id: "breakdown", label: "Breakdown", view: Breakdown },
  ];
  const tab = $derived(isPhone() ? TABS[0] : (TABS.find((t) => t.id === sub) ?? TABS[0]));
</script>

<h1 class="mb-6 text-[34px] leading-tight font-bold tracking-tight">Reports</h1>
{#if !isPhone()}<SubTabs label="Reports" current={tab.id} tabs={TABS.map((t) => ({ id: t.id, label: t.label, href: `#reports/${t.id}` }))} />{/if}
{#if app.state?.connected}
  <tab.view />
  {#if isPhone()}<DesktopOnly class="mt-6" what="see spending over time, merchants, income against spending and the breakdown" />{/if}
{:else}
  <NotConnected title="Connect a bank to see where your money goes"
    text="Reports are built from your transactions: cash flow, trends, merchants and more. The first sync brings in months of history." />
{/if}
