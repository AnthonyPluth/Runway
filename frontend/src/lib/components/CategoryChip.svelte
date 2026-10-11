<script module lang="ts">
  import { cn } from "$lib/utils";

  /** The class for a CategorySelect button that wears a CategoryChip. At phone width an invisible hit area grows the
   * tap target to 44px (the pill is 28px, or 24px in a receipt); the focus ring goes round the pill. */
  export const chipButton = (size: "row" | "receipt" = "row", extra?: string | false) => cn(
    "group/chip relative inline-flex min-w-0 max-w-full rounded-full text-left focus-visible:ring-2 focus-visible:ring-ring",
    "phone:before:absolute phone:before:inset-x-0 phone:before:content-['']",
    size === "receipt" ? "phone:before:-inset-y-2.5" : "phone:before:-inset-y-2", extra);
</script>

<script lang="ts">
  import { categories, catLook } from "$lib/categories.svelte";
  import type { Snippet } from "svelte";

  // What a category looks like on a row or a receipt line: a pill with its emoji first, then its own name (never
  // "Parent > Child"; the full path is the tooltip). No chevron: the filled pill says it can be tapped. Nothing chosen
  // is a dashed amber "Choose category". Put it inside a CategorySelect (whose button takes `chipButton`).
  // `empty`: what the pill says with nothing chosen; `compactEmpty` is shorter, for a narrow container named `cat`.
  // `text`: what to write instead of the name (a split's several categories), no emoji. `lead`: something before it.
  let { category = "", size = "row", empty = "Choose category", compactEmpty, text, lead }: {
    category?: string | null; size?: "row" | "receipt"; empty?: string; compactEmpty?: string; text?: string; lead?: Snippet;
  } = $props();

  const name = $derived(text ?? category ?? "");
  const full = $derived(text ?? (category ? (categories.list.find((c) => c.name === category)?.path ?? [category]).join(" > ") : ""));
</script>

<span class={cn("inline-flex min-w-0 max-w-full items-center gap-[5px] rounded-full border px-2.5 text-[13px] leading-none transition-colors",
  size === "receipt" ? "h-6" : "h-7",
  name ? "border-border bg-muted text-foreground group-hover/chip:border-muted-foreground/40 dark:border-transparent dark:group-hover/chip:border-muted-foreground/40"
    : "border-dashed border-warning/60 text-warning group-hover/chip:bg-warning/10")}>
  {@render lead?.()}
  {#if name}
    {#if !text}<span aria-hidden="true" class="shrink-0 text-[14px] leading-none">{catLook(category).icon}</span>{/if}
    <span class="truncate" title={full || undefined}>{name}</span>
  {:else if compactEmpty}
    <span class="truncate"><span class="@[12rem]/cat:hidden">{compactEmpty}</span><span class="hidden @[12rem]/cat:inline">{empty}</span></span>
  {:else}
    <span class="truncate">{empty}</span>
  {/if}
</span>
