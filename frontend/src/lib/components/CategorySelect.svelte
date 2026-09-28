<script lang="ts">
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { catLabel, categoryGroups } from "$lib/categories.svelte";
  import type { Category } from "$lib/types";
  import type { Snippet } from "svelte";

  // Pick a category, grouped as Spending / Money in / Not spending. Call loadCategories() before showing it.
  // `blank` adds a first "Choose…" option (value ""); `first` lets you put your own options before the groups.
  let { value = $bindable(""), blank = "Choose…", canHoldChildren = false, exclude, ghost = false, label = "Category",
    class: className, disabled = false, onchange, first }: {
    value?: string; blank?: string | false; canHoldChildren?: boolean; exclude?: (c: Category) => boolean; ghost?: boolean;
    label?: string; class?: string; disabled?: boolean; onchange?: (value: string) => void; first?: Snippet;
  } = $props();
  const groups = $derived(categoryGroups({ canHoldChildren, exclude }));
</script>

<NativeSelect bind:value aria-label={label} {ghost} {disabled} class={className} onchange={() => onchange?.(value)}>
  {#if blank !== false}<option value="">{blank}</option>{/if}
  {@render first?.()}
  {#each groups as g (g.label)}
    <optgroup label={g.label}>
      {#each g.items as c (c.name)}<option value={c.name}>{catLabel(c)}</option>{/each}
    </optgroup>
  {/each}
</NativeSelect>
