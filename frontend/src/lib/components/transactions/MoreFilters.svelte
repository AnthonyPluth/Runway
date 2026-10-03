<script lang="ts">
  import * as Popover from "$lib/components/ui/popover";
  import { Input } from "$lib/components/ui/input";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { KINDS, type Kind } from "$lib/filters.svelte";
  import { cn } from "$lib/utils";
  import SlidersHorizontal from "@lucide/svelte/icons/sliders-horizontal";

  // The filters used less often, behind one button so the row stays one line: the kind (money in, out, transfers) and
  // the amount (either way). The kind applies when picked; an amount when you leave its field or press Enter.
  let { kind, min, max, onchange }: { kind: Kind; min: string; max: string; onchange: (f: { kind?: Kind; min?: string; max?: string }) => void } = $props();

  let open = $state(false);
  const count = $derived((kind ? 1 : 0) + (min || max ? 1 : 0));
  // A number, or nothing (a negative one counts as its size: the amount filter works either way).
  const clean = (s: string) => (s.trim() === "" || !isFinite(Number(s)) ? "" : String(Math.abs(Number(s))));
  function amount(which: "min" | "max", e: Event) {
    const v = clean((e.currentTarget as HTMLInputElement).value);
    if (v !== (which === "min" ? min : max)) onchange({ [which]: v });
  }
  const enter = (e: KeyboardEvent) => { if (e.key === "Enter") (e.currentTarget as HTMLInputElement).blur(); };
</script>

<Popover.Root bind:open>
  <Popover.Trigger class={cn("inline-flex h-10 cursor-pointer items-center justify-center gap-2 rounded-lg px-3 text-sm max-sm:flex-[1_1_40%] outline-none focus-visible:ring-3 focus-visible:ring-ring/50 phone:min-h-11",
    count ? "bg-primary/15 text-primary" : "dark:bg-input")} aria-label={count ? `More filters (${count} on)` : "More filters"}>
    <SlidersHorizontal class="size-4 opacity-70" aria-hidden="true" /><span class="whitespace-nowrap">More{#if count}<span class="ml-1.5 tabular-nums">{count}</span>{/if}</span>
  </Popover.Trigger>
  <Popover.Content class="flex w-[22rem] flex-col gap-4">
    <div class="flex flex-col gap-1.5">
      <span class="text-xs text-muted-foreground">Show</span>
      <Segmented label="Kind of transaction" value={kind || "all"} class="w-full"
        options={KINDS.map((k) => ({ value: k.value || "all", label: k.label }))}
        onchange={(v) => onchange({ kind: (v === "all" ? "" : v) as Kind })} />
    </div>
    <div class="flex flex-col gap-1.5">
      <span class="text-xs text-muted-foreground">Amount, in or out</span>
      <div class="flex items-center gap-2">
        <Input type="number" inputmode="decimal" min="0" step="0.01" placeholder="Min" aria-label="Smallest amount" value={min}
          class="text-right tabular-nums" onchange={(e) => amount("min", e)} onkeydown={enter} />
        <span class="text-muted-foreground" aria-hidden="true">–</span>
        <Input type="number" inputmode="decimal" min="0" step="0.01" placeholder="Max" aria-label="Largest amount" value={max}
          class="text-right tabular-nums" onchange={(e) => amount("max", e)} onkeydown={enter} />
      </div>
    </div>
  </Popover.Content>
</Popover.Root>
