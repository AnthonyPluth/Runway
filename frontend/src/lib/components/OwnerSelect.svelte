<script lang="ts">
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { ownerChoices } from "$lib/owners";
  import type { HTMLSelectAttributes } from "svelte/elements";

  // Whose it is, as a dropdown of the people (the same list Settings offers for an account's owner). `blank` adds a
  // first option with no owner; `joint` a last one for shared accounts. The current value stays a choice even if that
  // person is no longer in the list.
  let { value = $bindable(""), owners, joint = false, blank, ...rest }:
    { value?: string; owners: string[]; joint?: boolean; blank?: string } & Omit<HTMLSelectAttributes, "value"> = $props();
  const names = $derived(ownerChoices(owners, value, joint));
</script>

<NativeSelect bind:value {...rest}>
  {#if blank !== undefined}<option value="">{blank}</option>{/if}
  {#each names as n (n)}<option value={n}>{n}</option>{/each}
</NativeSelect>
