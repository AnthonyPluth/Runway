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

<!-- iOS draws an empty date input with nothing in it, so the hint stands in for it until there's a date (or you're typing one).
     Elsewhere the browser's own "mm/dd/yyyy" is hidden while the hint shows, so the two don't overprint. No max: the dates
     can be ahead of today. The calendar button stays hidden (app.css). -->
{#snippet field(name: string, hint: string, value: string, set: (v: string) => void, invalid = false)}
  <span class="relative flex">
    <Input type="date" {name} {value} oninput={(e) => set(e.currentTarget.value)} aria-invalid={invalid || undefined}
      class={cn("min-w-0 appearance-none border-border text-foreground", !value && "peer not-focus:text-transparent")} />
    {#if !value}
      <span aria-hidden="true" class="pointer-events-none absolute inset-y-0 left-3 flex items-center text-base text-muted-foreground peer-focus:hidden md:text-sm">{hint}</span>
    {/if}
  </span>
{/snippet}

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
        <div class="grid grid-cols-2 gap-3">
          <label class="flex min-w-0 flex-col gap-1 text-xs text-muted-foreground">From{@render field("start", "Start", mine.from, (v) => (mine.from = v))}</label>
          <label class="flex min-w-0 flex-col gap-1 text-xs text-muted-foreground">To{@render field("end", "End", mine.to, (v) => (mine.to = v), bad)}</label>
        </div>
        {#if bad}<p class="text-xs text-destructive" role="alert">The end is before the start.</p>{/if}
        <Button type="submit" size="sm" disabled={bad || (mine.from === from && mine.to === to)}>Apply</Button>
      </form>
    </Popover.Content>
  </Popover.Root>
  {#if set}
    <button type="button" class="mr-1 flex size-7 cursor-pointer items-center justify-center rounded-full hover:bg-primary/20" aria-label="Show all dates"
      title="All dates" onclick={() => onchange("", "")}><X class="size-3.5" /></button>
  {/if}
</span>
