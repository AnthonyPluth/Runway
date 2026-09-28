<script lang="ts" module>
  // "Show all" stays chosen while you move around.
  let showAll = $state(false);
</script>

<script lang="ts">
  import EventsList from "$lib/components/overview/EventsList.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import * as Card from "$lib/components/ui/card";
  import type { UpcomingEvent } from "./types";

  // Upcoming (projected) items for the forecast account, already filtered like the list below. Click an amount to
  // change just that one occurrence.
  let { events }: { events: UpcomingEvent[] } = $props();
</script>

{#if events.length}
  <Card.Root class="mb-6 gap-3 border-dashed">
    <Card.Header><Card.Title class="flex items-center gap-2">Upcoming <Badge variant="secondary">projected</Badge></Card.Title></Card.Header>
    <Card.Content><EventsList {events} limit={4} accounts bind:all={showAll} /></Card.Content>
  </Card.Root>
{/if}
