<script lang="ts">
  import { commas } from "$lib/commas";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { onMount } from "svelte";

  // The amount of a credit to mark used, in a small popover: the rest of this period's (what's left) unless you change it.
  // `onuse` gets the amount as typed, or null for the rest.
  let { name, remaining, onuse }: { name: string; remaining: number | null; onuse: (amount: string | null) => void } = $props();
  const rest = $derived(String(Math.round((remaining ?? 0) * 100) / 100));
  // svelte-ignore state_referenced_locally
  let amount = $state<string | number | null>(rest);   // a number field gives a number, or null once emptied
  let box = $state<HTMLInputElement | null>(null);
  onMount(() => { box?.focus(); box?.select(); });
  function submit(e: SubmitEvent) {
    e.preventDefault();
    const typed = String(amount ?? "").trim();
    onuse(typed === "" || Number(typed) === Number(rest) ? null : typed);
  }
</script>

<form class="flex items-end gap-2" onsubmit={submit}>
  <label class="flex flex-col gap-1 text-xs text-muted-foreground">Amount used
    <Input bind:ref={box} type="number" min="0" step="0.01" class="h-8 w-28 text-foreground" bind:value={amount} {@attach commas} aria-label={`Amount of ${name} used`} />
  </label>
  <Button type="submit" size="sm">Mark used</Button>
</form>
