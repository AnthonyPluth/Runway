<script lang="ts" module>
  /** The month `n` months from `month` (YYYY-MM). */
  export function shiftMonth(month: string, n: number): string {
    const [y, m] = month.split("-").map(Number);
    const d = new Date(y, m - 1 + n, 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  }
</script>

<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { monthLabel } from "$lib/format";
  import { cn } from "$lib/utils";
  import ChevronLeft from "@lucide/svelte/icons/chevron-left";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";

  // One month with arrows either side, for Budget and Cash flow. Styled like the segmented controls next to it; the
  // arrows are 44px on a phone, a size a finger can hit.
  let { month, onchange, class: className }: { month: string; onchange: (month: string) => void; class?: string } = $props();
</script>

<div class={cn("inline-flex h-9 items-center rounded-lg bg-muted p-[3px] phone:h-auto", className)}>
  <Button variant="ghost" size="icon" class="size-8 phone:size-11" aria-label="Previous month" onclick={() => onchange(shiftMonth(month, -1))}><ChevronLeft /></Button>
  <span class="min-w-36 rounded-md bg-background px-3 py-1 text-center text-sm font-medium shadow-sm dark:bg-input/30" aria-live="polite">{monthLabel(month)}</span>
  <Button variant="ghost" size="icon" class="size-8 phone:size-11" aria-label="Next month" onclick={() => onchange(shiftMonth(month, 1))}><ChevronRight /></Button>
</div>
