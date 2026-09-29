<script lang="ts">
  import { cn } from "$lib/utils";
  import { fmt } from "$lib/format";

  // An amount you can click to change. `save` gets the new (positive) number; the caller decides the sign.
  let { amount, label, title, signed = false, class: className, save }: {
    amount: number; label: string; title: string; signed?: boolean; class?: string; save: (value: number) => Promise<void>;
  } = $props();

  let editing = $state(false);
  // A number input bound to `value` reads as null (not "") once it has been emptied.
  let value = $state<string | number | null>("");
  let done = false;

  function start() { value = Math.abs(amount).toFixed(2); done = false; editing = true; }
  async function finish(keep: boolean) {
    if (done) return;
    done = true;
    if (keep && value != null && value !== "" && Number(value) !== Math.abs(amount)) await save(Math.abs(Number(value)));
    editing = false;
  }
  function focus(el: HTMLInputElement) { el.focus(); el.select(); }
</script>

{#if editing}
  <input type="number" step="0.01" min="0" aria-label={label} bind:value use:focus data-editor
    class="h-8 w-28 rounded-md border border-input bg-transparent px-2 text-right text-sm tabular-nums shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30"
    onkeydown={(e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); }}
    onblur={() => finish(true)} />
{:else}
  <button type="button" {title} onclick={start}
    class={cn("cursor-pointer rounded-md px-1 py-0.5 tabular-nums hover:bg-muted", signed && amount > 0 && "font-semibold text-emerald-500", className)}>
    {signed ? (amount > 0 ? "+" : "−") + fmt(Math.abs(amount)) : fmt(amount)}
  </button>
{/if}
