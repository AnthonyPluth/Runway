<script lang="ts">
  import { NativeSelect } from "$lib/components/ui/native-select";
  import type { HTMLSelectAttributes } from "svelte/elements";
  import { currencyGroups } from "./churning";
  import type { Churning } from "./types";

  // What a card earns (or a balance is in): the currencies grouped as bank points, airlines, hotels and cash, since
  // an AAdvantage mile isn't a United mile. `only` limits it to some keys; `blank` adds a first empty option.
  let { value = $bindable(""), d, only, blank, ...rest }:
    { value?: string; d: Pick<Churning, "currencies" | "currency_groups">; only?: (key: string) => boolean; blank?: string } & Omit<HTMLSelectAttributes, "value"> = $props();
  const groups = $derived(currencyGroups(d).map((g) => ({ ...g, currencies: g.currencies.filter((c) => !only || only(c.key)) })).filter((g) => g.currencies.length));
</script>

<NativeSelect bind:value {...rest}>
  {#if blank !== undefined}<option value="">{blank}</option>{/if}
  {#each groups as g (g.kind)}
    <optgroup label={g.label}>{#each g.currencies as c (c.key)}<option value={c.key}>{c.name}</option>{/each}</optgroup>
  {/each}
</NativeSelect>
