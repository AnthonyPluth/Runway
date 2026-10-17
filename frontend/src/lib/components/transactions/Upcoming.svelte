<script lang="ts" module>
  // "Show all" stays chosen while you move around.
  let showAll = $state(false);
</script>

<script lang="ts">
  import EventsList from "$lib/components/overview/EventsList.svelte";
  import Group from "$lib/components/ui/group/Group.svelte";
  import { viewport } from "$lib/phone.svelte";
  import ChevronDown from "@lucide/svelte/icons/chevron-down";
  import type { UpcomingEvent } from "./types";

  // Upcoming (projected) items for the forecast account, already filtered like the list below. Click an amount to
  // change just that one occurrence. `oneAccount`: filtered to one account, which the rows then don't repeat.
  // `onchanged`: load the forecast again after an amount changes.
  let { events, oneAccount = false, onchanged }: { events: UpcomingEvent[]; oneAccount?: boolean; onchanged: () => void } = $props();
  // On a phone the block is one line, "Upcoming · N" with Show/Hide, so the transactions below stay on the first screen.
  // What's chosen stays through reloads (this component stays mounted while `events` changes).
  let open = $state(false);
  const uid = $props.id();
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

{#if events.length && viewport.phone}
  <section class="mb-4" data-upcoming-summary>
    <div class="flex min-h-11 items-center justify-between rounded-xl bg-muted/50 px-4">
      <h2 class="eyebrow">Upcoming · {events.length}</h2>
      <button type="button" class="inline-flex min-h-11 cursor-pointer items-center gap-1 text-sm font-medium text-primary" aria-expanded={open}
        aria-controls={`${uid}-upcoming`} aria-label={`${open ? "Hide" : "Show"} upcoming, ${events.length}`} onclick={() => (open = !open)}>
        {open ? "Hide" : "Show"}<ChevronDown class={`size-4 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden="true" />
      </button>
    </div>
    <div id={`${uid}-upcoming`} hidden={!open} class="mt-2">
      {#if open}<Group title="Upcoming · projected" inset="3.75rem"><EventsList {events} {limit} marked accounts={!oneAccount} bind:all={showAll} {onchanged} /></Group>{/if}
    </div>
  </section>
{:else if events.length}
  <Group title="Upcoming · projected" inset="3.75rem" class="mb-6"><EventsList {events} {limit} marked accounts={!oneAccount} bind:all={showAll} {onchanged} /></Group>
{/if}
