<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { cn } from "$lib/utils";

  // A section with nothing in it, as one muted line instead of a card: "Upcoming · nothing in the next six months · Add a to-do".
  let { label, message, action, onaction, id, class: cls }: {
    label: string; message: string; action?: string; onaction?: () => void; id?: string; class?: string;
  } = $props();
</script>

<p {id} class={cn("mb-6 flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground", cls)}>
  <span class="font-medium text-foreground">{label}</span>
  <span aria-hidden="true">·</span>
  <span>{message}</span>
  {#if action && onaction}
    <span aria-hidden="true">·</span>
    <Button variant="link" size="sm" class="h-auto px-0 py-0" onclick={onaction}>{action}</Button>
  {/if}
</p>
