<script lang="ts">
  import { cn } from "$lib/utils";

  // An on/off setting as a row: its label on the left, the switch on the right; the whole row toggles it.
  let { checked, label, title, disabled = false, onchange }: { checked: boolean; label: string; title?: string; disabled?: boolean; onchange: (on: boolean) => void } = $props();
</script>

<button type="button" role="switch" aria-checked={checked} {disabled} {title}
  class="flex min-h-10 w-full cursor-pointer items-center justify-between gap-3 rounded-md text-left text-sm disabled:cursor-not-allowed disabled:opacity-50 phone:min-h-11"
  onclick={() => onchange(!checked)}>
  <span>{label}</span>
  <span aria-hidden="true" class={cn("relative inline-flex h-6 w-10 shrink-0 rounded-full transition-colors motion-reduce:transition-none", checked ? "bg-primary" : "bg-input")}>
    <span class={cn("absolute top-0.5 left-0.5 size-5 rounded-full bg-white shadow transition-transform motion-reduce:transition-none", checked && "translate-x-4")}></span>
  </span>
</button>
