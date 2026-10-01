<script lang="ts">
  import Info from "@lucide/svelte/icons/info";

  // The one way a page points at how its numbers are worked out: a quiet ⓘ beside the title, to that area's group on
  // Settings → Assumptions. At most one per page.
  let { group }: { group: string } = $props();
</script>

<a href={`#setup/assumptions/${group}`} aria-label="Assumptions" title="How Runway works this out"
  class="inline-flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
  <Info class="size-[18px]" aria-hidden="true" />
</a>
