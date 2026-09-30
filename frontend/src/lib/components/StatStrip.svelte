<script lang="ts" module>
  export type StatTone = "bad" | "warn" | "good";
  export type Stat = { label: string; value: string; sub?: string; tone?: StatTone; subTone?: StatTone };
</script>

<script lang="ts">
  import { cn } from "$lib/utils";

  // A row of figures with no boxes: label over value over an optional note, thin dividers between them. One row from
  // md up, a two-column grid on phones. A tone colors the value and puts a dot before the label, so a warning stays
  // visible without a ring around a tile. `subTone` colors just the note (a gain or loss under a figure).
  let { items, class: className }: { items: Stat[]; class?: string } = $props();
  const text: Record<StatTone, string> = { bad: "text-destructive", warn: "text-[var(--warning)]", good: "text-emerald-400" };
</script>

<dl class={cn("grid grid-cols-2 gap-x-6 gap-y-5 md:flex md:gap-x-0", className)}>
  {#each items as it (it.label)}
    <div class="min-w-0 md:flex-1 md:border-l md:border-border md:px-6 md:first:border-l-0 md:first:pl-0 md:last:pr-0" data-tone={it.tone}>
      <dt class="flex items-center gap-1.5 text-[13px] text-muted-foreground">
        {#if it.tone && it.tone !== "good"}<span class={cn("size-1.5 shrink-0 rounded-full bg-current", text[it.tone])} aria-hidden="true"></span>{/if}
        <span class="min-w-0">{it.label}</span>
      </dt>
      <dd class={cn("mt-0.5 text-xl font-semibold tabular-nums", it.tone && text[it.tone])}>{it.value}</dd>
      {#if it.sub}<dd class={cn("mt-0.5 text-[13px] tabular-nums", it.subTone ? text[it.subTone] : "text-muted-foreground")}>{it.sub}</dd>{/if}
    </div>
  {/each}
</dl>
