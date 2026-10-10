<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { cn } from "$lib/utils";
  import Sparkles from "@lucide/svelte/icons/sparkles";

  // The look of every action that asks the AI (or works something out for you to apply): an outline button with the
  // sparkles icon and its label. On a phone just the icon, so it fits beside the other controls; the label is still its
  // accessible name (`busyLabel` while `busy`, like "Suggesting…"). Only the look is shared: what the click does,
  // when it's disabled or shown, and the tooltip belong to the caller.
  let { label, busyLabel, busy = false, disabled = false, title, class: className, onclick }: {
    label: string; busyLabel?: string; busy?: boolean; disabled?: boolean; title?: string; class?: string;
    onclick: () => void;
  } = $props();

  const text = $derived(busy && busyLabel ? busyLabel : label);
</script>

<Button variant="outline" size="sm" class={cn("size-10 sm:h-8 sm:w-auto", className)} aria-label={text} aria-busy={busy || undefined}
  {title} disabled={disabled || busy} {onclick}>
  <Sparkles class={cn("size-4", busy && "animate-pulse motion-reduce:animate-none")} aria-hidden="true" /><span class="hidden sm:inline">{text}</span>
</Button>
