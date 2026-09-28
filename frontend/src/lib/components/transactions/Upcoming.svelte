<script lang="ts" module>
  // "Show all" stays chosen while you move around.
  let showAll = $state(false);
</script>

<script lang="ts">
  import EventsList from "$lib/components/overview/EventsList.svelte";
  import Group from "$lib/components/ui/group/Group.svelte";
  import type { UpcomingEvent } from "./types";

  // Upcoming (projected) items for the forecast account, already filtered like the list below. Click an amount to
  // change just that one occurrence.
  let { events }: { events: UpcomingEvent[] } = $props();
</script>

{#if events.length}
  <Group title="Upcoming · projected" inset="3.75rem" class="mb-6"><EventsList {events} limit={4} accounts bind:all={showAll} /></Group>
{/if}
