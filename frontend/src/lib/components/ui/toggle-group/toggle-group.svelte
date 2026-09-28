<script lang="ts">
  import { cn } from "$lib/utils";
  import { ToggleGroup } from "bits-ui";

  // A segmented control: one of a few options, always one chosen.
  let { value = $bindable(), options, label, class: className, onchange }: {
    value: string; options: { value: string; label: string }[]; label: string; class?: string; onchange?: (v: string) => void;
  } = $props();
</script>

<ToggleGroup.Root type="single" aria-label={label} {value}
  onValueChange={(v) => { if (v) { value = v; onchange?.(v); } }}
  class={cn("bg-muted text-muted-foreground inline-flex h-9 w-fit items-center justify-center rounded-lg p-[3px]", className)}>
  {#each options as o (o.value)}
    <ToggleGroup.Item value={o.value}
      class="data-[state=on]:bg-background dark:data-[state=on]:text-foreground focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:outline-ring dark:data-[state=on]:border-input dark:data-[state=on]:bg-input/30 text-foreground dark:text-muted-foreground inline-flex h-[calc(100%-1px)] flex-1 cursor-pointer items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-transparent px-2 py-1 text-sm font-medium transition-[color,box-shadow] focus-visible:ring-[3px] focus-visible:outline-1 data-[state=on]:shadow-sm">
      {o.label}
    </ToggleGroup.Item>
  {/each}
</ToggleGroup.Root>
