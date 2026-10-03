<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";

  // What a report shows before its data arrives (a placeholder block, like Budget's), or when it couldn't load.
  let { error, retry }: { error: Error | null; retry: () => void } = $props();
</script>

{#if error}
  <Card.Root>
    <Card.Content>
      <p class="text-sm">Something went wrong: {error.message}</p>
      <Button class="mt-3" variant="outline" onclick={retry}>Try again</Button>
    </Card.Content>
  </Card.Root>
{:else}
  <div class="h-40 animate-pulse motion-reduce:animate-none rounded-xl bg-muted" role="status"><span class="sr-only">Loading…</span></div>
{/if}
