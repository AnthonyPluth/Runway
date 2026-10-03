<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { cn } from "$lib/utils";

  // What a report shows before its data arrives (a placeholder block, like Budget's), or when it couldn't load: a plain
  // sentence and Retry, with the error's own text kept behind Details. `compact`: inside a card (a merchant opened in
  // the table, a block's transactions), a line with no placeholder of its own.
  let { error, retry, what = "this report", compact = false }: {
    error: Error | null; retry: () => void; what?: string; compact?: boolean;
  } = $props();
</script>

{#if error}
  <div role="alert" class={cn("text-sm", !compact && "rounded-xl border border-[var(--edge)] bg-card p-4")}>
    <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
      <p>Couldn’t load {what}.</p>
      <Button variant={compact ? "link" : "outline"} size={compact ? "sm" : "default"} class={cn(compact && "h-auto p-0 phone:min-h-11")} onclick={retry}>Retry</Button>
    </div>
    {#if error.message}
      <details class="mt-2 text-muted-foreground">
        <summary class="w-fit cursor-pointer text-xs select-none hover:text-foreground phone:py-3">Details</summary>
        <p class="mt-1 text-xs break-words">{error.message}</p>
      </details>
    {/if}
  </div>
{:else if !compact}
  <div class="h-40 animate-pulse motion-reduce:animate-none rounded-xl bg-muted" role="status"><span class="sr-only">Loading…</span></div>
{/if}
