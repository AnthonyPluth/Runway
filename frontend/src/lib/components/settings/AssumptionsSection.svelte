<script lang="ts">
  import { onMount } from "svelte";
  import * as Card from "$lib/components/ui/card";
  import { ASSUMPTION_GROUPS, DISCLAIMER } from "./assumptionGroups";

  // Settings → Assumptions: how Runway works its numbers out, by area, in one place instead of under each page's title.
  // A page links to its group (#setup/assumptions/forecast); the group is read from the address and scrolled to.
  const wanted = () => decodeURIComponent((location.hash.split("?")[0].split("/")[2] ?? "")).trim();
  let current = $state(wanted());

  function show() {
    current = wanted();
    if (!current) return;
    // after the page has been drawn, so the group exists to scroll to
    requestAnimationFrame(() => document.getElementById(`assumptions-${current}`)?.scrollIntoView({ block: "start" }));
  }
  onMount(() => {
    show();
    window.addEventListener("hashchange", show);
    return () => window.removeEventListener("hashchange", show);
  });
</script>

<p class="text-sm text-muted-foreground">{DISCLAIMER}</p>

<nav aria-label="Assumptions by area" class="flex flex-wrap gap-x-4 gap-y-1 text-sm">
  {#each ASSUMPTION_GROUPS as g (g.id)}
    <a href={`#setup/assumptions/${g.id}`} class="font-medium text-foreground underline underline-offset-4">{g.title}</a>
  {/each}
</nav>

{#each ASSUMPTION_GROUPS as g (g.id)}
  <Card.Root id={`assumptions-${g.id}`} class="scroll-mt-4" data-current={g.id === current ? "true" : undefined}>
    <Card.Header><Card.Title><h2>{g.title}</h2></Card.Title></Card.Header>
    <Card.Content>
      <ul class="flex list-disc flex-col gap-2.5 pl-5 text-sm leading-relaxed marker:text-muted-foreground">
        {#each g.items as item (item)}<li>{item}</li>{/each}
      </ul>
    </Card.Content>
  </Card.Root>
{/each}
