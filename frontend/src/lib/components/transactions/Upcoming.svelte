<script lang="ts" module>
  // "Show all" stays chosen while you move around.
  let showAll = $state(false);
</script>

<script lang="ts">
  import EventsList from "$lib/components/overview/EventsList.svelte";
  import Group from "$lib/components/ui/group/Group.svelte";
  import type { UpcomingEvent } from "./types";

  // Upcoming (projected) items for the forecast account, already filtered like the list below. Click an amount to
  // change just that one occurrence. `oneAccount`: filtered to one account, which the rows then don't repeat.
  // `onchanged`: load the forecast again after an amount changes.
  let { events, oneAccount = false, onchanged }: { events: UpcomingEvent[]; oneAccount?: boolean; onchanged: () => void } = $props();
  // From lg up the block starts collapsed to its first 3, so the list below stays on screen.
  const wide = typeof matchMedia === "function" ? matchMedia("(min-width: 1024px)") : null;
  let limit = $state(wide?.matches ? 3 : 4);
  $effect(() => {
    if (!wide) return;
    const on = () => (limit = wide.matches ? 3 : 4);
    wide.addEventListener("change", on);
    return () => wide.removeEventListener("change", on);
  });
</script>

{#if events.length}
  <Group title="Upcoming · projected" inset="3.75rem" class="mb-6"><EventsList {events} {limit} accounts={!oneAccount} bind:all={showAll} {onchanged} /></Group>
{/if}
