<script lang="ts">
  import BenefitDrafts from "./BenefitDrafts.svelte";
  import RatesEditor from "./RatesEditor.svelte";
  import type { RateRow } from "./churning";
  import type { Churning, DraftBenefit } from "./types";

  // The lists a card form edits, held in state in a parent as the form does (so the rows are the form's own objects).
  let { d, which }: { d: Churning; which: "benefits" | "rates" } = $props();
  let benefits = $state<DraftBenefit[]>([{ name: "Lyft credit", kind: "credit", amount: null, period: "annual" }, { name: "Lounge access", kind: "access", amount: null, period: "annual" }]);
  let rates = $state<RateRow[]>([{ category: "Travel", multiplier: 5, portal_only: false }, { category: "Restaurants", multiplier: 3, portal_only: false }]);
  let base = $state("1"), portalName = $state("");
</script>

{#if which === "benefits"}<BenefitDrafts {d} bind:rows={benefits} />{:else}<RatesEditor {d} bind:base bind:rows={rates} bind:portalName />{/if}
