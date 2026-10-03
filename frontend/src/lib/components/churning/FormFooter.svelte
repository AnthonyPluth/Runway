<script lang="ts">
  import { cn } from "$lib/utils";
  import type { Snippet } from "svelte";

  // The buttons at the bottom of a form, and below them what the server refused (also shown in the section it is
  // about). `sticky` keeps the Add button in reach on a phone, above the tab bar, however long the form is.
  let { error = "", sticky = false, children }: { error?: string; sticky?: boolean; children: Snippet } = $props();
</script>

<div class={cn("mt-4", sticky && "phone:sticky phone:bottom-[calc(env(safe-area-inset-bottom)+3.75rem)] phone:z-10 phone:-mx-4 phone:-mb-4 phone:rounded-b-lg phone:border-t phone:bg-popover phone:px-4 phone:py-3")} data-testid="form-footer">
  <div class="flex flex-wrap items-center gap-2">{@render children()}</div>
  {#if error}<p class="mt-2 text-sm text-destructive" role="alert">{error}</p>{/if}
</div>
