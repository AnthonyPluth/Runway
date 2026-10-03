<script lang="ts" module>
  export type StatTone = "bad" | "warn" | "good" | "up" | "down";
  export type Stat = { label: string; value: string; sub?: string; tone?: StatTone; subTone?: StatTone };
</script>

<script lang="ts">
  import { cn } from "$lib/utils";

  // A row of figures in tiles: the value big, its label and an optional note under it. Compact tiles in a row from md up, a two-column
  // grid on phones. A tone colors the value; `bad` and `warn` also tint the tile's edge and put a dot before
  // the label, so a warning stands out. `up` and `down` only color it green or coral, for a figure that's simply a gain
  // or a loss. `subTone` colors just the note (a gain or loss under a figure).
  let { items, class: className }: { items: Stat[]; class?: string } = $props();
  const text: Record<StatTone, string> = { bad: "text-destructive", warn: "text-[var(--warning)]", good: "text-good", up: "text-good", down: "text-loss" };
  const edge: Partial<Record<StatTone, string>> = { bad: "border-destructive/40", warn: "border-[var(--warning)]/40" };
  const dot = (t: StatTone | undefined) => t === "bad" || t === "warn";
</script>

<dl class={cn("grid grid-cols-2 gap-3 md:grid-cols-[repeat(auto-fill,minmax(12rem,15.5rem))]", className)}>
  {#each items as it (it.label)}
    <div class={cn("flex min-w-0 flex-col rounded-[14px] border bg-card px-4 py-3.5", (it.tone && edge[it.tone]) || "border-[var(--edge)]")} data-tone={it.tone}>
      <dt class="order-2 mt-1 flex items-center gap-1.5 text-[13px] leading-snug text-muted-foreground">
        {#if it.tone && dot(it.tone)}<span class={cn("size-1.5 shrink-0 rounded-full bg-current", text[it.tone])} aria-hidden="true"></span>{/if}
        <span class="min-w-0">{it.label}</span>
      </dt>
      <dd class={cn("order-1 text-xl leading-tight break-words md:text-[26px] font-bold tracking-[-0.03em] tabular-nums", it.tone && text[it.tone])}>{it.value}</dd>
      {#if it.sub}<dd class={cn("order-3 mt-0.5 text-[13px] tabular-nums", it.subTone ? text[it.subTone] : "text-muted-foreground")}>{it.sub}</dd>{/if}
    </div>
  {/each}
</dl>
