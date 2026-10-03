<script lang="ts">
  import * as Popover from "$lib/components/ui/popover";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { presets, rangeLabel } from "$lib/filters.svelte";
  import { cn } from "$lib/utils";
  import Calendar from "@lucide/svelte/icons/calendar";
  import Check from "@lucide/svelte/icons/check";
  import X from "@lucide/svelte/icons/x";

  // The list's dates, as one button in the filter row: a few ranges a tap away, or your own from and to. Set, it reads as
  // a chip with its own ✕. `note` qualifies the range ("Budget accounts", for a list opened from Budget).
  let { from, to, note = "", onchange }: { from: string; to: string; note?: string; onchange: (from: string, to: string) => void } = $props();

  let open = $state(false);
  // svelte-ignore state_referenced_locally
  let mine = $state({ from, to });
  $effect(() => { if (open) mine = { from, to }; });   // each time it opens, from the range in use
  const options = $derived(presets());
  const label = $derived(rangeLabel(from, to));
  const set = $derived(!!(from || to));
  const bad = $derived(!!(mine.from && mine.to && mine.from > mine.to));

  function pick(f: string, t: string) { open = false; onchange(f, t); }
  function apply(e: SubmitEvent) {
    e.preventDefault();
    if (!bad) pick(mine.from, mine.to);
  }
</script>

<span class={cn("inline-flex h-10 min-w-0 items-center rounded-lg text-sm phone:min-h-11 max-sm:flex-[1_1_40%]", set ? "bg-primary/15 text-primary" : "dark:bg-input")}>
  <Popover.Root bind:open>
    <Popover.Trigger class={cn("inline-flex h-full min-w-0 flex-1 cursor-pointer items-center gap-2 rounded-lg pl-3 max-sm:justify-center outline-none focus-visible:ring-3 focus-visible:ring-ring/50", set ? "pr-1.5" : "pr-3")}
      aria-label={`Dates: ${label}${note ? ` · ${note}` : ""}`}>
      <Calendar class="size-4 shrink-0 opacity-70" aria-hidden="true" />
      <span class="truncate whitespace-nowrap">{label}{#if note}<span class="opacity-80"> · {note}</span>{/if}</span>
    </Popover.Trigger>
    <Popover.Content class="w-64">
      <div class="flex flex-col" role="group" aria-label="Date range">
        {#each options as p (p.id)}
          {@const on = p.from === from && p.to === to}
          <button type="button" class="flex min-h-9 cursor-pointer items-center justify-between rounded-md px-2 text-left text-sm hover:bg-muted phone:min-h-11"
            aria-pressed={on} onclick={() => pick(p.from, p.to)}>
            {p.label}{#if on}<Check class="size-4 text-primary" aria-hidden="true" />{/if}
          </button>
        {/each}
      </div>
      <form class="mt-2 flex flex-col gap-2 border-t pt-3" onsubmit={apply}>
        <div class="grid grid-cols-2 gap-2">
          <label class="flex flex-col gap-1 text-xs text-muted-foreground">From<Input type="date" bind:value={mine.from} class="text-foreground" /></label>
          <label class="flex flex-col gap-1 text-xs text-muted-foreground">To<Input type="date" bind:value={mine.to} class="text-foreground" aria-invalid={bad || undefined} /></label>
        </div>
        {#if bad}<p class="text-xs text-destructive" role="alert">The end is before the start.</p>{/if}
        <Button type="submit" size="sm" variant="outline" disabled={bad || (mine.from === from && mine.to === to)}>Apply</Button>
      </form>
    </Popover.Content>
  </Popover.Root>
  {#if set}
    <button type="button" class="mr-1 flex size-7 cursor-pointer items-center justify-center rounded-full hover:bg-primary/20" aria-label="Show all dates"
      title="All dates" onclick={() => onchange("", "")}><X class="size-3.5" /></button>
  {/if}
</span>
