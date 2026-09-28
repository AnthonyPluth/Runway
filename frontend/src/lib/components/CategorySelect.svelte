<script lang="ts">
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { categories, catLabel, categoryGroups } from "$lib/categories.svelte";
  import type { Category } from "$lib/types";
  import { cn } from "$lib/utils";
  import type { Snippet } from "svelte";
  import ChevronDown from "@lucide/svelte/icons/chevron-down";

  // Pick a category, grouped as Spending / Money in / Not spending. Call loadCategories() before showing it.
  // `blank` adds a first "Choose…" option (value ""); `first` lets you put your own options before the groups.
  // `short`: closed, it shows just the category ("Public Transit"), not its path ("Travel > Public Transit"); the open
  // list keeps the paths, which tell apart subcategories of the same name.
  let { value = $bindable(""), blank = "Choose…", canHoldChildren = false, exclude, ghost = false, short = false,
    label = "Category", class: className, disabled = false, onchange, first }: {
    value?: string; blank?: string | false; canHoldChildren?: boolean; exclude?: (c: Category) => boolean; ghost?: boolean;
    short?: boolean; label?: string; class?: string; disabled?: boolean; onchange?: (value: string) => void; first?: Snippet;
  } = $props();
  const groups = $derived(categoryGroups({ canHoldChildren, exclude }));
  // What the closed select shows in short form: the category's own name (or the blank option's text). A value that
  // isn't a category (one of `first`'s options) shows as the select itself would.
  const shown = $derived(!short ? null : value === "" ? (blank === false ? null : blank)
    : categories.list.some((c) => c.name === value) ? value : null);
</script>

{#if shown === null}
  <NativeSelect bind:value aria-label={label} {ghost} {disabled} class={className} onchange={() => onchange?.(value)}>
    {@render options()}
  </NativeSelect>
{:else}
  <!-- The select's own text (and with it the browser's arrow) is hidden and the short name and an arrow drawn over
       it; the select still gets every click and key, and a screen reader still hears the full option. -->
  <span class={cn("relative inline-flex min-w-0", className)}>
    <NativeSelect bind:value aria-label={label} {ghost} {disabled} onchange={() => onchange?.(value)}
      class="w-full appearance-none text-transparent [&_optgroup]:text-popover-foreground [&_option]:text-popover-foreground">
      {@render options()}
    </NativeSelect>
    <span aria-hidden="true" class={cn("pointer-events-none absolute inset-y-0 left-2.5 right-8 flex items-center truncate text-sm",
      value === "" && "text-muted-foreground", disabled && "opacity-50")}>{shown}</span>
    <ChevronDown aria-hidden="true" class={cn("pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2", disabled && "opacity-50")} />
  </span>
{/if}

{#snippet options()}
  {#if blank !== false}<option value="">{blank}</option>{/if}
  {@render first?.()}
  {#each groups as g (g.label)}
    <optgroup label={g.label}>
      {#each g.items as c (c.name)}<option value={c.name}>{c.icon ? `${c.icon}  ` : ""}{catLabel(c)}</option>{/each}
    </optgroup>
  {/each}
{/snippet}
