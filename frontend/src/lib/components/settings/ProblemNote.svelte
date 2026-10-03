<script lang="ts">
  import { cn } from "$lib/utils";
  import type { Snippet } from "svelte";

  // Something that went wrong with a connection, said plainly (`text`), with what the service itself said (`detail`,
  // often a raw error) behind "Details" for anyone who needs to look it up or quote it. `children` adds what to do.
  let { text, detail = "", class: cls = "", children }: { text: string; detail?: string | null; class?: string; children?: Snippet } = $props();
</script>

<div class={cn("flex flex-col gap-1 text-sm", cls)}>
  <p class="text-warning">{text}</p>
  {@render children?.()}
  {#if detail && detail.trim() !== text}
    <details class="text-xs text-muted-foreground">
      <summary class="w-fit cursor-pointer select-none hoverable:hover:text-foreground">Details</summary>
      <p class="mt-1 font-mono break-words whitespace-pre-wrap select-all">{detail}</p>
    </details>
  {/if}
</div>
