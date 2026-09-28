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
  class={cn("inline-flex rounded-[10px] bg-muted p-[3px]", className)}>
  {#each options as o (o.value)}
    <ToggleGroup.Item value={o.value}
      class="cursor-pointer rounded-[7px] px-3 py-1 text-[13px] text-muted-foreground transition-colors hover:text-foreground data-[state=on]:bg-accent data-[state=on]:font-medium data-[state=on]:text-foreground-strong">
      {o.label}
    </ToggleGroup.Item>
  {/each}
</ToggleGroup.Root>
