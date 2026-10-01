<script lang="ts">
  import DesktopOnly from "$lib/components/DesktopOnly.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { isPhone } from "$lib/phone.svelte";

  // What a page shows in place of its numbers until a bank is connected, so the page says what to do instead of
  // showing $0 everywhere: a title and a button. `text` is an optional extra sentence. `secondary` is an extra way forward for a page that's useful without a bank (Net worth).
  let { title, text, secondary }: {
    title: string; text?: string; secondary?: { label: string; onclick: () => void };
  } = $props();
</script>

<Card.Root class="mx-auto mt-6 max-w-xl text-center md:mt-10">
  <Card.Header>
    <img src="/logo.svg" alt="" width="40" height="40" class="mx-auto mb-2" />
    <Card.Title class="text-xl">{title}</Card.Title>
    {#if text}<Card.Description>{text}</Card.Description>{/if}
  </Card.Header>
  <Card.Content class="flex flex-col items-center gap-3">
    {#if isPhone()}
      <DesktopOnly what="connect a bank" />
    {:else}
      <Button href="#setup/connections">Connect a bank</Button>
    {/if}
    {#if secondary && !isPhone()}
      <Button variant="link" size="sm" class="h-auto px-0 py-0" onclick={secondary.onclick}>{secondary.label}</Button>
    {/if}
  </Card.Content>
</Card.Root>
