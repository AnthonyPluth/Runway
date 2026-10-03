<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import { cn } from "$lib/utils";
  import type { Snippet } from "svelte";
  import { CHIP_CLASS, type ChipTone } from "./churning";

  // A small label on a row. A status (where something stands) gets a `tone` and is filled with it; an attribute (Business,
  // AU, Card) has no tone and is the same neutral outline everywhere.
  let { tone, class: cls, children, ...rest }: { tone?: ChipTone; class?: string; title?: string; children: Snippet; [key: string]: unknown } = $props();
</script>

{#if tone}
  <Badge class={cn(CHIP_CLASS[tone], cls)} data-chip={tone} {...rest}>{@render children()}</Badge>
{:else}
  <Badge variant="outline" class={cls} data-chip="attribute" {...rest}>{@render children()}</Badge>
{/if}
