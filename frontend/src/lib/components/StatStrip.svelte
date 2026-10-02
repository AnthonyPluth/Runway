<script lang="ts" module>
  export type StatTone = "bad" | "warn" | "good";
  export type Stat = { label: string; value: string; sub?: string; tone?: StatTone; subTone?: StatTone };
</script>

<script lang="ts">
  import { cn } from "$lib/utils";

  // A row of figures in tiles: the value big, its label and an optional note under it. Compact tiles in a row from md up, a two-column
  // grid on phones. A tone colors the value, tints the tile's edge and puts a dot before the label, so a warning stands
  // out. `subTone` colors just the note (a gain or loss under a figure).
  let { items, class: className }: { items: Stat[]; class?: string } = $props();
  const text: Record<StatTone, string> = { bad: "text-destructive", warn: "text-[var(--warning)]", good: "text-emerald-400" };
  const edge: Record<StatTone, string> = { bad: "border-destructive/40", warn: "border-[var(--warning)]/40", good: "border-[var(--edge)]" };
</script>

<dl class={cn("grid grid-cols-2 gap-3 md:grid-cols-[repeat(auto-fill,minmax(12rem,15.5rem))]", className)}>
  {#each items as it (it.label)}
    <div class={cn("flex min-w-0 flex-col rounded-[14px] border bg-card px-4 py-3.5", it.tone ? edge[it.tone] : "border-[var(--edge)]")} data-tone={it.tone}>
      <dt class="order-2 mt-1 flex items-center gap-1.5 text-[13px] leading-snug text-muted-foreground">
        {#if it.tone && it.tone !== "good"}<span class={cn("size-1.5 shrink-0 rounded-full bg-current", text[it.tone])} aria-hidden="true"></span>{/if}
        <span class="min-w-0">{it.label}</span>
      </dt>
      <dd class={cn("order-1 text-[26px] leading-tight font-bold tracking-[-0.03em] tabular-nums", it.tone && text[it.tone])}>{it.value}</dd>
      {#if it.sub}<dd class={cn("order-3 mt-0.5 text-[13px] tabular-nums", it.subTone ? text[it.subTone] : "text-muted-foreground")}>{it.sub}</dd>{/if}
    </div>
  {/each}
</dl>
