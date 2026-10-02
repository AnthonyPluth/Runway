<script lang="ts">
  import { cn } from "$lib/utils";

  // A folded-away list as one muted line: "2 closed · Show". Show opens it, Hide folds it again; the list itself is the caller's.
  let { count, noun, open = $bindable(false), class: cls }: { count: number; noun: string; open?: boolean; class?: string } = $props();
</script>

<p class={cn("text-sm text-muted-foreground", cls)}>
  {count} {noun} ·
  <button type="button" class="cursor-pointer font-medium text-foreground underline underline-offset-2" aria-expanded={open} onclick={() => (open = !open)}>{open ? "Hide" : "Show"}</button>
</p>
