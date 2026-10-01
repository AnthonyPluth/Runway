<script lang="ts">
  import { cn } from "$lib/utils";

  // The stand-in for something that's only done on a computer: "Open Runway on a computer to edit rules." Callers show
  // it in place of the real thing with `{#if isPhone()}`.
  let { what, class: cls }: { what: string; class?: string } = $props();
</script>

<p class={cn("text-sm text-muted-foreground", cls)} data-testid="desktop-only">Open Runway on a computer to {what}.</p>
